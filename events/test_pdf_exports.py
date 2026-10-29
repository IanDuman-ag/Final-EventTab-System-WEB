"""Exercise actual export/render paths across supported ReportLab versions."""
import base64
import io
import re
import tempfile
from datetime import date

from django.test import TestCase, override_settings
from PIL import Image

from .models import Event


class PdfExportCompatibilityTests(TestCase):
    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name, USE_CLOUDINARY=False)
        override.enable()
        self.addCleanup(override.disable)

    def assert_pdf(self, value, minimum_pages=1):
        self.assertTrue(value.startswith(b'%PDF-'))
        self.assertTrue(value.rstrip().endswith(b'%%EOF'))
        self.assertGreaterEqual(len(re.findall(rb'/Type\s*/Page\b', value)), minimum_pages)
        self.assertIn(b'/Font', value)

    def test_admin_report_repeated_headers_and_pagination(self):
        from .admin_report_service import export_report_pdf
        value = export_report_pdf({'title': 'Compatibility Results', 'category': 'Sports',
            'generated_at': '2026-10-17', 'headers': ['Rank', 'Participant', 'Score'],
            'rows': [[index, 'Participant ' + str(index), '95.50'] for index in range(180)],
            'stats': {'events': 1, 'participants': 180, 'rows': 180, 'winners': 1}})
        self.assert_pdf(value, minimum_pages=2)

    def test_audit_pdf_pagination(self):
        from .audit_service import export_audit_pdf
        rows = [{'datetime_display': 'Oct 17, 2026 10:00 AM', 'user_name': 'Test Judge',
                 'user_role': 'Judge', 'module': 'Criteria-Based Event',
                 'action': 'Score Submitted', 'status': 'Success'} for _ in range(160)]
        self.assert_pdf(export_audit_pdf(rows), minimum_pages=2)

    def test_scoresheets_images_fonts_and_orientations(self):
        from .scoresheet_pdf import render_scoresheet_pdf, default_sample_payload
        image = io.BytesIO()
        Image.new('RGB', (80, 40), 'blue').save(image, format='PNG')
        source = 'data:image/png;base64,' + base64.b64encode(image.getvalue()).decode()
        for kind in ('match', 'criteria'):
            for orientation in ('portrait', 'landscape'):
                with self.subTest(kind=kind, orientation=orientation):
                    value = render_scoresheet_pdf(None, event_type=kind, orientation=orientation,
                                                  payload=default_sample_payload())
                    self.assert_pdf(value)
        value = render_scoresheet_pdf([
            {'type': 'image', 'x': 40, 'y': 40, 'w': 100, 'h': 60, 'props': {'src': source}},
            {'type': 'text', 'x': 40, 'y': 120, 'w': 300, 'h': 30,
             'props': {'text': 'Compatibility Results', 'fontSize': 14}},
        ])
        self.assert_pdf(value)
        self.assertIn(b'/Subtype /Image', value)

    def test_superadmin_report_numbered_canvas(self):
        from core.reports_generator import generate_pdf_report
        event = Event.objects.create(name='PDF Match', category='Sports', scoring_method='match',
                                     event_date=date.today(), venue='Hall')
        self.assert_pdf(generate_pdf_report(event.name).getvalue())
