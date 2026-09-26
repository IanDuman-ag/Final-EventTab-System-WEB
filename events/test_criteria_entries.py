import json

from django.test import TestCase

from .criteria_event_service import CriteriaEventValidationError, _sync_event_entries, _validated_event_entries
from .models import Department, Event, RegistryCandidate, Team


class CriteriaEventEntryValidationTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name='Information Technology', code='BSIT')
        self.people = [
            RegistryCandidate.objects.create(number=str(index), name=name, department=self.department)
            for index, name in enumerate(['John Cruz', 'Maria Santos', 'Ana Reyes', 'Mark Santos'], 1)
        ]
        self.team = Team.objects.create(name='BLACKNIGHTS', code='BL', department=self.department)

    def payload(self, pairs):
        return {'event_entries': json.dumps([{'member_ids': pair} for pair in pairs])}

    def test_pair_is_one_entry_with_two_existing_members(self):
        rows = _validated_event_entries('pair', self.payload([[self.people[0].id, self.people[1].id]]), strict=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual([person.id for person in rows[0]['members']], [self.people[0].id, self.people[1].id])

    def test_pair_rejects_same_member(self):
        with self.assertRaisesMessage(CriteriaEventValidationError, 'two different individuals'):
            _validated_event_entries('pair', self.payload([[self.people[0].id, self.people[0].id]]), strict=True)

    def test_pair_rejects_duplicate_and_cross_pair_reuse(self):
        with self.assertRaisesMessage(CriteriaEventValidationError, 'same pair'):
            _validated_event_entries('pair', self.payload([[self.people[0].id, self.people[1].id], [self.people[1].id, self.people[0].id]]), strict=True)
        with self.assertRaisesMessage(CriteriaEventValidationError, 'more than one pair'):
            _validated_event_entries('pair', self.payload([[self.people[0].id, self.people[1].id], [self.people[0].id, self.people[2].id]]), strict=True)

    def test_legacy_group_alias_loads_team(self):
        rows = _validated_event_entries('group', {'participant_ids': json.dumps([self.team.id])}, strict=True)
        self.assertEqual(rows[0]['entry_type'], 'team')
        self.assertEqual(rows[0]['source'].id, self.team.id)

    def test_confirmed_roster_replacement_reports_change_for_qualification_reset(self):
        event = Event.objects.create(name='Duet', category='Special Event', event_date='2026-09-27', venue='Hall', scoring_method='criteria', participation_type='individual')
        first = _validated_event_entries('individual', {'participant_ids': json.dumps([self.people[0].id])}, strict=True)
        saved, replaced = _sync_event_entries(event, first, {})
        self.assertFalse(replaced)
        second = _validated_event_entries('individual', {'participant_ids': json.dumps([self.people[1].id])}, strict=True)
        saved, replaced = _sync_event_entries(event, second, {'confirm_entry_replacement': 'true'})
        self.assertTrue(replaced)
        self.assertEqual(event.participant_ids, [saved[0].id])

    def test_unchanged_roster_preserves_entry_and_qualification_ids(self):
        event = Event.objects.create(name='Solo', category='Special Event', event_date='2026-09-27', venue='Hall', scoring_method='criteria', participation_type='individual')
        rows = _validated_event_entries('individual', {'participant_ids': json.dumps([self.people[0].id])}, strict=True)
        saved, _ = _sync_event_entries(event, rows, {})
        original_id = saved[0].id
        event.result_processing_config = {'qualified_participant_ids': {'0': [original_id], '1': [original_id]}}
        event.save(update_fields=['result_processing_config'])
        saved_again, replaced = _sync_event_entries(event, rows, {})
        event.refresh_from_db()
        self.assertFalse(replaced)
        self.assertEqual(saved_again[0].id, original_id)
        self.assertEqual(event.participant_ids, [original_id])
        self.assertEqual(event.result_processing_config['qualified_participant_ids'], {'0': [original_id], '1': [original_id]})
