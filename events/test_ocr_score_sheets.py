"""Focused coverage of private OCR drafts and existing round integration."""
import io
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .models import (CriteriaEventEntry, CriteriaScoreSubmission, Event,
                     EventScoringCategory, EventScoringCriterion, OCRScoreSheet)
from .criteria_faculty_service import criteria_faculty_portal, approve_criteria_round, round_is_scoreable
from .ocr_service import OCRSheetError, create_sheet, save_draft, confirm_sheet
from .ocr_engine import extract, load_images, printable


class OCRScoreSheetTests(TestCase):
    def setUp(self):
        self.private=tempfile.TemporaryDirectory()
        self.storage_override=override_settings(OCR_PRIVATE_ROOT=Path(self.private.name));self.storage_override.enable()
        self.addCleanup(self.storage_override.disable);self.addCleanup(self.private.cleanup)
        User=get_user_model()
        self.faculty=User.objects.create_user('ocr-faculty');self.faculty.groups.add(Group.objects.create(name='Faculty'))
        self.other=User.objects.create_user('other-faculty');self.other.groups.add(Group.objects.get(name='Faculty'))
        self.judge=User.objects.create_user('paper-judge');self.judge2=User.objects.create_user('mobile-judge')
        self.event=Event.objects.create(name='Vocal Duet',event_date=date.today(),scoring_method='criteria',faculty_account=self.faculty,
            rounds_config=[{'id':'prelim','name':'Preliminary','status':'open','qualifiers':1},{'id':'final','name':'Final','status':'locked'}])
        self.event.assigned_judges.add(self.judge,self.judge2)
        self.entries=[CriteriaEventEntry.objects.create(event=self.event,entry_type='team',display_name=f'Team {n}',display_order=n) for n in range(2)]
        self.event.participant_ids=[row.pk for row in self.entries];self.event.save()
        category=EventScoringCategory.objects.create(event=self.event,assigned_round_id='prelim',name='Performance',purpose='official',judge_mode='scoring',overall_weight_percent=100,display_order=0)
        self.criterion=EventScoringCriterion.objects.create(category=category,name='Skill',weight_percent=100,min_score=0,max_score=100,display_order=0)
        self.client.force_login(self.faculty)

    def sheet(self,judge=None):
        sheet=create_sheet(self.event.pk,self.faculty,'prelim',(judge or self.judge).pk)
        out=io.BytesIO();Image.new('RGB',(800,600),'white').save(out,format='PNG')
        sheet.original.save('original.png',ContentFile(out.getvalue()))
        sheet.content_type='image/png';sheet.save()
        return sheet

    def values(self,sheet):
        return {f'{entry["id"]}/{col["key"]}':str(90-i*10) for i,entry in enumerate(sheet.snapshot['participants']) for col in sheet.snapshot['columns']}

    def test_only_assigned_criteria_events_and_judges_are_selectable(self):
        Event.objects.create(name='Other criteria',event_date=date.today(),scoring_method='criteria',faculty_account=self.other)
        Event.objects.create(name='Sports',event_date=date.today(),scoring_method='match',faculty_account=self.faculty)
        response=self.client.get(reverse('faculty_ocr_api'))
        self.assertEqual([row['id'] for row in response.json()['events']],[self.event.pk])
        with self.assertRaises(OCRSheetError):create_sheet(self.event.pk,self.other,'prelim',self.judge.pk)
        with self.assertRaises(OCRSheetError):create_sheet(self.event.pk,self.faculty,'prelim',self.other.pk)
        with self.assertRaises(OCRSheetError):create_sheet(self.event.pk,self.faculty,'final',self.judge.pk)

    def test_private_files_invalid_uploads_and_ocr_failure(self):
        sheet=self.sheet()
        endpoint=reverse('faculty_ocr_api')
        response=self.client.post(endpoint,{'action':'upload','sheet':sheet.pk,'file':SimpleUploadedFile('fake.png',b'not an image')})
        self.assertEqual(response.status_code,400)
        with patch('core.ocr_views.extract',side_effect=RuntimeError('missing local engine')):
            response=self.client.post(endpoint,{'action':'process','sheet':sheet.pk})
        self.assertEqual(response.json()['ocr_status'],'failed')
        self.assertFalse(CriteriaScoreSubmission.objects.exists())
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('faculty_ocr_original',args=[sheet.pk])).status_code,404)
        self.assertEqual(self.client.post(endpoint,{'action':'confirm','sheet':sheet.pk,'acknowledged':'true'}).status_code,404)
        with self.assertRaises(ValueError):_ = sheet.original.url

    def test_duplicate_json_cells_and_invalid_preview_pages_are_rejected(self):
        sheet=self.sheet()
        key=next(iter(self.values(sheet)))
        response=self.client.post(reverse('faculty_ocr_api'),{'action':'save','sheet':sheet.pk,
            'scores':'{"'+key+'":"80","'+key+'":"90"}'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.client.get(reverse('faculty_ocr_original',args=[sheet.pk]),{'preview':'1','page':'-1'}).status_code,404)
        self.assertFalse(CriteriaScoreSubmission.objects.exists())

    def test_pdf_upload_preview_and_manual_confirmation(self):
        sheet=self.sheet()
        endpoint=reverse('faculty_ocr_api')
        response=self.client.post(endpoint,{'action':'upload','sheet':sheet.pk,
            'file':SimpleUploadedFile('scores.pdf',printable(sheet),content_type='application/pdf')})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['pages'],1)
        preview=self.client.get(reverse('faculty_ocr_original',args=[sheet.pk]),{'preview':'1','page':'0'})
        self.assertEqual(preview['Content-Type'],'image/png')
        self.assertEqual(self.client.post(endpoint,{'action':'manual','sheet':sheet.pk}).json()['ocr_status'],'manual')
        save_draft(sheet.pk,self.faculty,self.values(sheet),verify=True,attested=True)
        confirm_sheet(sheet.pk,self.faculty)
        self.assertEqual(CriteriaScoreSubmission.objects.filter(source='OCR').count(),2)

    def test_configuration_change_during_processing_leaves_a_failed_draft(self):
        sheet=self.sheet()
        def change_configuration(_sheet):
            self.criterion.max_score=95;self.criterion.save()
            return {}
        with patch('core.ocr_views.extract',side_effect=change_configuration):
            response=self.client.post(reverse('faculty_ocr_api'),{'action':'process','sheet':sheet.pk})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['ocr_status'],'failed')
        self.assertFalse(CriteriaScoreSubmission.objects.exists())

    def test_draft_correction_requires_attestation_and_valid_complete_scores(self):
        sheet=self.sheet();values=self.values(sheet)
        save_draft(sheet.pk,self.faculty,values)
        invalid={**values,next(iter(values)):'101'}
        with self.assertRaises(OCRSheetError):save_draft(sheet.pk,self.faculty,invalid,verify=True,attested=True)
        with self.assertRaises(OCRSheetError):save_draft(sheet.pk,self.faculty,{},verify=True,attested=True)
        with self.assertRaises(OCRSheetError):save_draft(sheet.pk,self.faculty,values,verify=True)
        verified=save_draft(sheet.pk,self.faculty,values,verify=True,attested=True)
        self.assertEqual(verified.verification_status,'verified')
        self.assertEqual(len(verified.corrections),2)
        self.assertFalse(CriteriaScoreSubmission.objects.exists())

    def test_confirmation_integrates_judges_and_waits_for_every_submission(self):
        sheet=self.sheet();save_draft(sheet.pk,self.faculty,self.values(sheet),verify=True,attested=True)
        confirm_sheet(sheet.pk,self.faculty)
        self.assertEqual(CriteriaScoreSubmission.objects.filter(source='OCR',status='SUBMITTED').count(),2)
        portal=criteria_faculty_portal(self.event,self.faculty)
        self.assertEqual(portal['summary']['submitted_judges'],1)
        self.assertFalse(portal['selected_round']['can_approve'])
        self.assertEqual(portal['results'],[])
        self.assertEqual(portal['judges'][0]['submission_method'],'OCR / Paper-Based')
        self.assertFalse(round_is_scoreable(self.event,1))
        with self.assertRaises(OCRSheetError):confirm_sheet(sheet.pk,self.faculty)
        with self.assertRaises(OCRSheetError):save_draft(sheet.pk,self.faculty,self.values(sheet))
        for i,entry in enumerate(self.entries):
            CriteriaScoreSubmission.objects.create(event=self.event,assigned_round_id='prelim',category=self.criterion.category,criterion=self.criterion,judge=self.judge2,participant_type='entry',participant_id=entry.pk,source='MOBILE',status='SUBMITTED',raw_score=90-i*10)
        portal=criteria_faculty_portal(self.event,self.faculty)
        self.assertEqual(portal['summary']['submitted_judges'],2)
        self.assertTrue(portal['selected_round']['can_approve'])
        self.assertEqual(len(portal['results']),2)
        approve_criteria_round(self.event,self.faculty,0)
        self.assertFalse(round_is_scoreable(self.event,1))
        self.event.refresh_from_db();self.assertFalse(self.event.results_finalized)

    def test_mobile_duplicate_and_round_lock_are_rechecked_at_confirmation(self):
        sheet=self.sheet();save_draft(sheet.pk,self.faculty,self.values(sheet),verify=True,attested=True)
        CriteriaScoreSubmission.objects.create(event=self.event,assigned_round_id='prelim',category=self.criterion.category,criterion=self.criterion,judge=self.judge,participant_type='entry',participant_id=self.entries[0].pk,source='MOBILE',status='SUBMITTED',raw_score=70)
        with self.assertRaises(OCRSheetError):confirm_sheet(sheet.pk,self.faculty)
        self.assertFalse(CriteriaScoreSubmission.objects.filter(source='OCR').exists())
        sheet2=self.sheet(self.judge2);save_draft(sheet2.pk,self.faculty,self.values(sheet2),verify=True,attested=True)
        self.event.rounds_config[0]['status']='locked';self.event.save()
        with self.assertRaises(OCRSheetError):confirm_sheet(sheet2.pk,self.faculty)
        with self.assertRaises(OCRSheetError):save_draft(sheet2.pk,self.faculty,self.values(sheet2))

    def test_confirmation_is_atomic_when_audit_fails(self):
        sheet=self.sheet();save_draft(sheet.pk,self.faculty,self.values(sheet),verify=True,attested=True)
        with patch('events.ocr_service.record_audit',side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):confirm_sheet(sheet.pk,self.faculty)
        self.assertFalse(CriteriaScoreSubmission.objects.exists())
        sheet.refresh_from_db();self.assertEqual(sheet.verification_status,'verified')

    def test_pdf_template_contains_identified_cells_and_renders_locally(self):
        sheet=self.sheet();raw=printable(sheet)
        self.assertTrue(raw.startswith(b'%PDF'))
        images,mime=load_images(raw)
        self.assertEqual(mime,'application/pdf');self.assertEqual(len(images),1)
        self.assertGreater(images[0].width,1000)

    def test_generated_scan_uses_local_ocr_without_official_writes(self):
        import cv2
        import numpy as np
        sheet=self.sheet();images,_=load_images(printable(sheet))
        image=images[0]
        from PIL import ImageDraw,ImageFont
        from .ocr_engine import pages
        draw=ImageDraw.Draw(image)
        font_path=Path('C:/Windows/Fonts/arial.ttf')
        if not font_path.exists():font_path=Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
        font=ImageFont.truetype(str(font_path),26) if font_path.exists() else ImageFont.load_default(size=26)
        for cell in pages(sheet.snapshot)[0]['cells']:
            x,y,w,h=cell['box'];draw.text(((x+20)*2,(y+3)*2),'85',font=font,fill='black')
        out=io.BytesIO();image.save(out,format='PNG');sheet.original.save('scan.png',ContentFile(out.getvalue()))
        result=extract(sheet)
        self.assertEqual(len(result),2)
        self.assertTrue(all(row['value']=='85' for row in result.values()),result)
        self.assertFalse(CriteriaScoreSubmission.objects.exists())

    def test_ocr_locks_out_a_later_flutter_submission(self):
        from datetime import time
        from .models import EventCategory, JudgingEvent, JudgeScore
        from .criteria_mobile_rounds import mobile_round_context
        mobile=JudgingEvent.objects.create(title=self.event.name,category=EventCategory.objects.create(name='Arts',category_type='academic'),date=date.today(),time=time(9),venue='Hall',status='active')
        self.event.judging_event=mobile;self.event.save()
        self.judge.groups.add(Group.objects.create(name='Judge'));mobile.assigned_judges.add(self.judge)
        scoped=mobile_round_context(mobile)
        sheet=self.sheet();save_draft(sheet.pk,self.faculty,self.values(sheet),verify=True,attested=True);confirm_sheet(sheet.pk,self.faculty)
        self.client.force_login(self.judge)
        response=self.client.post(reverse('judgingevent-submit-scores',args=[mobile.pk]),
            {'candidate_id':scoped['candidates'][0].pk,'scores':[{'criterion_id':scoped['criteria'][0].pk,'score':85}]},content_type='application/json')
        self.assertEqual(response.status_code,400,response.content)
        self.assertFalse(JudgeScore.objects.exists())

    def test_wrong_printed_identifier_and_changed_configuration_are_rejected(self):
        first=self.sheet();second=self.sheet(self.judge2)
        response=self.client.post(reverse('faculty_ocr_api'),{'action':'upload','sheet':first.pk,
            'file':SimpleUploadedFile('wrong.pdf',printable(second),content_type='application/pdf')})
        self.assertEqual(response.status_code,400)
        save_draft(first.pk,self.faculty,self.values(first),verify=True,attested=True)
        self.criterion.weight_percent=99;self.criterion.save()
        with self.assertRaises(OCRSheetError):confirm_sheet(first.pk,self.faculty)
        self.assertFalse(CriteriaScoreSubmission.objects.exists())

    def test_subcriteria_and_tabulator_verification_remain_on_existing_path(self):
        from .models import EventScoringSubcriterion,CriteriaSubcriterionScore
        EventScoringSubcriterion.objects.create(criterion=self.criterion,name='Technique',max_score=60,display_order=1)
        EventScoringSubcriterion.objects.create(criterion=self.criterion,name='Delivery',max_score=40,display_order=2)
        self.event.result_processing_config={'verification_mode':'tabulator_verification'};self.event.save()
        for judge in [self.judge,self.judge2]:
            sheet=self.sheet(judge)
            values={f'{entry["id"]}/{col["key"]}':str(Decimal(col['maximum'])-10) for entry in sheet.snapshot['participants'] for col in sheet.snapshot['columns']}
            save_draft(sheet.pk,self.faculty,values,verify=True,attested=True);confirm_sheet(sheet.pk,self.faculty)
        self.assertEqual(CriteriaSubcriterionScore.objects.count(),8)
        self.assertFalse(criteria_faculty_portal(self.event,self.faculty)['selected_round']['can_approve'])
        self.assertFalse(CriteriaScoreSubmission.objects.filter(status='VERIFIED').exists())


from django.test import TransactionTestCase


class OCRConcurrencyTests(TransactionTestCase):
    setUp=OCRScoreSheetTests.setUp
    sheet=OCRScoreSheetTests.sheet
    values=OCRScoreSheetTests.values

    def test_concurrent_paper_confirmations_create_only_one_official_sheet(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import close_old_connections
        sheets=[self.sheet(),self.sheet()]
        for sheet in sheets:save_draft(sheet.pk,self.faculty,self.values(sheet),verify=True,attested=True)
        def submit(sheet):
            close_old_connections()
            try:
                confirm_sheet(sheet.pk,self.faculty)
                return 'confirmed'
            except OCRSheetError:
                return 'blocked'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes=list(executor.map(submit,sheets))
        self.assertEqual(sorted(outcomes),['blocked','confirmed'])
        self.assertEqual(CriteriaScoreSubmission.objects.count(),2)
        self.assertEqual(OCRScoreSheet.objects.filter(verification_status='confirmed').count(),1)
