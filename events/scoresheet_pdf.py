"""Render scoresheet template layouts to PDF via ReportLab."""
from __future__ import annotations

import base64
import io
import re
from typing import Any

from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import A4, landscape, portrait
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

PLACEHOLDER_RE = re.compile(r'\{\{\s*([A-Za-z0-9_]+)\s*\}\}')

# Design canvas is 794 x 1123 CSS px ≈ A4 at 96dpi; PDF uses points (1/72").
CANVAS_W = 794.0
CANVAS_H = 1123.0


def default_match_layout() -> list[dict[str, Any]]:
    """Intramurals-style default match scoresheet layout."""
    return [
        {
            'id': 'logo', 'type': 'rect', 'x': 40, 'y': 36, 'w': 70, 'h': 70,
            'props': {'fill': '#ffffff', 'stroke': '#0b2c5c', 'strokeWidth': 2, 'radius': 35},
        },
        {
            'id': 'logo_text', 'type': 'text', 'x': 48, 'y': 58, 'w': 54, 'h': 30,
            'props': {'text': 'LOGO', 'fontSize': 11, 'fontWeight': 'bold', 'align': 'center', 'color': '#0b2c5c'},
        },
        {
            'id': 'title', 'type': 'text', 'x': 130, 'y': 40, 'w': 420, 'h': 36,
            'props': {
                'text': 'UNIVERSITY OF EXCELLENCE\nINTRAMURALS 2025',
                'fontSize': 16, 'fontWeight': 'bold', 'align': 'center', 'color': '#0b2c5c',
            },
        },
        {
            'id': 'motto', 'type': 'text', 'x': 130, 'y': 82, 'w': 420, 'h': 20,
            'props': {
                'text': 'Unity. Sportsmanship. Excellence.',
                'fontSize': 10, 'fontWeight': 'normal', 'align': 'center', 'color': '#5a6f86',
            },
        },
        {
            'id': 'meta_box', 'type': 'rect', 'x': 560, 'y': 36, 'w': 194, 'h': 78,
            'props': {'fill': '#f5f8fc', 'stroke': '#c5d3e4', 'strokeWidth': 1, 'radius': 0},
        },
        {
            'id': 'meta_text', 'type': 'text', 'x': 572, 'y': 46, 'w': 170, 'h': 60,
            'props': {
                'text': 'GAME NO. {{GameNumber}}\nDATE: {{Date}}\nVENUE: {{Venue}}',
                'fontSize': 10, 'fontWeight': 'normal', 'align': 'left', 'color': '#1d3554',
            },
        },
        {
            'id': 'event_label', 'type': 'text', 'x': 40, 'y': 130, 'w': 714, 'h': 24,
            'props': {
                'text': 'EVENT: {{EventName}}',
                'fontSize': 13, 'fontWeight': 'bold', 'align': 'left', 'color': '#0b2c5c',
            },
        },
        {
            'id': 'team_a_box', 'type': 'rect', 'x': 40, 'y': 170, 'w': 300, 'h': 90,
            'props': {'fill': '#ffffff', 'stroke': '#0b2c5c', 'strokeWidth': 1.5, 'radius': 0},
        },
        {
            'id': 'team_a_label', 'type': 'text', 'x': 50, 'y': 182, 'w': 280, 'h': 66,
            'props': {
                'text': 'TEAM A\n{{TeamA}}',
                'fontSize': 14, 'fontWeight': 'bold', 'align': 'center', 'color': '#0b2c5c',
            },
        },
        {
            'id': 'vs', 'type': 'text', 'x': 360, 'y': 200, 'w': 74, 'h': 30,
            'props': {'text': 'VS', 'fontSize': 18, 'fontWeight': 'bold', 'align': 'center', 'color': '#155bd7'},
        },
        {
            'id': 'team_b_box', 'type': 'rect', 'x': 454, 'y': 170, 'w': 300, 'h': 90,
            'props': {'fill': '#ffffff', 'stroke': '#0b2c5c', 'strokeWidth': 1.5, 'radius': 0},
        },
        {
            'id': 'team_b_label', 'type': 'text', 'x': 464, 'y': 182, 'w': 280, 'h': 66,
            'props': {
                'text': 'TEAM B\n{{TeamB}}',
                'fontSize': 14, 'fontWeight': 'bold', 'align': 'center', 'color': '#0b2c5c',
            },
        },
        {
            'id': 'score_a_box', 'type': 'rect', 'x': 40, 'y': 290, 'w': 220, 'h': 80,
            'props': {'fill': '#f8fbff', 'stroke': '#9fb4cc', 'strokeWidth': 1, 'radius': 0},
        },
        {
            'id': 'score_a', 'type': 'text', 'x': 50, 'y': 300, 'w': 200, 'h': 60,
            'props': {
                'text': 'SCORE\n{{ScoreA}}',
                'fontSize': 14, 'fontWeight': 'bold', 'align': 'center', 'color': '#0b2c5c',
            },
        },
        {
            'id': 'winner_box', 'type': 'rect', 'x': 280, 'y': 290, 'w': 234, 'h': 80,
            'props': {'fill': '#ffffff', 'stroke': '#155bd7', 'strokeWidth': 1.5, 'radius': 0},
        },
        {
            'id': 'winner', 'type': 'text', 'x': 290, 'y': 300, 'w': 214, 'h': 60,
            'props': {
                'text': 'WINNER\n{{Winner}}',
                'fontSize': 13, 'fontWeight': 'bold', 'align': 'center', 'color': '#155bd7',
            },
        },
        {
            'id': 'score_b_box', 'type': 'rect', 'x': 534, 'y': 290, 'w': 220, 'h': 80,
            'props': {'fill': '#f8fbff', 'stroke': '#9fb4cc', 'strokeWidth': 1, 'radius': 0},
        },
        {
            'id': 'score_b', 'type': 'text', 'x': 544, 'y': 300, 'w': 200, 'h': 60,
            'props': {
                'text': 'SCORE\n{{ScoreB}}',
                'fontSize': 14, 'fontWeight': 'bold', 'align': 'center', 'color': '#0b2c5c',
            },
        },
        {
            'id': 'remarks_title', 'type': 'text', 'x': 40, 'y': 400, 'w': 200, 'h': 22,
            'props': {'text': 'REMARKS', 'fontSize': 12, 'fontWeight': 'bold', 'align': 'left', 'color': '#0b2c5c'},
        },
        {'id': 'remark_1', 'type': 'line', 'x': 40, 'y': 440, 'w': 714, 'h': 0, 'props': {'stroke': '#9fb4cc', 'strokeWidth': 1}},
        {'id': 'remark_2', 'type': 'line', 'x': 40, 'y': 470, 'w': 714, 'h': 0, 'props': {'stroke': '#9fb4cc', 'strokeWidth': 1}},
        {'id': 'remark_3', 'type': 'line', 'x': 40, 'y': 500, 'w': 714, 'h': 0, 'props': {'stroke': '#9fb4cc', 'strokeWidth': 1}},
        {
            'id': 'sig_prep', 'type': 'signature', 'x': 40, 'y': 560, 'w': 220, 'h': 90,
            'props': {'label': 'PREPARED BY:\n(Scorer)'},
        },
        {
            'id': 'sig_check', 'type': 'signature', 'x': 287, 'y': 560, 'w': 220, 'h': 90,
            'props': {'label': 'CHECKED BY:\n(Referee)'},
        },
        {
            'id': 'sig_appr', 'type': 'signature', 'x': 534, 'y': 560, 'w': 220, 'h': 90,
            'props': {'label': 'APPROVED BY:\n(Event Coordinator)'},
        },
    ]


def default_sample_payload() -> dict[str, str]:
    return {
        'GameNumber': '12',
        'Date': 'May 20, 2025',
        'Venue': 'Main Gym',
        'EventName': 'Basketball Men',
        'TeamA': 'Engineering Tigers',
        'TeamB': 'Science Hawks',
        'ScoreA': '78',
        'ScoreB': '72',
        'Winner': 'Engineering Tigers',
        'Remarks': '',
    }


def substitute_text(text: str, payload: dict[str, Any] | None) -> str:
    payload = payload or {}

    def repl(match):
        key = match.group(1)
        value = payload.get(key)
        if value is None or value == '':
            return match.group(0)
        return str(value)

    return PLACEHOLDER_RE.sub(repl, text or '')


def _page_size(orientation: str):
    return landscape(A4) if orientation == 'landscape' else portrait(A4)


def _to_pdf_coords(x, y, w, h, page_w, page_h, orientation: str):
    """Map design-canvas coords (origin top-left) into PDF points (origin bottom-left)."""
    design_w = CANVAS_H if orientation == 'landscape' else CANVAS_W
    design_h = CANVAS_W if orientation == 'landscape' else CANVAS_H
    scale_x = page_w / design_w
    scale_y = page_h / design_h
    px = float(x) * scale_x
    py = page_h - (float(y) + float(h)) * scale_y
    pw = float(w) * scale_x
    ph = float(h) * scale_y
    return px, py, pw, ph, scale_x, scale_y


def _color(value, fallback='#000000'):
    try:
        return HexColor(value or fallback)
    except Exception:
        return HexColor(fallback)


def render_scoresheet_pdf(
    layout: list[dict[str, Any]] | None,
    *,
    orientation: str = 'portrait',
    paper_size: str = 'a4',
    payload: dict[str, Any] | None = None,
) -> bytes:
    del paper_size  # currently A4 only
    layout = layout or default_match_layout()
    payload = payload or {}
    buffer = io.BytesIO()
    page_w, page_h = _page_size(orientation)
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h))

    for el in layout:
        el_type = (el.get('type') or 'text').lower()
        x, y, w, h = el.get('x', 0), el.get('y', 0), el.get('w', 100), el.get('h', 24)
        props = el.get('props') or {}
        px, py, pw, ph, scale_x, scale_y = _to_pdf_coords(x, y, w, h, page_w, page_h, orientation)

        if el_type == 'image':
            src = props.get('src') or ''
            stroke = props.get('stroke', '#0b2c5c')
            stroke_w = float(props.get('strokeWidth', 1)) * min(scale_x, scale_y)
            c.setStrokeColor(_color(stroke))
            c.setLineWidth(max(0.4, stroke_w))
            c.setFillColor(white)
            c.rect(px, py, pw, ph, stroke=1, fill=1)
            drawn = False
            if isinstance(src, str) and src.startswith('data:image'):
                try:
                    header, b64 = src.split(',', 1)
                    img = ImageReader(io.BytesIO(base64.b64decode(b64)))
                    c.drawImage(img, px + 2, py + 2, width=pw - 4, height=ph - 4, preserveAspectRatio=True, mask='auto')
                    drawn = True
                except Exception:
                    drawn = False
            if not drawn:
                c.setFillColor(_color(props.get('color', '#5a6f86')))
                c.setFont('Helvetica', max(8, 10 * min(scale_x, scale_y)))
                c.drawCentredString(px + pw / 2, py + ph / 2 - 4, str(props.get('label', 'IMAGE / LOGO')))

        elif el_type in {'rect', 'rectangle'}:
            fill = props.get('fill', '#ffffff')
            stroke = props.get('stroke', '#0b2c5c')
            stroke_w = float(props.get('strokeWidth', 1)) * min(scale_x, scale_y)
            radius = float(props.get('radius', 0)) * min(scale_x, scale_y)
            c.setStrokeColor(_color(stroke))
            c.setFillColor(_color(fill, '#ffffff'))
            c.setLineWidth(max(0.4, stroke_w))
            if radius > 0:
                c.roundRect(px, py, pw, ph, min(radius, min(pw, ph) / 2), stroke=1, fill=1)
            else:
                c.rect(px, py, pw, ph, stroke=1, fill=1)

        elif el_type == 'line':
            stroke = props.get('stroke', '#9fb4cc')
            stroke_w = float(props.get('strokeWidth', 1)) * min(scale_x, scale_y)
            c.setStrokeColor(_color(stroke))
            c.setLineWidth(max(0.5, stroke_w))
            # line uses w as length along x; h ignored
            c.line(px, py + ph, px + pw, py + ph)

        elif el_type == 'table':
            rows = int(props.get('rows', 4))
            cols = int(props.get('cols', 3))
            c.setStrokeColor(_color(props.get('stroke', '#9fb4cc')))
            c.setFillColor(white)
            c.setLineWidth(0.8)
            c.rect(px, py, pw, ph, stroke=1, fill=1)
            if rows > 0 and cols > 0:
                cell_w = pw / cols
                cell_h = ph / rows
                for i in range(1, cols):
                    c.line(px + i * cell_w, py, px + i * cell_w, py + ph)
                for j in range(1, rows):
                    c.line(px, py + j * cell_h, px + pw, py + j * cell_h)

        elif el_type == 'signature':
            label = substitute_text(props.get('label', 'Signature'), payload)
            c.setStrokeColor(_color('#9fb4cc'))
            c.setLineWidth(0.8)
            c.line(px + 10, py + ph * 0.45, px + pw - 10, py + ph * 0.45)
            c.setFillColor(_color(props.get('color', '#0b2c5c')))
            font_size = max(8, float(props.get('fontSize', 10)) * min(scale_x, scale_y))
            c.setFont('Helvetica-Bold' if props.get('fontWeight') == 'bold' else 'Helvetica', font_size)
            lines = label.split('\n')
            ty = py + ph * 0.28
            for line in lines:
                c.drawCentredString(px + pw / 2, ty, line)
                ty -= font_size + 2

        elif el_type in {'text', 'input'}:
            raw = props.get('text') or props.get('placeholder') or ('{{Field}}' if el_type == 'input' else 'Text')
            text = substitute_text(str(raw), payload)
            if el_type == 'input':
                c.setStrokeColor(_color(props.get('stroke', '#c5d3e4')))
                c.setFillColor(_color(props.get('fill', '#ffffff')))
                c.setLineWidth(0.7)
                c.rect(px, py, pw, ph, stroke=1, fill=1)
            font_size = max(7, float(props.get('fontSize', 12)) * min(scale_x, scale_y))
            weight = props.get('fontWeight', 'normal')
            font_name = 'Helvetica-Bold' if weight == 'bold' else 'Helvetica'
            if props.get('italic'):
                font_name = 'Helvetica-BoldOblique' if weight == 'bold' else 'Helvetica-Oblique'
            c.setFillColor(_color(props.get('color', '#000000')))
            c.setFont(font_name, font_size)
            align = props.get('align', 'left')
            lines = text.split('\n')
            total_h = len(lines) * (font_size + 2)
            ty = py + ph - (ph - total_h) / 2 - font_size
            for line in lines:
                if align == 'center':
                    c.drawCentredString(px + pw / 2, ty, line)
                elif align == 'right':
                    c.drawRightString(px + pw - 4, ty, line)
                else:
                    c.drawString(px + 4, ty, line)
                ty -= font_size + 2
        else:
            # Unknown → treat as text box outline
            c.setStrokeColor(black)
            c.setFillColor(white)
            c.rect(px, py, pw, ph, stroke=1, fill=0)

    c.showPage()
    c.save()
    return buffer.getvalue()
