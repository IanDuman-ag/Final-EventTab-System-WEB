"""Faculty-only OCR screen, draft actions, and private score sheet delivery."""
import json
import uuid
from io import BytesIO
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from events.criteria_faculty_service import criteria_faculty_portal, criteria_rounds, round_is_scoreable
from events.models import Event, OCRScoreSheet
from events.ocr_engine import MAX_BYTES, check_identifiers, extract, load_images, printable
from events.ocr_service import (OCRSheetError, authorize, confirm_sheet, create_sheet, definition,
                                fingerprint, lock_event, locked_sheet, save_draft)


def fic_user(user):
    return user.is_active and (user.is_staff or user.groups.filter(Q(name__iexact='Faculty') | Q(name__iexact='Faculty In-Charge')).exists())


def unique_score_cells(pairs):
    values = {}
    for key, value in pairs:
        if key in values:
            raise OCRSheetError('Duplicate score cells are not allowed.')
        values[key] = value
    return values


def faculty_events(user):
    if not fic_user(user):
        return Event.objects.none()
    return Event.objects.filter(faculty_account=user, scoring_method='criteria').order_by('name')


def payload(sheet):
    return {'id': str(sheet.pk), 'snapshot': sheet.snapshot, 'extracted': sheet.extracted, 'corrected': sheet.corrected,
            'ocr_status': sheet.ocr_status, 'verification_status': sheet.verification_status, 'error': sheet.error,
            'has_original': bool(sheet.original), 'pages': sheet.page_count, 'created_at': sheet.created_at.isoformat(),
            'original': reverse('faculty_ocr_original', args=[sheet.pk]),
            'print': reverse('faculty_ocr_print', args=[sheet.pk])}


@login_required
@require_GET
def ocr_page(request):
    if not fic_user(request.user):
        return HttpResponse('Faculty-in-Charge access required.', status=403)
    return render(request, 'facultydash/ocr_scan.html', {'user_display': request.user.get_full_name() or request.user.username})


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def ocr_api(request):
    if not fic_user(request.user):
        return JsonResponse({'message':'Faculty-in-Charge access required.'},status=403)
    events = faculty_events(request.user)
    try:
        if request.method == 'GET':
            if request.GET.get('sheet'):
                sheet = get_object_or_404(OCRScoreSheet, pk=request.GET['sheet'], event__in=events)
                return JsonResponse(payload(sheet))
            if request.GET.get('event') and request.GET.get('mode') != 'records':
                event = get_object_or_404(events, pk=request.GET['event'])
                rounds = criteria_rounds(event)
                selected = request.GET.get('round') or rounds[0]['id']
                index=next((i for i,row in enumerate(rounds) if row['id']==selected),None)
                if index is None: raise OCRSheetError('Select a round belonging to this event.')
                monitor=criteria_faculty_portal(event,request.user,index)['judges']
                judges=[{'id':row['id'],'name':row['name'],'status':row['status'],'progress':row['progress_percent']} for row in monitor]
                data = {'rounds': [{'id': row['id'], 'name': row['name'], 'writable': round_is_scoreable(event, i)} for i,row in enumerate(rounds)], 'judges': judges}
                if request.GET.get('judge'):
                    data['snapshot'] = definition(event, request.user, selected, request.GET['judge'])
                return JsonResponse(data)
            records = OCRScoreSheet.objects.filter(event__in=events).select_related('event', 'judge')
            totals = {'total': records.exclude(original='').count(), 'pending': records.exclude(original='').filter(ocr_status__in=['pending','processing']).count(),
                      'review': records.filter(verification_status__in=['for_review','verified']).count(),
                      'confirmed': records.filter(verification_status='confirmed').count()}
            for key, field in [('event','event_id'), ('round','round_id'), ('judge','judge_id'), ('ocr','ocr_status'), ('verification','verification_status')]:
                if request.GET.get(key): records = records.filter(**{field: request.GET[key]})
            q = request.GET.get('q', '').strip()
            if q:
                records = records.filter(Q(event__name__icontains=q)|Q(judge__username__icontains=q)|Q(snapshot__round__icontains=q))
            page = Paginator(records, 10).get_page(request.GET.get('page'))
            return JsonResponse({'events': list(events.values('id','name')), 'summary': totals,
                'round_options': list({(row['id'],row['name']): {'id':row['id'],'name':row['name']} for event in events for row in criteria_rounds(event)}.values()),
                'judge_options': [{'id':row['assigned_judges__id'],'name':(' '.join([row['assigned_judges__first_name'] or '',row['assigned_judges__last_name'] or '']).strip() or row['assigned_judges__username'])} for row in events.values('assigned_judges__id','assigned_judges__username','assigned_judges__first_name','assigned_judges__last_name').order_by('assigned_judges__id').distinct() if row['assigned_judges__id']],
                'records': [{'id':str(row.pk), 'judge':row.snapshot['judge'], 'event':row.snapshot['event'], 'round':row.snapshot['round'],
                             'uploaded_at':row.uploaded_at.isoformat() if row.uploaded_at else None, 'ocr':row.ocr_status, 'verification':row.verification_status,
                             'method':'Manual / Paper-Based' if row.ocr_status=='manual' else 'OCR / Paper-Based'} for row in page.object_list], 'page':page.number, 'pages':page.paginator.num_pages})
        action = request.POST.get('action')
        if action == 'create':
            event = get_object_or_404(events, pk=request.POST.get('event'))
            sheet = create_sheet(event.pk, request.user, request.POST.get('round'), request.POST.get('judge'))
            return JsonResponse(payload(sheet), status=201)
        sheet = get_object_or_404(OCRScoreSheet, pk=request.POST.get('sheet'), event__in=events)
        if action in ('save','verify'):
            values = json.loads(request.POST.get('scores', '{}'), object_pairs_hook=unique_score_cells)
            sheet = save_draft(sheet.pk, request.user, values, verify=action=='verify', attested=request.POST.get('reviewed')=='true')
        elif action == 'confirm':
            if request.POST.get('acknowledged') != 'true': raise OCRSheetError('Confirm that you compared every score with the original sheet.')
            sheet = confirm_sheet(sheet.pk, request.user)
        elif action == 'manual':
            with transaction.atomic():
                _,sheet=locked_sheet(sheet.pk,request.user)
                if sheet.ocr_status=='processing': raise OCRSheetError('Wait for OCR to finish before manual entry.')
                sheet.ocr_status='manual';sheet.verification_status='for_review';sheet.save()
        elif action == 'remove':
            with transaction.atomic():
                _,sheet=locked_sheet(sheet.pk,request.user)
                if sheet.ocr_status=='processing': raise OCRSheetError('Wait for OCR processing before removing the file.')
                sheet.corrections=[*sheet.corrections,{'action':'remove_original','by':request.user.pk,'at':timezone.now().isoformat(),'original':sheet.original.name,'before':sheet.corrected}]
                sheet.original='';sheet.content_type='';sheet.uploaded_at=None;sheet.extracted={};sheet.corrected={}
                sheet.ocr_status='pending';sheet.verification_status='pending';sheet.error='';sheet.save()
        elif action == 'upload':
            upload = request.FILES.get('file')
            if not upload or upload.size > MAX_BYTES: raise OCRSheetError('Select a JPG, PNG, or PDF under 10 MB.')
            extension = Path(upload.name).suffix.lower()
            if extension not in ('.jpg','.jpeg','.png','.pdf'): raise OCRSheetError('Use JPG, PNG, or PDF files.')
            raw = upload.read(MAX_BYTES+1); _, mime = load_images(raw, verify_only=True)
            if mime != {'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.pdf':'application/pdf'}[extension]:
                raise OCRSheetError('The filename does not match the actual file content.')
            with transaction.atomic():
                _, sheet = locked_sheet(sheet.pk, request.user)
                if sheet.ocr_status == 'processing': raise OCRSheetError('Wait for OCR processing before replacing the file.')
                check_identifiers(sheet, raw)
                if sheet.original:
                    sheet.corrections = [*sheet.corrections, {'action':'replace_original','by':request.user.pk,'at':timezone.now().isoformat(),'original':sheet.original.name,'before':sheet.corrected}]
                sheet.original.save(uuid.uuid4().hex+extension, ContentFile(raw), save=False)
                if mime == 'application/pdf':
                    import pypdfium2 as pdfium
                    document = pdfium.PdfDocument(raw)
                    try: sheet.page_count = len(document)
                    finally: document.close()
                else: sheet.page_count = 1
                sheet.content_type = mime; sheet.ocr_status = 'pending'; sheet.verification_status = 'pending'
                sheet.uploaded_at=timezone.now();sheet.uploaded_by=request.user
                sheet.extracted = {}; sheet.corrected = {}; sheet.error = ''; sheet.save()
        elif action == 'process':
            with transaction.atomic():
                _, sheet = locked_sheet(sheet.pk, request.user)
                if not sheet.original: raise OCRSheetError('Upload the score sheet first.')
                if sheet.ocr_status == 'processing' and (timezone.now()-sheet.updated_at).total_seconds()<180:
                    raise OCRSheetError('OCR is already processing this sheet.')
                sheet.corrections = [*sheet.corrections, {'action':'ocr_retry', 'by':request.user.pk, 'at':timezone.now().isoformat(), 'before':sheet.corrected}]
                sheet.corrected={}; sheet.ocr_status = 'processing'; sheet.verification_status='pending'; sheet.save()
                processing_revision = sheet.updated_at
            try:
                values = extract(sheet)
                status, error = 'completed', ''
            except Exception as exc:
                values, status = {}, 'failed'
                error = str(exc) if isinstance(exc, OCRSheetError) else 'Local OCR could not read this sheet. Check Tesseract installation or use Manual Entry.'
            with transaction.atomic():
                event = lock_event(sheet.event_id)
                authorize(event, request.user)
                sheet = OCRScoreSheet.objects.select_for_update().get(pk=sheet.pk)
                if sheet.ocr_status != 'processing' or sheet.updated_at != processing_revision:
                    raise OCRSheetError('This OCR attempt was replaced. Refresh the score sheet before continuing.')
                try:
                    current = definition(event, request.user, sheet.round_id, sheet.judge_id, writable=True)
                    if fingerprint(current) != fingerprint(sheet.snapshot):
                        raise OCRSheetError('The score configuration changed. Generate a new sheet.')
                except OCRSheetError as exc:
                    values, status, error = {}, 'failed', str(exc)
                sheet.extracted=values; sheet.ocr_status=status; sheet.error=error[:250]
                sheet.verification_status='for_review'; sheet.save()
        else:
            raise OCRSheetError('Unknown score sheet action.')
        return JsonResponse(payload(sheet))
    except (OCRSheetError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({'message':str(exc)}, status=400)
    except IntegrityError:
        return JsonResponse({'message':'A score submission already exists. Refresh before continuing.'}, status=409)
    except ValidationError:
        return JsonResponse({'message':'Select a valid event, judge, round, and score sheet.'},status=400)


@login_required
@never_cache
@require_GET
def original_sheet(request, sheet_id):
    sheet = get_object_or_404(OCRScoreSheet, pk=sheet_id, event__in=faculty_events(request.user))
    if not sheet.original: return HttpResponse('No scanned original has been uploaded.', status=404)
    if request.GET.get('preview'):
        with sheet.original.open('rb') as source: images,_=load_images(source.read(MAX_BYTES+1))
        try:
            page = int(request.GET.get('page','0'))
            if page < 0: return HttpResponse(status=404)
            image = images[page]
        except (ValueError,IndexError): return HttpResponse(status=404)
        out=BytesIO(); image.save(out,format='PNG')
        response=HttpResponse(out.getvalue(),content_type='image/png')
    else:
        response=FileResponse(sheet.original.open('rb'),as_attachment=True,filename=f'{sheet.pk}{Path(sheet.original.name).suffix}',content_type=sheet.content_type)
    response['X-Content-Type-Options']='nosniff'
    return response


@login_required
@never_cache
@require_GET
def print_sheet(request,sheet_id):
    sheet=get_object_or_404(OCRScoreSheet,pk=sheet_id,event__in=faculty_events(request.user))
    response=HttpResponse(printable(sheet),content_type='application/pdf')
    response['Content-Disposition']=f'inline; filename="EventTab-sheet-{sheet.pk}.pdf"'
    return response
