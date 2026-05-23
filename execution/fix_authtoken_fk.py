"""
Point authtoken_token.user_id at auth_user (Django default) instead of accounts_user.

Run when the DB was previously used with the eventtab mobile backend custom user model:
  python execution/fix_authtoken_fk.py
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.db import connection


def main():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'authtoken_token'::regclass AND contype = 'f'
            """
        )
        constraints = [row[0] for row in cursor.fetchall()]
        print('Existing FK constraints on authtoken_token:', constraints)

        for name in constraints:
            cursor.execute(f'ALTER TABLE authtoken_token DROP CONSTRAINT IF EXISTS "{name}"')

        cursor.execute('DELETE FROM authtoken_token')

        cursor.execute(
            """
            ALTER TABLE authtoken_token
            ADD CONSTRAINT authtoken_token_user_id_auth_user_fk
            FOREIGN KEY (user_id) REFERENCES auth_user(id) ON DELETE CASCADE
            """
        )

    print('authtoken_token now references auth_user. Old tokens were cleared.')


if __name__ == '__main__':
    main()
