"""
Point judging M2M and related FKs at auth_user instead of accounts_user.

Run once:
  python execution/fix_judging_judge_fk.py
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.db import connection


def repoint_fk(table_name, column_name='user_id'):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = %s::regclass AND contype = 'f'
            """,
            [table_name],
        )
        constraints = [row[0] for row in cursor.fetchall()]
        print(f'{table_name}: {constraints}')
        for name in constraints:
            if 'accounts' in name:
                cursor.execute(f'ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS "{name}"')
        cursor.execute(
            f"""
            ALTER TABLE {table_name}
            ADD CONSTRAINT {table_name}_{column_name}_auth_fk
            FOREIGN KEY ({column_name}) REFERENCES auth_user(id) ON DELETE CASCADE
            """
        )


def main():
    repoint_fk('events_judgingevent_assigned_judges', 'user_id')
    repoint_fk('events_judgescore', 'judge_id')
    print('Judging user FKs now reference auth_user.')


if __name__ == '__main__':
    main()
