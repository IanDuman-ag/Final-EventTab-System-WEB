"""Three fixed stages; infrastructure approval never runs Django as root."""
import json
import os

from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, connection, transaction

from events import vocal_duet_simple as maintenance
from events.vocal_duet_reset import ResetError


class Command(BaseCommand):
    help = 'Fixed development-only Vocal Duet maintenance: preview, verify, apply. No database or scope overrides.'

    def add_arguments(self, parser):
        parser.add_argument('stage', choices=('preview', 'verify', 'apply'))

    def handle(self, *args, **options):
        if hasattr(os, 'geteuid') and os.geteuid() == 0:
            raise CommandError('Run Django as eventtab, never root. Only the separate host helper uses sudo.')
        try:
            if options['stage'] == 'apply':
                result = maintenance.commit()
            else:
                with transaction.atomic():
                    if connection.vendor != 'postgresql':
                        raise ResetError('PostgreSQL is required; no database writes were attempted.')
                    with connection.cursor() as cursor:
                        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                    if options['stage'] == 'preview':
                        result = maintenance.preview()
                    else:
                        initial = maintenance.read_json(maintenance.session_path() / 'preview.json')
                        reference = None
                        evidence = initial['review'].get('roster_evidence_reference')
                        if not isinstance(evidence, str) or not evidence.strip():
                            reference = input('Actual roster/test-run evidence reference (do not invent one): ').strip()
                            if not reference:
                                raise ResetError('Actual roster/test-score evidence is required. No data changed.')

                        def confirm(backup, review):
                            self.stdout.write('Full relevant restore rows match. Archive SHA256: ' + backup['backup_sha256'])
                            self.stdout.write(json.dumps({'criteria': review['criteria'], 'pairs': review['pairs'],
                                'test_score_ids': list(range(175, 191)), 'evidence': review['roster_evidence_reference']}, indent=2))
                            self.stdout.write('After checking the actual roster, departments, mappings, and test-score evidence, type: ' + maintenance.CONFIRM)
                            self.stdout.write('This also confirms that this exact archive actually produced the isolated restore; row equality alone cannot establish archive provenance.')
                            return input('Confirmation: ').strip()

                        result = maintenance.verify(reference, confirm)
                if options['stage'] == 'verify':
                    try:
                        maintenance.authorization(result)
                        self.stdout.write('Existing authorization is valid; continue to stage 3.')
                    except ResetError as error:
                        self.stdout.write(str(error))
            self.stdout.write(json.dumps(result, indent=2, default=str))
        except (ResetError, OSError, ValueError, KeyError, TypeError, EOFError, DatabaseError, ObjectDoesNotExist) as error:
            if isinstance(error, DatabaseError):
                message = 'Database inspection failed. Check read access, PostgreSQL target, and isolated restore; no reset was approved. Do not grant broad privileges.'
            elif isinstance(error, ObjectDoesNotExist):
                message = 'Required Vocal Duet setup records are missing. Verify events 64/79, Faculty 69 and the exact official roster. No reset was approved.'
            else:
                message = str(error)
            raise CommandError(message) from None
