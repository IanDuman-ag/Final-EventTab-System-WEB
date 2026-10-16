"""Approval-only checks: no database setup, score fixtures, reset or apply."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, Mock

from django.test import SimpleTestCase
from django.utils import timezone

from . import vocal_duet_owner_evidence as evidence
from . import vocal_duet_simple as simple
from . import vocal_duet_development as dev
from . import test_vocal_duet_simple as fixtures
from .vocal_duet_reset import ResetError, _digest


class EvidenceRulesTests(SimpleTestCase):
    def test_confirmation_variants_and_fake_references_are_rejected(self):
        for value in (simple.CONFIRM, simple.CONFIRM.lower(), 'RESET   ONLY VOCAL DUET TEST SCORES 175–190',
                'Kamatera record: ' + simple.CONFIRM, evidence.DEVELOPMENT_CONFIRMATION,
                'yes', '', None, 'This is a sufficiently long confirmation without a source', 'Kamatera console\x00 record 123'):
            self.assertIsNotNone(evidence.evidence_error(value), repr(value))

    def test_actual_references_require_separate_usage_confirmation(self):
        reference = 'Kamatera Console > Servers > eventtab-server > Overview IP 103.125.217.125; History creation 2026-10-14 UTC'
        self.assertIsNone(evidence.evidence_error(reference))
        approval = {'owner_evidence_reference': reference}
        self.assertIsNotNone(evidence.approval_evidence_error(approval))
        approval.update(evidence_version=2, development_use_confirmed=True)
        self.assertIsNone(evidence.approval_evidence_error(approval))


class OwnerEvidenceWorkflowTests(SimpleTestCase):
    def setUp(self):
        fixtures.OwnerApprovalDiagnosticTests.setUp(self)
        self.previous = {'sha256': '9' * 64, 'rejection': None, 'approval': {}}

    def test_existing_approval_cannot_be_silently_overwritten(self):
        with patch.object(self.helper, 'secure_read', side_effect=self.read), \
                patch.object(self.helper, 'current_approval', return_value=self.previous), \
                patch('builtins.input') as prompt, patch.object(self.helper, 'publish') as publish:
            with self.assertRaisesMessage(ValueError, 'reject action first'):
                self.helper.approve(self.facts, self.now)
        prompt.assert_not_called(); publish.assert_not_called()

    read = fixtures.OwnerApprovalDiagnosticTests.read

    def test_phrase_as_evidence_fails_before_usage_or_reset_confirmation(self):
        with patch.object(self.helper, 'secure_read', side_effect=self.read), \
                patch('builtins.input', return_value=simple.CONFIRM) as prompt, patch('builtins.print'), \
                patch.object(self.helper, 'publish') as publish:
            with self.assertRaisesMessage(ValueError, 'not an evidence reference'):
                self.helper.approve(self.facts, self.now)
        self.assertEqual(prompt.call_count, 1); publish.assert_not_called()

    def test_reapproval_links_exact_rejected_predecessor_and_unchanged_checkpoint(self):
        previous = {**self.previous, 'rejection': {'purpose': 'test'}, 'rejection_sha256': '8' * 64}
        before = deepcopy(self.checkpoint)
        with patch.object(self.helper, 'secure_read', side_effect=self.read), \
                patch.object(self.helper, 'current_approval', return_value=previous), \
                patch('builtins.input', side_effect=['TEST Kamatera console ownership record', evidence.DEVELOPMENT_CONFIRMATION, simple.CONFIRM]), \
                patch('builtins.print'), patch.object(self.helper, 'publish') as publish:
            self.helper.approve(self.facts, self.now)
        approval = publish.call_args.args[1]
        self.assertEqual(approval['supersedes_approval_sha256'], previous['sha256'])
        self.assertEqual(approval['rejection_record_sha256'], previous['rejection_sha256'])
        self.assertEqual(approval['checkpoint_sha256'], _digest(before))
        self.assertEqual(self.checkpoint, before)
        self.assertIsNone(evidence.approval_evidence_error(approval))

    def test_expired_checkpoint_cannot_be_reapproved_after_rejection(self):
        self.checkpoint['verified_at'] = (self.now - timedelta(hours=3)).isoformat()
        with patch.object(self.helper, 'secure_read', side_effect=self.read), \
                patch.object(self.helper, 'publish') as publish:
            with self.assertRaisesMessage(ValueError, 'checkpoint.verified_at'):
                self.helper.approve(self.facts, self.now)
        publish.assert_not_called()

    def test_django_rejects_bad_or_revoked_evidence_without_querying_scores(self):
        now = timezone.now()
        approval = {'purpose': 'eventtab-simple-owner-vocal-duet-test-approval-v1',
            'checkpoint_sha256': _digest(self.checkpoint), 'target': self.checkpoint['target'],
            'backup_sha256': self.checkpoint['backup']['backup_sha256'], 'confirmed': simple.CONFIRM,
            'owner_evidence_reference': simple.CONFIRM, 'issued_at': now.isoformat(),
            'expires_at': (now + timedelta(hours=1)).isoformat(), 'evidence_version': 2, 'development_use_confirmed': True}
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'approval.json'
            path.write_text(json.dumps(approval))
            with patch.object(simple, 'OWNER_APPROVAL', path), patch.object(simple, 'actor', return_value=object()), \
                    patch.object(dev, 'load_authorization', side_effect=ResetError('TEST no signed permit')), \
                    patch.object(dev, '_root_controlled', return_value=None), \
                    patch.object(simple.models.AuditLog.objects, 'filter', return_value=Mock(exists=lambda: False)) as audits:
                with self.assertRaises(ResetError): simple.authorization(self.checkpoint)
                audits.assert_not_called()
                approval['owner_evidence_reference'] = 'TEST Kamatera console actual ownership record'
                path.write_text(json.dumps(approval))
                old_sha = hashlib.sha256(path.read_bytes()).hexdigest()
                (path.parent / ('owner-rejected-' + old_sha + '.json')).write_text('TEST denial marker')
                with self.assertRaises(ResetError): simple.authorization(self.checkpoint)
                audits.assert_not_called()
