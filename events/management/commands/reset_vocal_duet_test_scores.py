"""Preview by default; explicit operator-only transaction for approved test rows."""
import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction

from events.vocal_duet_reset import ResetError, apply_reset, reset_plan


class Command(BaseCommand):
    help = 'Preview or explicitly reset only Vocal Duet test score IDs 175–190 and repair reviewed mobile mappings.'

    def add_arguments(self, parser):
        parser.add_argument('--actor-id', type=int, required=True)
        parser.add_argument('--review-file', required=True)
        parser.add_argument('--apply', action='store_true')
        for name in ('approve-plan', 'confirm', 'backup-file', 'backup-sha256', 'restore-receipt', 'snapshot-file'):
            parser.add_argument('--' + name)

    def handle(self, *args, **options):
        try:
            review = json.loads(Path(options['review_file']).read_text(encoding='utf-8'))
            if (not isinstance(review, dict) or any(not isinstance(review.get(key), list) or
                    any(not isinstance(row, dict) for row in review[key]) for key in ('criteria', 'pairs'))):
                raise ResetError('Review file must be an object with criteria/pairs lists of explicit identity objects.')
            if options['apply']:
                names = ('approve_plan', 'confirm', 'backup_file', 'backup_sha256', 'restore_receipt', 'snapshot_file')
                if not all(options.get(name) for name in names):
                    raise ResetError('--apply requires an approved fingerprint, exact confirmation, backup file/hash, restore-verification receipt and new private snapshot path.')
                result = apply_reset(options['actor_id'], review, options['approve_plan'], options['confirm'],
                    options['backup_file'], options['backup_sha256'], options['restore_receipt'], options['snapshot_file'])
            else:
                top_level = not connection.in_atomic_block
                with transaction.atomic():
                    if connection.vendor == 'postgresql' and top_level:
                        with connection.cursor() as cursor:
                            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                    actor = get_user_model().objects.filter(pk=options['actor_id']).first()
                    result = reset_plan(actor, review)
            self.stdout.write(json.dumps(result, indent=2, default=str))
        except (ResetError, OSError, json.JSONDecodeError) as exc:
            # Do not print SQL, private payloads or backup contents on failure.
            if isinstance(exc, ResetError):
                raise CommandError(str(exc)) from None
            raise CommandError('Cannot read/validate a required local review, backup, receipt or snapshot file.') from None
        except ObjectDoesNotExist:
            raise CommandError('The scoped event or authorized operator does not exist.') from None
        except DatabaseError:
            raise CommandError('Database/schema/lock validation failed. No cleanup transaction was committed; obtain a DBA schema/lock review. Private database details were omitted.') from None
