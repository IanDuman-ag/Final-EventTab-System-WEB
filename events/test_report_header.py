import io
import tempfile
from pathlib import Path
from PIL import Image
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import SystemSettings
from .report_header_forms import ReportHeaderForm
from .report_header import pdf_report_header


def logo(name='logo.png', fmt='PNG'):
    output = io.BytesIO()
    Image.new('RGB', (80, 40), 'blue').save(output, format=fmt)
    return SimpleUploadedFile(name, output.getvalue(), content_type='image/' + fmt.lower())


class ReportHeaderTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name, USE_CLOUDINARY=False)
        self.override.enable()
        self.addCleanup(self.override.disable)
        users = get_user_model()
        self.admin = users.objects.create_user(username='branding-admin')
        self.admin.groups.add(Group.objects.create(name='Admin'))
        self.tabulator = users.objects.create_user(username='branding-tab', is_staff=True)
        self.tabulator.groups.add(Group.objects.create(name='Tabulator'))
        self.url = reverse('admin_report_header')

    def post(self, data):
        data = {'extra_logos-TOTAL_FORMS': '0', 'extra_logos-INITIAL_FORMS': '0', **data}
        return self.client.post(self.url, data)

    def test_authorization_and_csrf(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.tabulator)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.post( {'school_name': 'Bad'}).status_code, 403)
        self.assertFalse(SystemSettings.objects.exists())
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(self.url, {'school_name': 'School'}).status_code, 403)

    def test_save_replace_remove_singleton_and_preserve_other_settings(self):
        self.client.force_login(self.admin)
        config = SystemSettings.load()
        config.championship_points_config = {'unchanged': True}
        config.save()
        response = self.post( {'school_name': 'Official School', 'campus_name': 'Main Campus', 'report_subtitle': 'Annual Games', 'school_logo': logo(), 'report_right_logo': logo('right.webp', 'WEBP')})
        self.assertEqual(response.status_code, 302)
        config.refresh_from_db()
        first = config.school_logo.name
        self.assertTrue(Path(config.school_logo.path).exists())
        self.assertEqual(config.campus_name, 'Main Campus')
        self.assertEqual(config.updated_by, self.admin)
        self.post( {'school_name': 'Official School', 'school_logo': logo('replacement.jpg', 'JPEG')})
        config.refresh_from_db()
        self.assertNotEqual(first, config.school_logo.name)
        self.assertTrue(config.report_right_logo)
        self.post( {'school_name': 'Official School', 'remove_left_logo': 'on', 'remove_right_logo': 'on'})
        config.refresh_from_db()
        self.assertFalse(config.school_logo)
        self.assertFalse(config.report_right_logo)
        self.assertEqual(SystemSettings.objects.count(), 1)
        self.assertEqual(config.championship_points_config, {'unchanged': True})

    def test_reject_invalid_and_oversized_uploads(self):
        invalid = [SimpleUploadedFile('bad.png', b'not an image'), logo('bad.gif', 'GIF'), logo('mismatch.jpg'), SimpleUploadedFile('huge.png', b'x' * (5 * 1024 * 1024 + 1))]
        for upload in invalid:
            form = ReportHeaderForm({'school_name': 'School'}, {'school_logo': upload}, instance=SystemSettings())
            self.assertFalse(form.is_valid())
            self.assertIn('school_logo', form.errors)

    def test_header_and_pdf_use_saved_branding(self):
        from django.template.loader import render_to_string
        from .report_header import report_branding
        from reportlab.platypus import SimpleDocTemplate
        config = SystemSettings.load()
        config.school_name = 'Official <School>'
        config.campus_name = 'Main Campus'
        config.school_logo = logo()
        config.report_right_logo = logo('right.webp', 'WEBP')
        config.save()
        html = render_to_string('reports/_header.html', {'branding': report_branding(), 'report_title': 'Detailed Score Breakdown'})
        self.assertIn('Official &lt;School&gt;', html)
        self.assertIn(config.report_right_logo.url, html)
        self.assertIn('Detailed Score Breakdown', html)
        output = io.BytesIO()
        SimpleDocTemplate(output).build([pdf_report_header('Final Results', 450)])
        self.assertTrue(output.getvalue().startswith(b'%PDF'))

    def test_allowed_formats_and_missing_logo_pdf_fallback(self):
        for name, fmt in [('logo.png', 'PNG'), ('logo.jpg', 'JPEG'), ('logo.jpeg', 'JPEG'), ('logo.webp', 'WEBP')]:
            form = ReportHeaderForm({'school_name': 'School'}, {'school_logo': logo(name, fmt)}, instance=SystemSettings())
            self.assertTrue(form.is_valid(), form.errors)
        config = SystemSettings.load()
        config.school_logo = 'system/missing-logo.png'
        config.save()
        from reportlab.platypus import SimpleDocTemplate
        with self.assertLogs('events.report_header', level='WARNING'):
            output = io.BytesIO()
            SimpleDocTemplate(output).build([pdf_report_header('Final Results', 450)])
        self.assertTrue(output.getvalue().startswith(b'%PDF'))

    def test_failed_save_keeps_persisted_branding(self):
        self.client.force_login(self.admin)
        config = SystemSettings.load()
        config.school_name = 'Saved School'
        config.save()
        response = self.post( {'school_name': 'Unsaved School', 'school_logo': SimpleUploadedFile('bad.png', b'invalid')})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Changes could not be saved')
        config.refresh_from_db()
        self.assertEqual(config.school_name, 'Saved School')
        self.assertEqual(response.context['branding']['school_name'], 'Saved School')

    def test_tabulator_print_uses_saved_header_without_edit_controls(self):
        from datetime import date
        from .models import Event
        config = SystemSettings.load()
        config.school_name = 'Official School'
        config.campus_name = 'Main Campus'
        config.report_subtitle = 'Annual Games'
        config.school_logo = logo()
        config.report_right_logo = logo('right.webp', 'WEBP')
        config.save()
        event = Event.objects.create(name='Print Event', category='Sports', scoring_method='match', event_date=date.today(), venue='Hall')
        event.assigned_tabulators.add(self.tabulator)
        self.client.force_login(self.tabulator)
        response = self.client.get(reverse('tabulator_results'), {'event_id': event.pk, 'print': '1', 'tab': 'breakdown'})
        self.assertEqual(response.status_code, 200)
        for value in ('Official School', 'Main Campus', 'Annual Games', config.school_logo.url, config.report_right_logo.url, 'Detailed Score Breakdown'):
            self.assertContains(response, value)
        self.assertNotContains(response, 'branding-form')
        self.assertNotContains(response, 'type="file"')
        self.assertNotContains(response, 'View details')

    def test_admin_page_renders(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Report Header Settings')
        self.assertContains(response, 'multipart/form-data')

    def test_add_replace_remove_extra_logos_and_print(self):
        from .models import ReportHeaderLogo
        self.client.force_login(self.admin)
        response = self.post({'school_name': 'School', 'extra_logos-TOTAL_FORMS': '2', 'extra_logos-0-side': 'left', 'extra_logos-0-image': logo('extra-left.png'), 'extra_logos-1-side': 'right', 'extra_logos-1-image': logo('extra-right.webp', 'WEBP')})
        self.assertEqual(response.status_code, 302)
        left, right = list(ReportHeaderLogo.objects.order_by('pk'))
        from .report_header import report_branding
        branding = report_branding()
        self.assertEqual(branding['extra_left_logos'][0]['url'], left.image.url)
        self.assertEqual(branding['extra_right_logos'][0]['url'], right.image.url)
        self.assertContains(self.client.get(self.url), left.image.url)
        before = left.image.name
        data = {'school_name': 'School', 'extra_logos-TOTAL_FORMS': '2', 'extra_logos-INITIAL_FORMS': '2', 'extra_logos-0-id': str(left.pk), 'extra_logos-0-side': 'left', 'extra_logos-0-image': logo('replaced.jpeg', 'JPEG'), 'extra_logos-1-id': str(right.pk), 'extra_logos-1-side': 'right'}
        self.assertEqual(self.post(data).status_code, 302)
        left.refresh_from_db()
        self.assertNotEqual(before, left.image.name)
        from reportlab.platypus import SimpleDocTemplate
        output=io.BytesIO()
        SimpleDocTemplate(output).build([pdf_report_header('Final Results', 450)])
        self.assertTrue(output.getvalue().startswith(b'%PDF'))
        data.pop('extra_logos-0-image')
        data['extra_logos-0-DELETE'] = 'on'
        self.assertEqual(self.post(data).status_code, 302)
        self.assertFalse(ReportHeaderLogo.objects.filter(pk=left.pk).exists())
        self.assertTrue(ReportHeaderLogo.objects.filter(pk=right.pk).exists())

    def test_extra_validation_is_atomic_and_limits_each_side(self):
        from .models import ReportHeaderLogo
        self.client.force_login(self.admin)
        config = SystemSettings.load()
        config.school_name = 'Saved'
        config.save()
        data = {'school_name': 'Unsaved', 'extra_logos-TOTAL_FORMS': '1', 'extra_logos-0-side': 'left', 'extra_logos-0-image': SimpleUploadedFile('bad.png', b'bad')}
        self.assertEqual(self.post(data).status_code, 200)
        config.refresh_from_db()
        self.assertEqual(config.school_name, 'Saved')
        data={'school_name':'Unsaved','extra_logos-TOTAL_FORMS':'6'}
        for index in range(6):
            data[f'extra_logos-{index}-side']='left'
            data[f'extra_logos-{index}-image']=logo(f'logo-{index}.png')
        response=self.post(data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'five extra logos')
        self.assertFalse(ReportHeaderLogo.objects.exists())
