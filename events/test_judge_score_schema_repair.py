"""Native PostgreSQL drift reproduction; never runs against production."""
from pathlib import Path
from unittest import skipUnless

from django.db import connection, IntegrityError, transaction
from django.test import TransactionTestCase

from . import test_mobile_submit as fixtures
from .models import JudgeScore, CriteriaScoreSubmission


@skipUnless(connection.vendor == 'postgresql', 'Requires disposable PostgreSQL')
class JudgeScoreSchemaRepairTests(TransactionTestCase):
    def setUp(self):
        fixtures.MobileSubmitTests.setUp(self)
        with connection.cursor() as cursor:
            cursor.execute("""SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid = 'events_judgescore'::regclass AND contype = 'f'
                AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
                    WHERE attrelid = 'events_judgescore'::regclass AND attname = 'judge_id')]""")
            self.foreign_keys = cursor.fetchall()
            for name, _ in self.foreign_keys:
                cursor.execute('ALTER TABLE events_judgescore DROP CONSTRAINT ' + connection.ops.quote_name(name))
            cursor.execute("ALTER TABLE events_judgescore ADD COLUMN review_note varchar(255) NOT NULL DEFAULT 'historical-note'")
            cursor.execute('ALTER TABLE events_judgescore ALTER COLUMN review_note DROP DEFAULT')
            cursor.execute("ALTER TABLE events_judgescore ADD COLUMN approval_status varchar(20) NOT NULL DEFAULT 'approved'")
            cursor.execute('ALTER TABLE events_judgescore ALTER COLUMN approval_status DROP DEFAULT')
        self.historical = self.snapshot()

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute('ALTER TABLE events_judgescore DROP COLUMN review_note, DROP COLUMN approval_status')
            for name, definition in self.foreign_keys:
                cursor.execute('ALTER TABLE events_judgescore ADD CONSTRAINT ' + connection.ops.quote_name(name) + ' ' + definition)
        super().tearDown()

    def snapshot(self):
        with connection.cursor() as cursor:
            cursor.execute('SELECT to_jsonb(s) FROM events_judgescore s WHERE judge_id <> 57 ORDER BY id')
            return cursor.fetchall()

    def repair(self, mode='apply', dry_run=False):
        body = (Path(__file__).resolve().parents[1] / 'execution/sql/judge_score_review_defaults_body.sql').read_text()
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL eventtab.review_defaults_mode = %s", [mode])
                cursor.execute(body)
            if dry_run:
                transaction.set_rollback(True)

    def assert_insert_failure(self, column):
        with self.assertRaises(IntegrityError) as raised:
            JudgeScore.objects.create(judge_id=57, candidate=self.candidates[0], criterion=self.criteria[0], score=80)
        cause = raised.exception.__cause__
        self.assertEqual(getattr(cause, 'sqlstate', None) or cause.pgcode, '23502')
        self.assertEqual(cause.diag.column_name, column)
        self.assertEqual(self.snapshot(), self.historical)

    def test_actual_omitted_columns_raise_two_independent_not_null_violations(self):
        self.assert_insert_failure('review_note')
        with self.settings(MOBILE_SCORE_DIAGNOSTICS=True), self.assertLogs('events.mobile_submit', level='ERROR') as logs:
            response = self.api.post(self.url, self.payload, format='json')
        self.assertEqual(response.status_code, 500)
        self.assertIn('23502', logs.output[0])
        self.assertIn('legacy_score_write', logs.output[0])
        self.assertFalse(JudgeScore.objects.filter(judge_id=57).exists())
        self.assertFalse(CriteriaScoreSubmission.objects.exists())
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE events_judgescore ALTER COLUMN review_note SET DEFAULT ''")
        self.assert_insert_failure('approval_status')

    def test_dry_run_apply_retry_and_rollback_preserve_every_existing_value(self):
        self.repair(dry_run=True)
        self.assert_insert_failure('review_note')
        self.repair()
        first = self.api.post(self.url, self.payload, format='json')
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(self.api.post(self.url, self.payload, format='json').json(), first.json())
        self.assertEqual(JudgeScore.objects.count(), 65)
        self.assertEqual(CriteriaScoreSubmission.objects.count(), 5)
        self.assertEqual(self.snapshot(), self.historical)
        with connection.cursor() as cursor:
            cursor.execute('SELECT DISTINCT review_note, approval_status FROM events_judgescore WHERE judge_id = 57')
            self.assertEqual(cursor.fetchall(), [('', 'pending')])
            cursor.execute("SELECT count(*) FROM pg_constraint WHERE conrelid='events_judgescore'::regclass AND contype='f' AND conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid='events_judgescore'::regclass AND attname='judge_id')]")
            self.assertEqual(cursor.fetchone()[0], 0)
        self.repair(mode='rollback')
        self.assertEqual(JudgeScore.objects.count(), 65)
        self.assertEqual(self.snapshot(), self.historical)
        self.payload['candidate_id'] = self.candidates[1].pk
        self.assertEqual(self.api.post(self.url, self.payload, format='json').status_code, 500)
        self.assertEqual(JudgeScore.objects.count(), 65)

    def test_unexpected_default_aborts_without_replacing_it(self):
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE events_judgescore ALTER COLUMN approval_status SET DEFAULT 'approved'")
        with self.assertRaisesMessage(Exception, 'Unexpected existing default'):
            self.repair()
        self.assertEqual(self.snapshot(), self.historical)
        self.assert_insert_failure('review_note')

    def test_unknown_status_check_requires_manual_review(self):
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE events_judgescore ADD CONSTRAINT test_review_status CHECK (approval_status = 'approved')")
        try:
            with self.assertRaisesMessage(Exception, 'Check constraint involves approval_status'):
                self.repair()
            self.assertEqual(self.snapshot(), self.historical)
            self.assert_insert_failure('review_note')
        finally:
            with connection.cursor() as cursor:
                cursor.execute('ALTER TABLE events_judgescore DROP CONSTRAINT test_review_status')
