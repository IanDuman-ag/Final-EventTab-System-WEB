"""Administrator-only management of the shared report header."""
import logging
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from events.models import SystemSettings
from events.report_header import report_branding
from events.report_header_forms import ReportHeaderForm, ReportHeaderLogoFormSet
from .admin_settings_views import can_manage_scoring

logger = logging.getLogger(__name__)


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def report_header_settings(request):
    if not request.user.is_active or not can_manage_scoring(request.user):
        return HttpResponseForbidden('Only authorized administrators may change report branding.')
    config = SystemSettings.load()
    form = ReportHeaderForm(instance=config)
    extra_formset = ReportHeaderLogoFormSet(instance=config, prefix='extra_logos')
    if request.method == 'POST':
        try:
            with transaction.atomic():
                config = SystemSettings.objects.select_for_update().get(pk=1)
                form = ReportHeaderForm(request.POST, request.FILES, instance=config)
                extra_formset = ReportHeaderLogoFormSet(request.POST, request.FILES, instance=config, prefix='extra_logos')
                form_valid = form.is_valid()
                extra_valid = extra_formset.is_valid()
                if form_valid and extra_valid:
                    form.instance.updated_by = request.user
                    form.save()
                    extra_formset.save()
                    messages.success(request, 'Report header settings saved successfully.')
                    return redirect('admin_report_header')
        except Exception:  # Storage providers may raise backend-specific errors.
            logger.exception('Unable to save report header settings')
            messages.error(request, 'Unable to save the report header. Please try again.')
    # Use persisted logo paths for preview, not uncommitted uploads on bound forms.
    saved = SystemSettings.objects.get(pk=1)
    return render(request, 'admindash/reportheader.html', {'form': form, 'extra_formset': extra_formset, 'branding': report_branding(saved), 'report_title': 'Detailed Score Breakdown'})
