"""Submission receipts and deliberately redacted, opt-in failure diagnostics."""
from functools import wraps
import logging
import os
from pathlib import Path
import re
import traceback
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404
from rest_framework.exceptions import APIException
from rest_framework.response import Response

from .mobile_scoring_compatibility import effective_weight
from .models import CriteriaScoreSubmission


logger = logging.getLogger('events.mobile_submit')


def _identifier(value):
    return re.sub(r'[^a-zA-Z0-9_.-]', '_', str(value or ''))[:160]


def submit_errors(view):
    """Outside atomic: rollback first, then return JSON without exception details."""
    @wraps(view)
    def wrapped(self, request, *args, **kwargs):
        try:
            return view(self, request, *args, **kwargs)
        except (APIException, Http404, PermissionDenied):
            raise
        except Exception as exc:
            reference = uuid.uuid4().hex
            if (getattr(settings, 'MOBILE_SCORE_DIAGNOSTICS', False)
                    or os.environ.get('EVENTTAB_SCORE_DIAGNOSTICS') == '1'):
                cause = exc.__cause__ or exc
                diag = getattr(cause, 'diag', None)
                # Do NOT use logger.exception, str(exc), SQL, locals, request
                # headers or request bodies: PostgreSQL DETAIL can contain data.
                frames = [{'file': Path(frame.filename).name, 'function': frame.name, 'line': frame.lineno}
                          for frame in traceback.extract_tb(exc.__traceback__)]
                logger.error('mobile_submit_failure %s', {
                    'reference': reference, 'stage': getattr(request, '_score_stage', 'entry'),
                    'exception': type(exc).__name__, 'cause': type(cause).__name__,
                    'sqlstate': _identifier(getattr(cause, 'pgcode', None)),
                    'constraint': _identifier(getattr(diag, 'constraint_name', None)),
                    'table': _identifier(getattr(diag, 'table_name', None)),
                    'column': _identifier(getattr(diag, 'column_name', None)), 'frames': frames,
                })
            return Response({'detail': 'Scores could not be saved. Please retry or contact support with this reference.',
                             'reference': reference}, status=500)
    return wrapped


def receipt(scores, scoped):
    total, breakdown = 0, []
    for score in scores:
        weight = effective_weight(scoped['normalized'][score.criterion_id]) if scoped else score.criterion.weight_percent
        weighted = float(score.score) * float(weight) / float(score.criterion.max_score)
        total += weighted
        breakdown.append({'criterion': score.criterion.name, 'score': float(score.score),
            'max_score': float(score.criterion.max_score), 'weight': float(weight), 'weighted_score': round(weighted, 2),
            'subcriteria': [{'id': row.subcriterion_id, 'name': row.subcriterion.name,
                            'score': float(row.score), 'max_score': float(row.subcriterion.max_score)}
                           for row in score.subcriterion_scores.select_related('subcriterion').all()]})
    method = scoped['event'].criteria_score_method if scoped else 'weighted_percentage'
    if method in {'raw_score', 'average_score'}:
        total = sum(float(score.score) for score in scores)
        if method == 'average_score':
            total /= len(scores)
    return {'verification_id': scores[0].verification_id, 'submitted_at': scores[0].submitted_at.isoformat(),
            'total_score': round(total, 1), 'breakdown': breakdown, 'is_locked': True}


def matching_receipt(scores, prepared, scoped, user, candidate):
    """Acknowledge an exact committed retry; never update a previous submission."""
    by_id = {row.criterion_id: row for row in scores}
    expected = {criterion.pk: value for criterion, value, _ in prepared}
    if not scores or set(by_id) != set(expected):
        return None
    verification_ids = {row.verification_id for row in scores}
    stamps = {row.submitted_at for row in scores}
    if len(verification_ids) != 1 or '' in verification_ids or len(stamps) != 1 or None in stamps:
        return None
    for criterion, value, children in prepared:
        row = by_id[criterion.pk]
        if not row.is_locked or row.score != value:
            return None
        stored = {item.subcriterion_id: item.score for item in row.subcriterion_scores.all()}
        supplied = {int(item['subcriterion_id']): Decimal(str(item['score'])) for item in children or []}
        if stored != supplied:
            return None
    if scoped:
        normalized = list(CriteriaScoreSubmission.objects.filter(
            event=scoped['event'], assigned_round_id=scoped['round_id'], judge=user,
            participant_id=scoped['candidate_entries'][candidate.pk], is_active=True,
            source='MOBILE', status__in=['SUBMITTED', 'VERIFIED', 'APPROVED', 'NEEDS_REVIEW']))
        targets = {definition.pk: expected[mid] for mid, definition in scoped['normalized'].items()}
        if (len(normalized) != len(targets) or {row.criterion_id: row.raw_score for row in normalized} != targets
                or any(row.submission_key != scores[0].verification_id or row.submitted_at != scores[0].submitted_at
                       for row in normalized)):
            return None
    return receipt([by_id[criterion.pk] for criterion, _, _ in prepared], scoped)
