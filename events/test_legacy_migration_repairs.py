"""Real PostgreSQL migration graph/SQL tests in disposable schemas only."""
import importlib
from uuid import uuid4

from django.db import connection, transaction, ProgrammingError
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

TEAM = importlib.import_module('events.migrations.0019_align_team_schema')
MATCH = importlib.import_module('events.migrations.0020_team_delete_set_null_on_legacy_match')
REPLACEMENT = ('events', '0018_squashed_0020_fresh_schema')
BASE = ('events', '0017_assignment_account_profile')
LAST = ('events', '0044_additional_report_header_logos')


class OriginalReleaseLoader(MigrationLoader):
    """Simulate the released migration directory before the additive file existed."""
    def load_disk(self):
        super().load_disk()
        self.disk_migrations.pop(REPLACEMENT)


class LegacyMigrationRepairTests(TestCase):
    def schema(self):
        name = 'migration_review_' + uuid4().hex
        with connection.cursor() as cursor:
            cursor.execute('CREATE SCHEMA ' + connection.ops.quote_name(name))
            cursor.execute('SET LOCAL search_path TO ' + connection.ops.quote_name(name))
        return name

    def executor(self, original=False):
        executor = MigrationExecutor(connection)
        if original:
            executor.loader = OriginalReleaseLoader(connection)
        return executor

    def migrate(self, target, original=False):
        # TestCase wraps fixture inserts and DDL in one transaction. Flush
        # deferred FK checks as a real pre-migration commit would have done.
        with connection.cursor() as cursor:
            cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        executor = self.executor(original)
        executor.migrate([target] if target else executor.loader.graph.leaf_nodes())
        with connection.cursor() as cursor:
            cursor.execute('SET CONSTRAINTS ALL DEFERRED')
        return executor

    def snapshot(self):
        with connection.cursor() as cursor:
            cursor.execute("""SELECT c.relname, a.attname, format_type(a.atttypid,a.atttypmod), a.attnotnull
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_attribute a ON a.attrelid=c.oid
                WHERE n.nspname=current_schema() AND a.attnum>0 AND NOT a.attisdropped
                AND c.relkind='r' ORDER BY c.relname,a.attnum""")
            columns = cursor.fetchall()
            cursor.execute("""SELECT c.relname, k.conname, k.contype, pg_get_constraintdef(k.oid),
                k.condeferrable, k.condeferred, k.confdeltype
                FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=current_schema() ORDER BY c.relname,k.conname""")
            constraints = cursor.fetchall()
            cursor.execute('SELECT app, name, applied FROM django_migrations ORDER BY app,name')
            history = cursor.fetchall()
        return columns, constraints, history

    def legacy(self):
        self.schema()
        self.migrate(BASE, original=True)
        with connection.cursor() as cursor:
            cursor.execute("""CREATE TABLE events_team (id bigint PRIMARY KEY, name varchar(200),
                abbreviation varchar(50) UNIQUE, logo_icon varchar(50), color varchar(20),
                description text, created_at timestamptz, updated_at timestamptz)""")
            cursor.execute("""INSERT INTO events_team (id,name,abbreviation,logo_icon,color,description)
                VALUES (1,'Legacy A','A','old-logo','red','old-description'),
                       (2,'Legacy B','B','old-logo','blue','retained team')""")
            cursor.execute("""CREATE TABLE events_match (id bigint PRIMARY KEY,
                team_a_id bigint NOT NULL, team_b_id bigint NOT NULL,
                CONSTRAINT events_match_team_a_id_d6c91033_fk_events_team_id
                    FOREIGN KEY (team_a_id) REFERENCES events_team(id) DEFERRABLE INITIALLY DEFERRED,
                CONSTRAINT events_match_team_b_id_b9064376_fk_events_team_id
                    FOREIGN KEY (team_b_id) REFERENCES events_team(id) DEFERRABLE INITIALLY DEFERRED)""")
            cursor.execute('INSERT INTO events_match VALUES (1,1,2)')
        # Explicit historical fixture: 0018 was adopted over pre-existing legacy
        # tables. Only this synthetic schema receives a recorded fixture row.
        MigrationRecorder(connection).record_applied('events', '0018_team_model')

    def test_fresh_replacement_forward_reverse_and_replay(self):
        self.schema()
        base = self.migrate(BASE).loader.project_state([BASE])
        department = base.apps.get_model('events', 'Department').objects.create(name='Retained department', code='KEEP')
        original_state = self.executor(original=True).loader.project_state([('events', '0020_team_delete_set_null_on_legacy_match')])
        replacement_state = self.executor().loader.project_state([REPLACEMENT])
        self.assertEqual(original_state.models, replacement_state.models)
        state = self.migrate(REPLACEMENT).loader.project_state([REPLACEMENT])
        state.apps.get_model('events', 'Team').objects.create(name='Fresh team', code='FRESH', department_id=department.pk)
        with connection.cursor() as cursor:
            columns = {c.name for c in connection.introspection.get_table_description(cursor, 'events_team')}
            self.assertIn('code', columns); self.assertNotIn('abbreviation', columns)
            self.assertNotIn('events_match', connection.introspection.table_names(cursor))
            constraints = connection.introspection.get_constraints(cursor, 'events_team')
            self.assertTrue(any(c['unique'] and c['columns'] == ['code'] for c in constraints.values()))
            self.assertTrue(any(c['foreign_key'] == ('events_department', 'id') for c in constraints.values()))
        # Original 0019 really fails on this schema; it is not elided/faked.
        with self.assertRaises(ProgrammingError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(TEAM.FORWARD_SQL)
        self.migrate(LAST)
        self.migrate(BASE)
        self.assertNotIn('events_team', connection.introspection.table_names())
        self.assertEqual(base.apps.get_model('events', 'Department').objects.get(pk=department.pk).name, 'Retained department')
        self.migrate(LAST)
        self.assertEqual(self.executor().migration_plan([LAST]), [])

    def test_partial_legacy_original_forward_and_reverse_preserve_known_semantics(self):
        self.legacy()
        # Partial old history must use the ORIGINAL migrations, not CreateModel.
        plan = self.executor().migration_plan([('events', '0020_team_delete_set_null_on_legacy_match')])
        self.assertEqual([m.name for m, backwards in plan],
            ['0019_align_team_schema', '0020_team_delete_set_null_on_legacy_match'])
        self.migrate(('events', '0020_team_delete_set_null_on_legacy_match'), original=True)
        with connection.cursor() as cursor:
            cursor.execute('SELECT id,code,department_id,members,coach,status FROM events_team ORDER BY id')
            self.assertEqual(cursor.fetchall(), [(1,'A',None,'','','active'), (2,'B',None,'','','active')])
            cursor.execute("""SELECT k.conname, k.confdeltype, k.condeferrable, k.condeferred,
                k.conrelid='events_match'::regclass, k.confrelid='events_team'::regclass
                FROM pg_constraint k WHERE k.conrelid='events_match'::regclass AND k.contype='f'
                ORDER BY k.conname""")
            rows = cursor.fetchall()
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row[1:] == ('n',True,True,True,True) for row in rows))
            cursor.execute('DELETE FROM events_team WHERE id=1')
            cursor.execute('SELECT id,team_a_id,team_b_id FROM events_match')
            self.assertEqual(cursor.fetchall(), [(1,None,2)])
        self.migrate(('events', '0018_team_model'), original=True)
        with connection.cursor() as cursor:
            cursor.execute('SELECT id,abbreviation,logo_icon,color,description FROM events_team')
            self.assertEqual(cursor.fetchall(), [(2,'B','A','#00C5D9','')])
            cursor.execute("""SELECT k.conname,k.confdeltype FROM pg_constraint k
                WHERE k.conrelid='events_match'::regclass AND k.contype='f' ORDER BY k.conname""")
            # Historical reverse is asymmetric and data-losing. Document it;
            # never substitute it for an application/database rollback.
            self.assertEqual([row[1] for row in cursor.fetchall()], ['a', 'n'])
            columns = connection.introspection.get_table_description(cursor, 'events_match')
            self.assertTrue(all(c.null_ok for c in columns if c.name in {'team_a_id','team_b_id'}))

    def test_completed_0044_legacy_history_is_read_only_with_replacement(self):
        self.legacy()
        self.migrate(None, original=True)
        before = self.snapshot()
        with connection.cursor() as cursor:
            cursor.execute('SELECT * FROM events_team ORDER BY id'); team_rows = cursor.fetchall()
            cursor.execute('SELECT * FROM events_match ORDER BY id'); match_rows = cursor.fetchall()
        self.assertNotIn(REPLACEMENT, MigrationRecorder(connection).applied_migrations())
        with CaptureQueriesContext(connection) as queries:
            executor = self.executor()
            executor.loader.check_consistent_history(connection)
            self.assertEqual(executor.migration_plan(executor.loader.graph.leaf_nodes()), [])
            self.assertIn(REPLACEMENT, executor.loader.applied_migrations)
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))
        self.assertEqual(self.snapshot(), before)
        with connection.cursor() as cursor:
            cursor.execute('SELECT * FROM events_team ORDER BY id'); self.assertEqual(cursor.fetchall(), team_rows)
            cursor.execute('SELECT * FROM events_match ORDER BY id'); self.assertEqual(cursor.fetchall(), match_rows)
        self.assertNotIn(REPLACEMENT, MigrationRecorder(connection).applied_migrations())
