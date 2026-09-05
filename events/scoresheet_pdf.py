"""Render scoresheet template layouts to PDF via ReportLab."""
from __future__ import annotations

import base64
import io
import re
from typing import Any, Callable

from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import A4, LETTER, landscape, portrait
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

PLACEHOLDER_RE = re.compile(r'\{\{\s*([A-Za-z0-9_]+)\s*\}\}')

# Design canvas ≈ A4 at 96dpi; PDF uses points (1/72").
CANVAS_W = 794.0
CANVAS_H = 1123.0
CURRENT_LAYOUT_VERSION = 3
TOP_MARGIN = 36.0
BOTTOM_MARGIN = 40.0
LEFT_MARGIN = 40.0

MATCH_FIELD_DEFS: list[dict[str, str]] = [
    {'key': 'school_logo', 'label': 'School Logo', 'group': 'header'},
    {'key': 'event_name', 'label': 'Event Name', 'group': 'info'},
    {'key': 'event_classification', 'label': 'Event Classification', 'group': 'info'},
    {'key': 'division', 'label': 'Division', 'group': 'info'},
    {'key': 'tournament_format', 'label': 'Tournament Format', 'group': 'info'},
    {'key': 'game_number', 'label': 'Game Number', 'group': 'meta'},
    {'key': 'round', 'label': 'Round', 'group': 'meta'},
    {'key': 'date', 'label': 'Date', 'group': 'meta'},
    {'key': 'time', 'label': 'Time', 'group': 'meta'},
    {'key': 'venue', 'label': 'Venue', 'group': 'venue'},
    {'key': 'playing_area', 'label': 'Playing Area', 'group': 'venue'},
    {'key': 'team_a', 'label': 'Team A', 'group': 'teams'},
    {'key': 'team_b', 'label': 'Team B', 'group': 'teams'},
    {'key': 'quarter_scores', 'label': 'Quarter or Set Scores', 'group': 'scoring'},
    {'key': 'final_score', 'label': 'Final Score', 'group': 'result'},
    {'key': 'winner', 'label': 'Winner', 'group': 'result'},
    {'key': 'remarks', 'label': 'Remarks', 'group': 'remarks'},
    {'key': 'sig_referee', 'label': 'Referee Signature', 'group': 'signatures'},
    {'key': 'sig_scorer', 'label': 'Scorer Signature', 'group': 'signatures'},
    {'key': 'sig_faculty', 'label': 'Faculty In-Charge Signature', 'group': 'signatures'},
]

CRITERIA_FIELD_DEFS: list[dict[str, str]] = [
    {'key': 'school_logo', 'label': 'School Logo', 'group': 'header'},
    {'key': 'event_name', 'label': 'Event Name', 'group': 'info'},
    {'key': 'stage_name', 'label': 'Stage Name', 'group': 'info'},
    {'key': 'contestant_number', 'label': 'Contestant Number', 'group': 'contestant'},
    {'key': 'contestant_name', 'label': 'Contestant Name', 'group': 'contestant'},
    {'key': 'department', 'label': 'Department', 'group': 'contestant'},
    {'key': 'criteria_table', 'label': 'Criteria Table', 'group': 'criteria'},
    {'key': 'criteria_weight', 'label': 'Criteria Weight', 'group': 'criteria'},
    {'key': 'maximum_score', 'label': 'Maximum Score', 'group': 'criteria'},
    {'key': 'judge_score', 'label': 'Judge Score', 'group': 'scoring'},
    {'key': 'judge_comments', 'label': 'Judge Comments', 'group': 'scoring'},
    {'key': 'total_score', 'label': 'Total Score', 'group': 'scoring'},
    {'key': 'sig_judge', 'label': 'Judge Signature', 'group': 'signatures'},
    {'key': 'sig_faculty', 'label': 'Faculty Signature', 'group': 'signatures'},
]

# Legacy keys mapped during upgrade
_LEGACY_SIGNATURE_MAP = {
    'signature_area': ['sig_referee', 'sig_scorer', 'sig_faculty'],
}
_LEGACY_KEY_ALIASES = {
    'contestant': 'contestant_name',
}


def field_defs_for(event_type: str) -> list[dict[str, str]]:
    if event_type == 'criteria':
        return list(CRITERIA_FIELD_DEFS)
    return list(MATCH_FIELD_DEFS)


def field_keys_for(event_type: str) -> list[str]:
    return [d['key'] for d in field_defs_for(event_type)]


def default_fields_for(event_type: str = 'match') -> dict[str, bool]:
    return {d['key']: True for d in field_defs_for(event_type)}


def default_order_for(event_type: str = 'match') -> list[str]:
    return field_keys_for(event_type)


def normalize_order(order: list[str] | None, event_type: str, fields: dict[str, bool] | None = None) -> list[str]:
    known = field_keys_for(event_type)
    known_set = set(known)
    out: list[str] = []
    seen: set[str] = set()
    for key in order or []:
        key = _LEGACY_KEY_ALIASES.get(key, key)
        if key in _LEGACY_SIGNATURE_MAP:
            for mapped in _LEGACY_SIGNATURE_MAP[key]:
                if mapped in known_set and mapped not in seen:
                    out.append(mapped)
                    seen.add(mapped)
            continue
        if key in known_set and key not in seen:
            out.append(key)
            seen.add(key)
    for key in known:
        if key not in seen:
            out.append(key)
            seen.add(key)
    del fields  # order holds all keys; fields only control visibility
    return out


def _on(fields: dict[str, bool], key: str) -> bool:
    return bool(fields.get(key, False))


def _text(eid, x, y, w, h, text, *, size=12, bold=False, align='left', color='#0b2c5c', page=0):
    return {
        'id': eid, 'type': 'text', 'x': x, 'y': y, 'w': w, 'h': h, 'page': page,
        'props': {
            'text': text, 'fontSize': size,
            'fontWeight': 'bold' if bold else 'normal',
            'align': align, 'color': color,
        },
    }


def _rect(eid, x, y, w, h, *, fill='#ffffff', stroke='#0b2c5c', stroke_w=1.2, page=0):
    return {
        'id': eid, 'type': 'rect', 'x': x, 'y': y, 'w': w, 'h': h, 'page': page,
        'props': {'fill': fill, 'stroke': stroke, 'strokeWidth': stroke_w, 'radius': 0},
    }


def _line(eid, x, y, w, *, page=0):
    return {
        'id': eid, 'type': 'line', 'x': x, 'y': y, 'w': w, 'h': 0, 'page': page,
        'props': {'stroke': '#9fb4cc', 'strokeWidth': 1},
    }


def _sig(eid, x, y, w, h, label, *, page=0):
    return {
        'id': eid, 'type': 'signature', 'x': x, 'y': y, 'w': w, 'h': h, 'page': page,
        'props': {'label': label},
    }


def _table(eid, x, y, w, h, rows, cols, *, page=0, headers=None):
    return {
        'id': eid, 'type': 'table', 'x': x, 'y': y, 'w': w, 'h': h, 'page': page,
        'props': {'rows': rows, 'cols': cols, 'stroke': '#9fb4cc', 'headers': headers or []},
    }


class _Flow:
    def __init__(self, orientation: str = 'portrait'):
        self.orientation = orientation
        design_w = CANVAS_H if orientation == 'landscape' else CANVAS_W
        self.left = LEFT_MARGIN
        self.width = design_w - LEFT_MARGIN * 2
        self.page_bottom = (CANVAS_W if orientation == 'landscape' else CANVAS_H) - BOTTOM_MARGIN
        self.y = TOP_MARGIN
        self.page = 0
        self.elements: list[dict[str, Any]] = []

    def ensure(self, height: float):
        if self.y + height > self.page_bottom:
            self.page += 1
            self.y = TOP_MARGIN

    def advance(self, amount: float):
        self.y += amount


def layout_from_fields(
    fields: dict[str, bool] | None,
    event_type: str = 'match',
    order: list[str] | None = None,
    orientation: str = 'portrait',
) -> list[dict[str, Any]]:
    """Build PDF canvas elements from selected/ordered template fields."""
    fields = {**default_fields_for(event_type), **(fields or {})}
    order = normalize_order(order, event_type, fields)
    flow = _Flow(orientation)
    enabled = [k for k in order if _on(fields, k)]

    i = 0
    while i < len(enabled):
        key = enabled[i]
        group = next((d['group'] for d in field_defs_for(event_type) if d['key'] == key), '')

        # Side-by-side groups
        if group == 'teams' and key == 'team_a':
            show_b = i + 1 < len(enabled) and enabled[i + 1] == 'team_b'
            _render_teams(flow, True, show_b)
            i += 2 if show_b else 1
            continue
        if group == 'teams' and key == 'team_b':
            _render_teams(flow, False, True)
            i += 1
            continue
        if group == 'result':
            keys = [key]
            j = i + 1
            while j < len(enabled):
                g = next((d['group'] for d in field_defs_for(event_type) if d['key'] == enabled[j]), '')
                if g != 'result':
                    break
                keys.append(enabled[j])
                j += 1
            _render_result_row(flow, keys)
            i = j
            continue
        if group == 'signatures':
            keys = [key]
            j = i + 1
            while j < len(enabled):
                g = next((d['group'] for d in field_defs_for(event_type) if d['key'] == enabled[j]), '')
                if g != 'signatures':
                    break
                keys.append(enabled[j])
                j += 1
            _render_signatures(flow, keys)
            i = j
            continue
        if group == 'meta':
            keys = [key]
            j = i + 1
            while j < len(enabled):
                g = next((d['group'] for d in field_defs_for(event_type) if d['key'] == enabled[j]), '')
                if g != 'meta':
                    break
                keys.append(enabled[j])
                j += 1
            _render_meta_row(flow, keys)
            i = j
            continue
        if group == 'venue':
            keys = [key]
            j = i + 1
            while j < len(enabled):
                g = next((d['group'] for d in field_defs_for(event_type) if d['key'] == enabled[j]), '')
                if g != 'venue':
                    break
                keys.append(enabled[j])
                j += 1
            _render_venue_row(flow, keys)
            i = j
            continue
        if group == 'contestant':
            keys = [key]
            j = i + 1
            while j < len(enabled):
                g = next((d['group'] for d in field_defs_for(event_type) if d['key'] == enabled[j]), '')
                if g != 'contestant':
                    break
                keys.append(enabled[j])
                j += 1
            _render_contestant(flow, keys)
            i = j
            continue

        renderer = FIELD_RENDERERS.get(key)
        if renderer:
            renderer(flow)
        i += 1

    return flow.elements


def _render_header_logo(flow: _Flow):
    flow.ensure(70)
    flow.elements.append(_rect('school_logo', flow.left, flow.y, 70, 70, stroke='#0b2c5c', stroke_w=2, page=flow.page))
    flow.elements.append(_text(
        'school_logo_label', flow.left, flow.y + 22, 70, 28, 'LOGO',
        size=11, bold=True, align='center', page=flow.page,
    ))
    flow.elements.append(_text(
        'sheet_title', flow.left + 90, flow.y + 18, flow.width - 90, 36,
        'OFFICIAL SCORESHEET', size=18, bold=True, align='left', page=flow.page,
    ))
    flow.advance(82)


def _render_line_field(flow: _Flow, eid: str, label_text: str):
    flow.ensure(28)
    flow.elements.append(_text(eid, flow.left, flow.y, flow.width, 22, label_text, size=12, bold=True, page=flow.page))
    flow.advance(28)


def _render_meta_row(flow: _Flow, keys: list[str]):
    mapping = {
        'game_number': 'GAME NO. {{GameNumber}}',
        'round': 'ROUND: {{Round}}',
        'date': 'DATE: {{Date}}',
        'time': 'TIME: {{Time}}',
    }
    bits = [mapping[k] for k in keys if k in mapping]
    if not bits:
        return
    flow.ensure(28)
    flow.elements.append(_text('meta_row', flow.left, flow.y, flow.width, 22, '   |   '.join(bits), size=11, bold=True, page=flow.page))
    flow.advance(28)


def _render_venue_row(flow: _Flow, keys: list[str]):
    mapping = {
        'venue': 'VENUE: {{Venue}}',
        'playing_area': 'PLAYING AREA: {{PlayingArea}}',
    }
    bits = [mapping[k] for k in keys if k in mapping]
    if not bits:
        return
    flow.ensure(28)
    flow.elements.append(_text('venue_row', flow.left, flow.y, flow.width, 22, '   |   '.join(bits), size=11, page=flow.page))
    flow.advance(28)


def _render_teams(flow: _Flow, show_a: bool, show_b: bool):
    flow.ensure(100)
    box_w = 300.0
    y = flow.y
    if show_a:
        flow.elements.append(_rect('team_a_box', flow.left, y, box_w, 84, stroke_w=1.5, page=flow.page))
        flow.elements.append(_text(
            'team_a', flow.left + 10, y + 14, box_w - 20, 58, 'TEAM A\n{{TeamA}}',
            size=14, bold=True, align='center', page=flow.page,
        ))
    if show_a and show_b:
        flow.elements.append(_text(
            'vs', flow.left + box_w + 10, y + 28, 74, 30, 'VS',
            size=18, bold=True, align='center', color='#155bd7', page=flow.page,
        ))
    if show_b:
        bx = flow.left + flow.width - box_w
        flow.elements.append(_rect('team_b_box', bx, y, box_w, 84, stroke_w=1.5, page=flow.page))
        flow.elements.append(_text(
            'team_b', bx + 10, y + 14, box_w - 20, 58, 'TEAM B\n{{TeamB}}',
            size=14, bold=True, align='center', page=flow.page,
        ))
    flow.advance(100)


def _render_result_row(flow: _Flow, keys: list[str]):
    flow.ensure(88)
    col_w = (flow.width - 16 * (len(keys) - 1)) / max(len(keys), 1)
    x = flow.left
    for key in keys:
        if key == 'final_score':
            flow.elements.append(_rect('score_box', x, flow.y, col_w, 72, fill='#f8fbff', stroke='#9fb4cc', page=flow.page))
            flow.elements.append(_text(
                'final_score', x + 10, flow.y + 10, col_w - 20, 52,
                'FINAL SCORE\n{{ScoreA}}  —  {{ScoreB}}', size=13, bold=True, align='center', page=flow.page,
            ))
        elif key == 'winner':
            flow.elements.append(_rect('winner_box', x, flow.y, col_w, 72, stroke='#155bd7', stroke_w=1.5, page=flow.page))
            flow.elements.append(_text(
                'winner', x + 10, flow.y + 10, col_w - 20, 52,
                'WINNER\n{{Winner}}', size=13, bold=True, align='center', color='#155bd7', page=flow.page,
            ))
        x += col_w + 16
    flow.advance(88)


def _render_signatures(flow: _Flow, keys: list[str]):
    labels = {
        'sig_referee': 'REFEREE:\nSignature',
        'sig_scorer': 'SCORER:\nSignature',
        'sig_faculty': 'FACULTY IN-CHARGE:\nSignature',
        'sig_judge': 'JUDGE:\nSignature',
    }
    n = len(keys)
    flow.ensure(100)
    gap = 16.0
    sig_w = (flow.width - gap * (n - 1)) / max(n, 1)
    for i, key in enumerate(keys):
        sx = flow.left + i * (sig_w + gap)
        flow.elements.append(_sig(key, sx, flow.y, sig_w, 90, labels.get(key, 'SIGNATURE'), page=flow.page))
    flow.advance(100)


def _render_contestant(flow: _Flow, keys: list[str]):
    lines = []
    if 'contestant_number' in keys:
        lines.append('CONTESTANT NO.: {{ContestantNumber}}')
    if 'contestant_name' in keys:
        lines.append('CONTESTANT: {{Contestant}}')
    if 'department' in keys:
        lines.append('DEPARTMENT: {{Department}}')
    if not lines:
        return
    h = 18 + 22 * len(lines)
    flow.ensure(h)
    flow.elements.append(_rect('contestant_box', flow.left, flow.y, flow.width, h - 8, page=flow.page))
    flow.elements.append(_text(
        'contestant_info', flow.left + 12, flow.y + 8, flow.width - 24, 22 * len(lines),
        '\n'.join(lines), size=12, bold=True, page=flow.page,
    ))
    flow.advance(h)


def _render_quarter_scores(flow: _Flow):
    flow.ensure(150)
    flow.elements.append(_text('q_label', flow.left, flow.y, flow.width, 20, 'QUARTER / SET SCORES', size=12, bold=True, page=flow.page))
    flow.advance(24)
    flow.elements.append(_table(
        'quarter_table', flow.left, flow.y, flow.width, 110, 3, 5,
        page=flow.page, headers=['', 'Q1/S1', 'Q2/S2', 'Q3/S3', 'Q4/S4'],
    ))
    flow.advance(126)


def _render_remarks(flow: _Flow):
    flow.ensure(110)
    flow.elements.append(_text('remarks_title', flow.left, flow.y, 200, 22, 'REMARKS', size=12, bold=True, page=flow.page))
    flow.advance(28)
    for i in range(3):
        flow.elements.append(_line(f'remark_{i}', flow.left, flow.y + i * 28, flow.width, page=flow.page))
    flow.advance(96)


def _render_criteria_table(flow: _Flow):
    flow.ensure(200)
    flow.elements.append(_text('criteria_label', flow.left, flow.y, flow.width, 20, 'CRITERIA', size=12, bold=True, page=flow.page))
    flow.advance(24)
    flow.elements.append(_table(
        'criteria_table', flow.left, flow.y, flow.width, 160, 6, 4,
        page=flow.page, headers=['Criterion', 'Weight', 'Max', 'Score'],
    ))
    flow.advance(176)


def _render_criteria_weight(flow: _Flow):
    _render_line_field(flow, 'criteria_weight', 'CRITERIA WEIGHT: {{CriteriaWeight}}')


def _render_maximum_score(flow: _Flow):
    _render_line_field(flow, 'maximum_score', 'MAXIMUM SCORE: {{MaximumScore}}')


def _render_judge_score(flow: _Flow):
    flow.ensure(56)
    flow.elements.append(_rect('judge_score_box', flow.left, flow.y, 260, 48, fill='#f8fbff', stroke='#9fb4cc', page=flow.page))
    flow.elements.append(_text(
        'judge_score', flow.left + 10, flow.y + 8, 240, 32,
        'JUDGE SCORE\n{{JudgeScore}}', size=12, bold=True, align='center', page=flow.page,
    ))
    flow.advance(60)


def _render_judge_comments(flow: _Flow):
    flow.ensure(100)
    flow.elements.append(_text('judge_comments_title', flow.left, flow.y, 200, 22, 'JUDGE COMMENTS', size=12, bold=True, page=flow.page))
    flow.advance(28)
    for i in range(3):
        flow.elements.append(_line(f'judge_comment_{i}', flow.left, flow.y + i * 28, flow.width, page=flow.page))
    flow.advance(96)


def _render_total_score(flow: _Flow):
    flow.ensure(70)
    flow.elements.append(_rect('total_box', flow.left, flow.y, 260, 56, fill='#f8fbff', stroke='#155bd7', page=flow.page))
    flow.elements.append(_text(
        'total_score', flow.left + 10, flow.y + 8, 240, 40,
        'TOTAL SCORE\n{{TotalScore}}', size=13, bold=True, align='center', color='#155bd7', page=flow.page,
    ))
    flow.advance(70)


FIELD_RENDERERS: dict[str, Callable[[_Flow], None]] = {
    'school_logo': _render_header_logo,
    'event_name': lambda f: _render_line_field(f, 'event_name', 'EVENT: {{EventName}}'),
    'event_classification': lambda f: _render_line_field(f, 'event_classification', 'CLASSIFICATION: {{Classification}}'),
    'division': lambda f: _render_line_field(f, 'division', 'DIVISION: {{Division}}'),
    'tournament_format': lambda f: _render_line_field(f, 'tournament_format', 'TOURNAMENT FORMAT: {{TournamentFormat}}'),
    'stage_name': lambda f: _render_line_field(f, 'stage_name', 'STAGE: {{StageName}}'),
    'quarter_scores': _render_quarter_scores,
    'remarks': _render_remarks,
    'criteria_table': _render_criteria_table,
    'criteria_weight': _render_criteria_weight,
    'maximum_score': _render_maximum_score,
    'judge_score': _render_judge_score,
    'judge_comments': _render_judge_comments,
    'total_score': _render_total_score,
}


def pack_template_layout(
    fields: dict[str, bool] | None,
    event_type: str = 'match',
    order: list[str] | None = None,
    orientation: str = 'portrait',
    builder: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_fields = {**default_fields_for(event_type), **(fields or {})}
    normalized_order = normalize_order(order, event_type, normalized_fields)
    packed = {
        'version': CURRENT_LAYOUT_VERSION,
        'event_type': event_type,
        'fields': normalized_fields,
        'order': normalized_order,
        'elements': layout_from_fields(normalized_fields, event_type, normalized_order, orientation),
    }
    if isinstance(builder, dict) and builder:
        packed['builder'] = builder
    return packed


def extract_builder(layout: Any) -> dict[str, Any]:
    if isinstance(layout, dict) and isinstance(layout.get('builder'), dict):
        return dict(layout.get('builder') or {})
    return {}


def upgrade_layout(layout: Any, event_type: str = 'match') -> dict[str, Any]:
    builder = extract_builder(layout)
    if isinstance(layout, dict) and int(layout.get('version') or 0) >= CURRENT_LAYOUT_VERSION:
        et = layout.get('event_type') or event_type
        fields = extract_fields(layout, et)
        order = extract_order(layout, et)
        packed = {
            'version': CURRENT_LAYOUT_VERSION,
            'event_type': et,
            'fields': fields,
            'order': order,
            'elements': layout_from_fields(fields, et, order, 'portrait'),
        }
        if builder:
            packed['builder'] = builder
        return packed
    et = event_type
    if isinstance(layout, dict):
        et = layout.get('event_type') or event_type
        raw_fields = layout.get('fields') if isinstance(layout.get('fields'), dict) else {}
        # Map legacy signature_area
        fields = default_fields_for(et)
        for k, v in raw_fields.items():
            if k == 'signature_area':
                for mapped in _LEGACY_SIGNATURE_MAP['signature_area']:
                    if mapped in fields:
                        fields[mapped] = bool(v)
                continue
            alias = _LEGACY_KEY_ALIASES.get(k, k)
            if alias in fields:
                fields[alias] = bool(v)
        order = extract_order(layout, et)
        return pack_template_layout(fields, et, order, builder=builder)
    return pack_template_layout(default_fields_for(et), et, builder=builder)


def extract_fields(layout: Any, event_type: str = 'match') -> dict[str, bool]:
    defaults = default_fields_for(event_type)
    if isinstance(layout, dict):
        raw = layout.get('fields')
        if isinstance(raw, dict):
            out = dict(defaults)
            for k, v in raw.items():
                if k == 'signature_area':
                    for mapped in _LEGACY_SIGNATURE_MAP['signature_area']:
                        if mapped in out:
                            out[mapped] = bool(v)
                    continue
                alias = _LEGACY_KEY_ALIASES.get(k, k)
                if alias in out:
                    out[alias] = bool(v)
            return out
    return defaults


def extract_order(layout: Any, event_type: str = 'match') -> list[str]:
    if isinstance(layout, dict) and isinstance(layout.get('order'), list):
        return normalize_order(layout.get('order'), event_type)
    return default_order_for(event_type)


def resolve_elements(
    layout: Any,
    event_type: str = 'match',
    orientation: str = 'portrait',
) -> list[dict[str, Any]]:
    if isinstance(layout, dict):
        version = int(layout.get('version') or 0)
        et = layout.get('event_type') or event_type
        fields = extract_fields(layout, et)
        order = extract_order(layout, et)
        if version >= CURRENT_LAYOUT_VERSION:
            elements = layout.get('elements')
            if isinstance(elements, list) and elements:
                return elements
        return layout_from_fields(fields, et, order, orientation)
    if isinstance(layout, list) and layout:
        return layout
    return layout_from_fields(default_fields_for(event_type), event_type, None, orientation)


def default_match_layout() -> list[dict[str, Any]]:
    return layout_from_fields(default_fields_for('match'), 'match')


def default_sample_payload() -> dict[str, str]:
    return {
        'GameNumber': '12',
        'Round': 'Semifinal',
        'Date': 'May 20, 2025',
        'Time': '2:00 PM',
        'Venue': 'Main Gym',
        'PlayingArea': 'Court 1',
        'EventName': 'Basketball Men',
        'Classification': 'Intramurals',
        'Division': 'Men',
        'TournamentFormat': 'Single Elimination',
        'TeamA': 'Engineering Tigers',
        'TeamB': 'Science Hawks',
        'ScoreA': '',
        'ScoreB': '',
        'Winner': '',
        'StageName': 'Preliminary Round',
        'Contestant': 'Alex Rivera',
        'ContestantNumber': '07',
        'Department': 'College of Engineering',
        'CriteriaWeight': '100%',
        'MaximumScore': '100',
        'JudgeScore': '',
        'TotalScore': '',
        'FacultyInCharge': 'Prof. Santos',
        'Remarks': '',
    }


def substitute_text(text: str, payload: dict[str, Any] | None, *, blank_unresolved: bool = False) -> str:
    payload = payload or {}

    def repl(match):
        key = match.group(1)
        value = payload.get(key)
        if value is None or value == '':
            return '' if blank_unresolved else match.group(0)
        return str(value)

    return PLACEHOLDER_RE.sub(repl, text or '')


def _page_size(orientation: str, paper_size: str = 'a4'):
    base = LETTER if (paper_size or '').lower() == 'letter' else A4
    return landscape(base) if orientation == 'landscape' else portrait(base)


def _to_pdf_coords(x, y, w, h, page_w, page_h, orientation: str):
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
    layout: list[dict[str, Any]] | dict[str, Any] | None,
    *,
    orientation: str = 'portrait',
    paper_size: str = 'a4',
    payload: dict[str, Any] | None = None,
    event_type: str = 'match',
    blank_unresolved: bool = False,
) -> bytes:
    elements = resolve_elements(layout, event_type, orientation)
    payload = payload or {}
    buffer = io.BytesIO()
    page_w, page_h = _page_size(orientation, paper_size)
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h))

    pages: dict[int, list] = {}
    for el in elements:
        pages.setdefault(int(el.get('page') or 0), []).append(el)
    page_nums = sorted(pages.keys()) or [0]

    for pi, page_num in enumerate(page_nums):
        if pi > 0:
            c.showPage()
        for el in pages[page_num]:
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
                        _header, b64 = src.split(',', 1)
                        img = ImageReader(io.BytesIO(base64.b64decode(b64)))
                        c.drawImage(img, px + 2, py + 2, width=pw - 4, height=ph - 4, preserveAspectRatio=True, mask='auto')
                        drawn = True
                    except Exception:
                        drawn = False
                if not drawn:
                    c.setFillColor(_color(props.get('color', '#5a6f86')))
                    c.setFont('Helvetica', max(8, 10 * min(scale_x, scale_y)))
                    c.drawCentredString(px + pw / 2, py + ph / 2 - 4, str(props.get('label', 'LOGO')))

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
                c.line(px, py + ph, px + pw, py + ph)

            elif el_type == 'table':
                rows = int(props.get('rows', 4))
                cols = int(props.get('cols', 3))
                headers = props.get('headers') or []
                c.setStrokeColor(_color(props.get('stroke', '#9fb4cc')))
                c.setFillColor(white)
                c.setLineWidth(0.8)
                c.rect(px, py, pw, ph, stroke=1, fill=1)
                if rows > 0 and cols > 0:
                    cell_w = pw / cols
                    cell_h = ph / rows
                    for ci in range(1, cols):
                        c.line(px + ci * cell_w, py, px + ci * cell_w, py + ph)
                    for rj in range(1, rows):
                        c.line(px, py + rj * cell_h, px + pw, py + rj * cell_h)
                    if headers:
                        c.setFillColor(_color('#0b2c5c'))
                        c.setFont('Helvetica-Bold', max(7, 9 * min(scale_x, scale_y)))
                        header_y = py + ph - cell_h + 6
                        for hi, header in enumerate(headers[:cols]):
                            c.drawCentredString(px + hi * cell_w + cell_w / 2, header_y, str(header))

            elif el_type == 'signature':
                label = substitute_text(props.get('label', 'Signature'), payload, blank_unresolved=blank_unresolved)
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
                text = substitute_text(str(raw), payload, blank_unresolved=blank_unresolved)
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
                c.setStrokeColor(black)
                c.setFillColor(white)
                c.rect(px, py, pw, ph, stroke=1, fill=0)

    c.showPage()
    c.save()
    return buffer.getvalue()
