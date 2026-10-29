"""Local workflow gates; never contact SSH or modify the repository index."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import deploy_workflow as workflow


class WorkflowTests(unittest.TestCase):
    def test_ordinary_code_ui_and_mobile_changes_require_regressions_not_reactivation(self):
        before = {'requirements.txt': 'same', 'core/views.py': 'old', 'frontend/page.html': 'old', 'events/mobile_views.py': 'old'}
        after = {**before, 'core/views.py': 'new', 'frontend/page.html': 'new', 'events/mobile_views.py': 'new'}
        self.assertEqual(workflow.classify(before, after), [])

    def test_dependency_schema_settings_and_tooling_changes_block(self):
        for name in ['requirements.txt', 'events/models.py', 'events/migrations/0045_new.py',
                     'core/settings.py', 'execution/deploy_public.py', 'deploy.ps1']:
            with self.subTest(name=name):
                self.assertTrue(workflow.classify({name: 'old'}, {name: 'new'}))

    def test_runtime_additions_and_removals_block(self):
        self.assertTrue(workflow.classify({}, {'frontend/new.css': 'new'}))
        self.assertTrue(workflow.classify({'core/views.py': 'old'}, {}))

    def test_no_activation_is_local_only(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(workflow, 'git') as git:
            self.assertFalse(workflow.check(Path(temporary))['ready'])
            git.assert_not_called()

    def test_pending_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = root / '.tmp/deploy/activation.json'
            receipt.parent.mkdir(parents=True)
            receipt.write_text(json.dumps({'format': 1, 'activated_commit': 'a' * 40,
                'production_write_approval_reference': 'PENDING', 'backup_verification_reference': 'receipt',
                'publication_integration_reference': 'evidence'}))
            with self.assertRaises(ValueError):
                workflow.check(root)

    def test_status_and_recovery_do_not_require_clean_local_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = root / '.tmp/deploy/activation.json'
            receipt.parent.mkdir(parents=True)
            receipt.write_text(json.dumps({'format': 1, 'activated_commit': 'a' * 40,
                'production_write_approval_reference': 'operator-reference', 'backup_verification_reference': 'backup-reference',
                'publication_integration_reference': 'test-reference'}))
            with patch.object(workflow, 'git') as git:
                self.assertTrue(workflow.check(root, recovery=True)['ready'])
                git.assert_not_called()

    def test_clean_descendant_with_only_ordinary_edits_is_ready(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = root / '.tmp/deploy/activation.json'
            receipt.parent.mkdir(parents=True)
            receipt.write_text(json.dumps({'format': 1, 'activated_commit': 'a' * 40,
                'production_write_approval_reference': 'operator-reference', 'backup_verification_reference': 'backup-reference',
                'publication_integration_reference': 'test-reference'}))
            with patch.object(workflow.subprocess, 'run') as ancestry, patch.object(workflow, 'git', side_effect=['', 'b' * 40]), \
                 patch.object(workflow, 'tree', side_effect=[{'core/views.py': 'old'}, {'core/views.py': 'new'}]):
                self.assertTrue(workflow.check(root)['ready'])
                ancestry.assert_called_once()

    def test_dirty_activated_source_blocks_before_packaging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = root / '.tmp/deploy/activation.json'
            receipt.parent.mkdir(parents=True)
            receipt.write_text(json.dumps({'format': 1, 'activated_commit': 'a' * 40,
                'production_write_approval_reference': 'operator-reference', 'backup_verification_reference': 'backup-reference',
                'publication_integration_reference': 'test-reference'}))
            with patch.object(workflow.subprocess, 'run'), patch.object(workflow, 'git', return_value=' M core/views.py'):
                with self.assertRaises(ValueError):
                    workflow.check(root)
