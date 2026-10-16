"""Read schema metadata only. No repair, migration, credential or score output."""
import json

from django.core.management.base import BaseCommand
from django.db import connections, models, transaction

from events.models import (AuditLog, CriteriaScoreSubmission, CriteriaSubcriterionScore,
                           JudgeActivityLog, JudgeScore, JudgeSubcriterionScore)


class Command(BaseCommand):
    help = 'Read-only schema comparison for mobile submission tables; never repairs data or constraints.'

    def add_arguments(self, parser):
        parser.add_argument('--database', default='default')

    def handle(self, *args, **options):
        alias = options['database']
        connection = connections[alias]
        reports = []
        with transaction.atomic(using=alias):
            with connection.cursor() as cursor:
                if connection.vendor == 'postgresql':
                    cursor.execute('SET TRANSACTION READ ONLY')
                tables = set(connection.introspection.table_names(cursor))
                for model in (JudgeScore, CriteriaScoreSubmission, JudgeActivityLog, AuditLog,
                              JudgeSubcriterionScore, CriteriaSubcriterionScore):
                    table = model._meta.db_table
                    issues = []
                    if table not in tables:
                        reports.append({'table': table, 'issues': [{'kind': 'missing_table'}]})
                        continue
                    columns = {column.name: column for column in connection.introspection.get_table_description(cursor, table)}
                    constraints = connection.introspection.get_constraints(cursor, table)
                    fields = {field.column: field for field in model._meta.local_fields}
                    for name, field in fields.items():
                        if name not in columns:
                            issues.append({'kind': 'missing_column', 'column': name})
                            continue
                        if field.is_relation:
                            expected = (field.remote_field.model._meta.db_table, field.target_field.column)
                            actual = [(key, tuple(value['foreign_key'])) for key, value in constraints.items()
                                      if value.get('foreign_key') and value.get('columns') == [name]]
                            if not actual or any(target != expected for _, target in actual):
                                issues.append({'kind': 'foreign_key_mismatch', 'column': name,
                                               'expected': expected, 'actual': actual})
                        if isinstance(field, models.DecimalField):
                            precision, scale = columns[name].precision, columns[name].scale
                            if precision is not None and (precision < field.max_digits or scale != field.decimal_places):
                                issues.append({'kind': 'decimal_definition_mismatch', 'column': name,
                                    'expected': [field.max_digits, field.decimal_places], 'actual': [precision, scale]})
                    if connection.vendor == 'postgresql':
                        cursor.execute('''SELECT column_name FROM information_schema.columns
                            WHERE table_schema = current_schema() AND table_name = %s
                              AND is_nullable = 'NO' AND column_default IS NULL AND is_identity = 'NO' ''', [table])
                        for (name,) in cursor.fetchall():
                            if name not in fields:
                                issues.append({'kind': 'extra_required_column_without_default', 'column': name})
                    for constraint in model._meta.constraints:
                        if constraint.name not in constraints:
                            issues.append({'kind': 'missing_named_constraint', 'constraint': constraint.name})
                    reports.append({'table': table, 'issues': issues})
        self.stdout.write(json.dumps({'read_only': True, 'ok': not any(row['issues'] for row in reports),
                                      'tables': reports}, indent=2))
