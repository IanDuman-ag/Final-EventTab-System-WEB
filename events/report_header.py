"""Shared official branding for browser and ReportLab reports."""
import io
import logging
from xml.sax.saxutils import escape

from .models import SystemSettings

logger = logging.getLogger(__name__)


def report_branding(config=None):
    config = config or SystemSettings.objects.filter(pk=1).first()
    def logo_url(field):
        return field.url if field else ''
    extras = list(config.report_logos.all()) if config and config.pk else []
    return {
        'school_name': (config.school_name if config else '') or 'EventTab',
        'campus_name': config.campus_name if config else '',
        'report_subtitle': config.report_subtitle if config else '',
        'left_logo': logo_url(config.school_logo) if config else '',
        'right_logo': logo_url(config.report_right_logo) if config else '',
        'extra_left_logos': [{'id': logo.pk, 'url': logo.image.url} for logo in extras if logo.side == 'left'],
        'extra_right_logos': [{'id': logo.pk, 'url': logo.image.url} for logo in extras if logo.side == 'right'],
    }


def pdf_report_header(title, width):
    """Read logos through configured storage, including remote image backends."""
    from PIL import Image as PILImage
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Image, Paragraph, Table, TableStyle
    config = SystemSettings.objects.filter(pk=1).first()
    branding = report_branding(config)
    def image(field, limit=60):
        if not field:
            return ''
        try:
            with field.open('rb') as source:
                data = source.read(5 * 1024 * 1024 + 1)
            if len(data) > 5 * 1024 * 1024:
                raise ValueError('Logo exceeds the supported size.')
            with PILImage.open(io.BytesIO(data)) as original:
                original.thumbnail((600, 600))
                converted = io.BytesIO()
                original.convert('RGBA').save(converted, format='PNG')
            converted.seek(0)
            logo = Image(converted)
            scale = min(limit / logo.imageWidth, limit / logo.imageHeight)
            logo.drawWidth = logo.imageWidth * scale
            logo.drawHeight = logo.imageHeight * scale
            return logo
        except Exception:  # Storage backends can raise provider-specific errors.
            logger.warning('Report logo could not be loaded; using text branding.', exc_info=True)
            return ''
    lines = [escape(branding['school_name'])]
    if branding['campus_name']:
        lines.append(escape(branding['campus_name']))
    if branding['school_name'] != 'EventTab':
        lines.append('EventTab')
    if branding['report_subtitle']:
        lines.append(escape(branding['report_subtitle']))
    lines.append('<b>' + escape(str(title)) + '</b>')
    center = Paragraph('<br/>'.join(lines), ParagraphStyle('OfficialReportHeader', fontSize=12, leading=17, alignment=1, textColor=colors.HexColor('#11245a')))
    extras = list(config.report_logos.all()) if config else []
    multiple = bool(extras)
    side_width = width * .25 if multiple else 72
    def side_logos(side, primary):
        fields = ([primary] if primary else []) + [logo.image for logo in extras if logo.side == side]
        if not fields:
            return ''
        if not multiple:
            return image(fields[0])
        cells = [image(field, min(50, side_width / 2 - 12)) for field in fields]
        rows = [cells[index:index + 2] for index in range(0, len(cells), 2)]
        if len(rows[-1]) == 1:
            rows[-1].append('')
        group = Table(rows, colWidths=[side_width / 2, side_width / 2])
        group.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
        return group
    table = Table([[side_logos('left', config.school_logo if config else None), center,
                    side_logos('right', config.report_right_logo if config else None)]], colWidths=[side_width, width - side_width * 2, side_width])
    table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('BOTTOMPADDING', (0, 0), (-1, -1), 14), ('LINEBELOW', (0, 0), (-1, -1), 1, colors.HexColor('#11245a'))]))
    return table
