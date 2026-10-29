"""Judge photos come from explicit registry links without changing scoring data."""
from django.test import TestCase

from .models import CriteriaEventEntryMember, RegistryCandidate, Team
from . import test_judge_multi_round_workflow as fixtures
from .test_mobile_api import read_only_queries


class JudgeParticipantPhotoTests(TestCase):
    def setUp(self):
        fixtures.JudgeMultiRoundWorkflowTests.setUp(self)
        self.client.force_login(self.judges[0])
        self.person = RegistryCandidate.objects.create(number='photo-1', name='First', image='candidates/first.jpg')
        self.other = RegistryCandidate.objects.create(number='photo-2', name='Second', image='candidates/second.jpg')

    def participant(self, index=0):
        with read_only_queries():
            response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        return response.json()['participants'][index]

    def test_individual_uses_registry_photo(self):
        entry = self.entries[0]
        entry.source_individual = self.person
        entry.save(update_fields=['source_individual'])
        result = self.participant()
        self.assertEqual(result['photo'], self.person.image.url)
        self.assertEqual(result['photos'], [self.person.image.url])

    def test_team_uses_team_image(self):
        team = Team.objects.create(name='Photo team', code='PHOTO', image='teams/team.jpg')
        entry = self.entries[0]
        entry.entry_type, entry.source_team = 'team', team
        entry.save(update_fields=['entry_type', 'source_team'])
        self.assertEqual(self.participant()['photos'], [team.image.url])

    def test_pair_returns_both_member_photos_in_performance_order(self):
        entry = self.entries[0]
        entry.entry_type = 'pair'
        entry.save(update_fields=['entry_type'])
        CriteriaEventEntryMember.objects.create(entry=entry, individual=self.other, position=2)
        CriteriaEventEntryMember.objects.create(entry=entry, individual=self.person, position=1)
        self.assertEqual(self.participant()['photos'], [self.person.image.url, self.other.image.url])

    def test_missing_photo_does_not_borrow_another_candidates_image(self):
        self.assertEqual(self.participant()['photos'], [])
        self.assertEqual(self.participant()['photo'], '')
