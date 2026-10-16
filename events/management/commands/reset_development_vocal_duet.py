"""Separate infrastructure-authorized development command; never a production flag."""
import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from events.models import Event
from events.vocal_duet_reset import ResetError, _authorized
from events.vocal_duet_development import (_file_hash, apply_development_reset, development_plan,
                                          target_identity, verify_isolated_restore)
from events.vocal_duet_reset import _digest


class Command(BaseCommand):
    help = 'Root-provisioned, signed development-only Vocal Duet preview/restore verification/reset.'

    def add_arguments(self, parser):
        parser.add_argument('--actor-id', required=True, type=int)
        parser.add_argument('--review-file', required=True)
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--inspect-target', action='store_true')
        group.add_argument('--verify-backup', action='store_true')
        group.add_argument('--apply', action='store_true')
        for name in ('approve-plan', 'confirm', 'backup-file', 'backup-sha256', 'restore-receipt', 'snapshot-file', 'restored-database'):
            parser.add_argument('--' + name)

    def handle(self, *args, **options):
        try:
            review = json.loads(Path(options['review_file']).read_text())
            if (not isinstance(review, dict) or any(not isinstance(review.get(key), list) or
                    any(not isinstance(row, dict) for row in review[key]) for key in ('criteria', 'pairs'))):
                raise ResetError('A complete structured criteria/pair review file is required.')
            if options['apply']:
                required = ('approve_plan', 'confirm', 'backup_file', 'backup_sha256', 'restore_receipt', 'snapshot_file', 'restored_database')
                if not all(options.get(key) for key in required):
                    raise ResetError('Apply requires every backup, restore, snapshot and explicit approval argument.')
                result = apply_development_reset(options['actor_id'], review, options['approve_plan'], options['confirm'],
                    options['backup_file'], options['backup_sha256'], options['restore_receipt'], options['snapshot_file'], options['restored_database'])
            else:
                top_level = not connection.in_atomic_block
                with transaction.atomic():
                    if connection.vendor == 'postgresql' and top_level:
                        with connection.cursor() as cursor:
                            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                    actor = get_user_model().objects.filter(pk=options['actor_id']).first()
                    if not actor or actor.pk != 69 or not _authorized(actor, Event.objects.get(pk=79)):
                        raise ResetError('Only active authorized Faculty/Admin 69 may use this development workflow.')
                    if options['inspect_target']:
                        result = {'read_only': True, 'target': target_identity(), 'review_sha256': _digest(review)}
                    else:
                        result = development_plan(actor, review)
                        if options['verify_backup']:
                            required = ('backup_file', 'backup_sha256', 'restore_receipt', 'restored_database', 'approve_plan')
                            if not result['can_apply'] or result['fingerprint'] != options['approve_plan'] or not all(options.get(key) for key in required):
                                raise ResetError('Backup verification requires an unblocked exact preview and all backup/restore arguments.')
                            path = Path(options['backup_file'])
                            with path.open('rb') as source:
                                archive = source.read(5) == b'PGDMP'
                            if not archive or _file_hash(path) != options['backup_sha256']:
                                raise ResetError('The PostgreSQL custom backup checksum is invalid.')
                            scope = verify_isolated_restore(result, options['restored_database'])
                            receipt = {'backup_sha256': options['backup_sha256'], 'plan_fingerprint': result['fingerprint'],
                                'verified_by': actor.pk, 'verified_at': timezone.now().isoformat(), 'restore_succeeded': True,
                                'source_database': connection.settings_dict['NAME'], 'isolated_restore_database': options['restored_database'],
                                'scoped_rows_sha256': scope}
                            with Path(options['restore_receipt']).open('x') as output:
                                json.dump(receipt, output, indent=2)
                            result = {'read_only': True, 'backup_verified': True, 'receipt': receipt}
            self.stdout.write(json.dumps(result, indent=2, default=str))
        except ResetError as exc:
            raise CommandError(str(exc)) from None
        except (OSError, ValueError, ObjectDoesNotExist, DatabaseError):
            raise CommandError('Target, permit, database privilege, input or restore verification failed; no reset transaction committed. Private details were omitted.') from None
