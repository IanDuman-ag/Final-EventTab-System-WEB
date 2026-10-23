"""Presentation adapter for the assigned-event Results page.

Scoring stays in the existing engines. Unsupported round aggregates are never
substituted with legacy scores or promoted to published overall results.
"""
from decimal import Decimal
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.urls import reverse
from django.db.models import Q
from django.utils import timezone

from events.criteria_scoring import compute_official_criteria_rankings, normalized_criterion_score
from events.models import (
    CriteriaEventEntry, CriteriaScoreSubmission, JudgeScore, RegistryCandidate,
    Team,
)


def _round_options(event, modern, match):
    configured = [row for row in event.rounds_config or [] if isinstance(row, dict)]
    if match:
        names = event.bracket_matches.exclude(round_name='').values_list('round_name', flat=True).distinct()
        return [{'id': '', 'name': 'Event standings'}] + [{'id': name, 'name': name} for name in names]
    if modern:
        stored = list(event.scoring_categories.values_list('assigned_round_id', flat=True).distinct())
        options = []
        for index, row in enumerate(configured):
            rid = str(row.get('id') or row.get('stage_number') or index + 1)
            options.append({'id': rid, 'name': row.get('name') or rid})
        known = {row['id'] for row in options}
        options.extend({'id': rid, 'name': rid or 'Event scores'} for rid in stored if rid not in known)
        return options
    if configured:
        if any(row.get('mode') == 'preliminary_final' for row in configured):
            from events.faculty_service import stage_panels
            return [{'id': str(row['index']), 'name': row['name']} for row in stage_panels(event)]
        return [{'id': str(row.get('id') or row.get('stage_number') or index + 1),
                 'name': row.get('name') or str(row.get('id') or index + 1)}
                for index, row in enumerate(configured)]
    return [{'id': '', 'name': 'Event scores'}]


def _legacy_breakdown(event, rows, judges):
    from .faculty_views import _build_score_breakdown
    scores = JudgeScore.objects.filter(
        candidate__event=event.judging_event, is_locked=True,
        judge_id__in=[judge['id'] for judge in judges],
    ).prefetch_related('subcriterion_scores__subcriterion')
    score_map = {(score.candidate_id, score.criterion_id, score.judge_id): score for score in scores}
    groups = []
    for participant in rows:
        details = _build_score_breakdown(event, participant.get('candidate_id'))
        for criterion in details:
            criterion['cells'] = []
            for judge in judges:
                score = score_map.get((participant['candidate_id'], criterion['criterion_id'], judge['id']))
                criterion['cells'].append({
                    'value': score.score if score else None,
                    'status': 'Submitted' if score else 'Pending',
                    'subcriteria': [{'name': sub.subcriterion.name, 'value': sub.score,
                                     'max_score': sub.subcriterion.max_score}
                                    for sub in score.subcriterion_scores.all()] if score else [],
                })
            if not criterion['judge_count']:
                criterion['average'] = None
                criterion['contribution'] = None
        if details:
            groups.append({'name': participant['name'], 'number': participant.get('number'),
                           'total': participant.get('final_score') if participant.get('has_score') else None,
                           'rows': details})
    return groups


def _modern_data(event, page, categories):
    round_id = page['selected_round']
    from events.criteria_faculty_service import criteria_rounds
    from events.participant_removal_service import active_participant_ids
    index = next((index for index, row in enumerate(criteria_rounds(event)) if row['id'] == round_id), None)
    ids = active_participant_ids(event, index)
    participants = list(CriteriaEventEntry.objects.filter(event=event, id__in=ids))
    if not participants:
        model = RegistryCandidate if event.participation_type == 'individual' else Team
        participants = list(model.objects.filter(id__in=ids, status=model.STATUS_ACTIVE))
    participant_map = {row.pk: row for row in participants}
    ranked = []
    ranking_mode = any(category.judge_mode == 'ranking' for category in categories)
    if ranking_mode:
        page['weighted'] = False
        page['scope_notice'] = 'Ranking entries are shown for review. Calculated standings are not available for this scoring mode.'
    else:
        try:
            ranked = compute_official_criteria_rankings(event, round_id=round_id, participant_ids=ids)
        except ValueError as exc:
            page['error'] = str(exc)
    page['scope_notice'] = page['scope_notice'] or 'Round standings use approved, complete judge sheets. Other submissions remain pending verification.'
    multi_round = len(page['rounds']) > 1
    if multi_round:
        page['scope_notice'] += ' Round scores are shown separately; overall standings are not available for this event.'
    rows = []
    for result in ranked:
        participant = participant_map.get(result['participant_id'])
        rows.append(dict(result, number=getattr(participant, 'number', ''), department='',
                         has_score=True, rank=result['rank'] if result['complete'] else None,
                         status='Complete' if result['complete'] else 'Awaiting judges',
                         prize=(f"Round rank {result['rank']}" if multi_round else _placement(result['rank']))
                         if result['complete'] else ''))
    selected = page['selected_category']
    visible_categories = [category for category in categories if not selected or str(category.pk) == selected]
    # There is no category-only standings service. Do not display full-round
    # totals as though they were scores for one selected scoring category.
    page['live_rows'] = rows if not selected else []
    if selected:
        page['scope_notice'] = 'Selected scoring category breakdown only. Round rankings combine every official scoring category; choose All categories to view them.'
    all_complete = bool(rows) and len(rows) == len(participants) and all(row['complete'] for row in rows)
    page['round_complete'] = all_complete and not ranking_mode
    page['scope_complete'] = all_complete and not ranking_mode and not selected
    scores = CriteriaScoreSubmission.objects.filter(
        event=event, assigned_round_id=round_id, category__in=categories,
        participant_id__in=participant_map, judge_id__in=[judge['id'] for judge in page['judges']],
        is_active=True, status__in=['SUBMITTED', 'NEEDS_REVIEW', 'VERIFIED', 'APPROVED'],
    ).select_related('criterion', 'category').prefetch_related('subcriterion_scores__subcriterion')
    score_map = {(row.participant_id, row.criterion_id, row.judge_id): row for row in scores
                 if row.criterion.category_id == row.category_id}
    required_ids = {criterion.pk for category in categories for criterion in category.criteria.all()}
    complete_sheets = {
        (participant_id, judge['id']) for participant_id in participant_map for judge in page['judges']
        if required_ids and all(
            (participant_id, criterion_id, judge['id']) in score_map
            and score_map[(participant_id, criterion_id, judge['id'])].status == 'APPROVED'
            for criterion_id in required_ids
        )
    }
    final_by_id = {row['participant_id']: row['final_score'] for row in rows}
    for participant in participants:
        details = []
        for category in visible_categories:
            for criterion in category.criteria.all():
                cells, values = [], []
                for judge in page['judges']:
                    score = score_map.get((participant.pk, criterion.pk, judge['id']))
                    status = score.get_status_display() if score else 'Pending'
                    if score and score.status == 'APPROVED' and (participant.pk, judge['id']) not in complete_sheets:
                        status = 'Approved / incomplete sheet'
                    cells.append({
                        'value': score.raw_score if score else None,
                        'status': status,
                        'subcriteria': [{'name': sub.subcriterion.name, 'value': sub.raw_score,
                                         'max_score': sub.subcriterion.max_score}
                                        for sub in score.subcriterion_scores.all()] if score else [],
                    })
                    if score and (participant.pk, judge['id']) in complete_sheets:
                        values.append(score.raw_score)
                average = sum(values, Decimal(0)) / len(values) if values else None
                contribution = None
                if average is not None and not ranking_mode:
                    try:
                        contribution = normalized_criterion_score(
                            average, criterion.max_score, criterion.weight_percent,
                        ) * category.overall_weight_percent / 100
                    except ValueError as exc:
                        page['error'] = str(exc)
                details.append({'name': criterion.name, 'category': category.name,
                                'category_weight': category.overall_weight_percent,
                                'weight': criterion.weight_percent, 'max_score': criterion.max_score,
                                'cells': cells, 'average': average, 'contribution': contribution})
        if details:
            page['breakdown_groups'].append({
                'name': getattr(participant, 'display_name', None) or participant.name,
                'number': getattr(participant, 'number', ''),
                'total': final_by_id.get(participant.pk) if not selected else None, 'rows': details,
            })


def _placement(rank):
    return {1: 'Champion', 2: '1st Runner-up', 3: '2nd Runner-up'}.get(rank, '')


def build_results_page(request, event, payload):
    generated = timezone.localtime(timezone.now(), ZoneInfo('Asia/Manila'))
    page = {
        'event_kind': payload['event_kind'], 'rounds': [], 'categories': [],
        'selected_round': '', 'round_name': '', 'selected_category': '', 'category_name': 'All categories',
        'live_rows': [], 'official_rows': [], 'breakdown_groups': [], 'judges': [], 'match_results': [],
        'weighted': True, 'scope_complete': False, 'is_published': False,
        'scope_notice': '', 'error': '', 'status_label': 'Awaiting Scores', 'status_key': 'warning',
        'status_message': 'Standings will appear once valid results are available.',
        'csv_url': '', 'print_url': '', 'default_tab': 'live',
        'csv_live_available': False, 'csv_official_available': False, 'csv_breakdown_available': False,
        'generated_at': generated.strftime('%b %d, %Y %I:%M %p') + ' (UTC+08:00)',
        'generated_at_iso': generated.isoformat(), 'school_name': 'EventTab', 'school_logo': '',
        'report_title': 'Results & Rankings',
    }
    from events.report_header import report_branding
    page['branding'] = report_branding()
    page['school_name'] = page['branding']['school_name']
    page['school_logo'] = page['branding']['left_logo']
    if not event:
        return page
    match = payload['event_kind'] == 'match'
    modern = not match and event.scoring_categories.exists()
    page['rounds'] = _round_options(event, modern, match)
    round_ids = {row['id'] for row in page['rounds']}
    requested_round = request.GET.get('round_id')
    page['selected_round'] = requested_round if requested_round is not None else (page['rounds'][0]['id'] if page['rounds'] else '')
    if page['selected_round'] not in round_ids:
        page['error'] = 'The selected round is not available for this event.'
        return page
    page['round_name'] = next(row['name'] for row in page['rounds'] if row['id'] == page['selected_round'])
    categories = []
    if modern:
        categories = list(event.scoring_categories.filter(
            assigned_round_id=page['selected_round'], purpose='official',
        ).prefetch_related('criteria'))
        page['categories'] = [{'id': str(row.pk), 'name': row.name} for row in categories]
    else:
        names = sorted({row.get('department') for row in payload['results'] if row.get('department') and row['department'] != '\u2014'})
        page['categories'] = [{'id': name, 'name': name} for name in names]
    selected = request.GET.get('category') or ''
    page['selected_category'] = selected
    if selected and selected not in {row['id'] for row in page['categories']}:
        page['error'] = 'The selected category is not available for this event.'
        return page
    if selected:
        page['category_name'] = next(row['name'] for row in page['categories'] if row['id'] == selected)
    page['judges'] = [{'id': judge.pk, 'name': judge.get_full_name() or judge.username}
                      for judge in event.assigned_judges.filter(is_active=True).order_by('username')]
    if modern:
        _modern_data(event, page, categories)
        # The legacy review checklist cannot verify normalized submissions.
        payload['can_confirm_tabulation'] = False
        round_published = bool(page.get('round_complete') and event.results_finalized
                               and event.publication_status == event.PUBLICATION_PUBLISHED)
        payload['verification'] = {
            'checks': [
                {'label': 'Round scoring approved', 'complete': page.get('round_complete', False),
                 'detail': 'All required participant and judge scores are approved.' if page.get('round_complete')
                           else 'Required scores are missing or awaiting approval.'},
                {'label': 'Event results finalized', 'complete': event.results_finalized},
                {'label': 'Results published', 'complete': round_published},
            ],
            'complete': round_published,
            'issues': [page['error']] if page['error'] else [],
        }
    elif match:
        rows = [dict(row, complete=True, has_score=bool(row.get('rank')),
                     status='Recorded', final_score=None) for row in payload['results']
                if not selected or row.get('department') == selected]
        page['match_results'] = payload['match_results']
        if page['selected_round']:
            allowed_ids = set(event.bracket_matches.filter(round_name=page['selected_round']).values_list('pk', flat=True))
            page['match_results'] = [row for row in page['match_results'] if row['id'] in allowed_ids]
            page['scope_notice'] = 'This view shows match results for the selected round. Event standings are available under Event standings.'
        else:
            page['live_rows'] = rows if payload['stats']['completed_matches'] else []
            page['scope_complete'] = bool(payload['stats']['match_count']) and payload['stats']['remaining_matches'] == 0
        if selected:
            allowed_ids = set(event.bracket_matches.filter(
                Q(team_a__department__name=selected) | Q(team_b__department__name=selected)
            ).values_list('pk', flat=True))
            page['match_results'] = [row for row in page['match_results'] if row['id'] in allowed_ids]
        payload['match_results'] = page['match_results']
    else:
        page['weighted'] = event.criteria_score_method == 'weighted_percentage'
        rows = [dict(row) for row in payload['results'] if not selected or row.get('department') == selected]
        locked = set(JudgeScore.objects.filter(
            candidate__event=event.judging_event, is_locked=True,
        ).values_list('candidate_id', 'criterion_id', 'judge_id')) if event.judging_event_id else set()
        required_criteria = list(event.judging_event.criteria.values_list('pk', flat=True)) if event.judging_event_id else []
        for row in rows:
            row['complete'] = bool(page['judges'] and required_criteria) and all(
                (row.get('candidate_id'), criterion_id, judge['id']) in locked
                for criterion_id in required_criteria for judge in page['judges']
            )
            row['status'] = 'Complete' if row['complete'] else 'Incomplete submissions'
            if not row['complete']:
                row['rank'] = None
                row['prize'] = ''
        multi = len(page['rounds']) > 1 or len(event.rounds_config or []) > 1
        if multi:
            page['scope_notice'] = 'These judging scores are not linked to individual rounds. Round-specific and overall standings are unavailable.'
            payload['can_confirm_tabulation'] = False
        else:
            page['live_rows'] = [row for row in rows if row.get('has_score')]
            page['scope_complete'] = bool(rows) and all(row['complete'] for row in rows)
            if event.judging_event_id:
                page['breakdown_groups'] = _legacy_breakdown(event, rows, page['judges'])
    page['is_published'] = bool(
        event.results_finalized and event.publication_status == event.PUBLICATION_PUBLISHED
        and page['scope_complete'] and not page['error']
    )
    round_config = next((row for index, row in enumerate(event.rounds_config or [])
                         if isinstance(row, dict)
                         and str(row.get('id') or row.get('stage_number') or index + 1) == page['selected_round']), {})
    if round_config.get('hide_standings') and not page['is_published']:
        page['live_rows'] = []
        page['scope_notice'] = 'Standings are hidden by the selected round configuration until results are published.'
    if page['is_published']:
        page['official_rows'] = page['live_rows']
        page.update(status_label='Published', status_key='success',
                    status_message='Official results have been verified and published.')
    elif event.results_finalized and page['scope_complete']:
        page.update(status_label='Pending Publication', status_message='This selection has no verified, published standings available.')
    elif modern and selected and page.get('round_complete') and event.results_finalized:
        if event.publication_status == event.PUBLICATION_PUBLISHED:
            page.update(status_label='Published (round)', status_key='success',
                        status_message='Showing category scores. Choose All categories to view official round standings.')
        else:
            page.update(status_label='Pending Publication', status_message='Round scores are approved and awaiting publication.')
    elif page['live_rows'] or any(cell['value'] is not None for group in page['breakdown_groups']
                                 for row in group['rows'] for cell in row['cells']):
        page.update(status_label='Pending Verification', status_message='Scores have been submitted and are awaiting verification.')
    elif event.status == event.STATUS_ACTIVE:
        page.update(status_label='In Progress', status_key='info')
    elif event.status == event.STATUS_UPCOMING:
        page.update(status_label='Upcoming', status_key='secondary')
    default = 'official' if page['is_published'] else 'live'
    tab = request.GET.get('tab') or default
    # Keep old bookmarked result-section names meaningful.
    tab = {'overview': default, 'standings': default, 'submissions': 'breakdown', 'calculation': 'breakdown'}.get(tab, tab)
    page['default_tab'] = tab if tab in {'live', 'official', 'breakdown'} else default
    page['report_title'] = {'live': 'Live Rankings (Provisional)', 'official': 'Official Results',
                            'breakdown': 'Detailed Score Breakdown'}[page['default_tab']]
    query = {'event_id': event.pk, 'round_id': page['selected_round'], 'category': selected, 'tab': page['default_tab']}
    if not page['error']:
        page['print_url'] = reverse('tabulator_results') + '?' + urlencode(dict(query, print='1'))
        page['csv_live_available'] = bool(page['live_rows'])
        page['csv_official_available'] = bool(page['official_rows'])
        page['csv_breakdown_available'] = bool(page['breakdown_groups'] or page['match_results'])
        page['csv_url'] = reverse('tabulator_results_csv') + '?' + urlencode(query)
    return page
