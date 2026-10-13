"""Browser-facing faculty criteria workflow permissions and publication handoff."""
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from events.criteria_faculty_service import approve_criteria_round, unlock_criteria_round, submit_criteria_publication
from events.models import CriteriaEventEntry, CriteriaScoreSubmission, Event, EventScoringCategory, EventScoringCriterion


class FacultyCriteriaPortalTests(TestCase):
    def setUp(self):
        self.faculty = get_user_model().objects.create_user(username='portal-faculty')
        self.faculty.groups.add(Group.objects.get_or_create(name='Faculty')[0])
        self.other = get_user_model().objects.create_user(username='portal-other')
        self.other.groups.add(Group.objects.get(name='Faculty'))
        self.judge = get_user_model().objects.create_user(username='portal-judge')
        self.event = Event.objects.create(name='Dance', category='Arts', venue='Hall', event_date=date.today(),
            scoring_method='criteria', faculty_account=self.faculty,
            rounds_config=[{'id':'prelim','name':'Preliminary','status':'open','qualifiers':1,'qualification_method':'top_ranking'},
                           {'id':'final','name':'Final','status':'locked'}])
        self.entries = [CriteriaEventEntry.objects.create(event=self.event, entry_type='team', display_name=f'Team {i}', display_order=i) for i in range(2)]
        self.event.participant_ids = [entry.pk for entry in self.entries]
        self.event.save()
        self.event.assigned_judges.add(self.judge)
        self.criteria = {}
        for index, round_id in enumerate(['prelim','final']):
            category = EventScoringCategory.objects.create(event=self.event, assigned_round_id=round_id, name=round_id,
                purpose='official', judge_mode='scoring', overall_weight_percent=100, display_order=index)
            self.criteria[round_id] = EventScoringCriterion.objects.create(category=category, name='Skill', weight_percent=100, max_score=100, display_order=0)
        self.url = reverse('faculty_event_manage', args=[self.event.pk])
        self.client.force_login(self.faculty)

    def complete(self, round_id='prelim'):
        entries = self.entries if round_id == 'prelim' else self.entries[:1]
        for index, entry in enumerate(entries):
            criterion = self.criteria[round_id]
            CriteriaScoreSubmission.objects.create(event=self.event, assigned_round_id=round_id, category=criterion.category,
                criterion=criterion, judge=self.judge, participant_id=entry.pk, source='MOBILE', status='SUBMITTED',
                raw_score=90-index*10, submitted_at=timezone.now())

    def test_tabs_and_live_payload_preserve_selected_round_without_mutating_state(self):
        original = self.event.result_processing_config.copy()
        response = self.client.get(self.url, {'tab':'judges', 'round':1})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['tab'],'judges')
        self.assertEqual(response.context['selected_round_index'],1)
        self.assertContains(response,'Round Management')
        response = self.client.get(self.url, {'format':'json','round':1})
        self.assertEqual(response.json()['portal']['selected_round']['status_key'],'locked')
        self.assertEqual(response['Cache-Control'],'no-store')
        self.event.refresh_from_db()
        self.assertEqual(self.event.result_processing_config,original)

    def test_approval_and_unlock_are_distinct_posts_and_keep_tab_state(self):
        self.complete()
        response = self.client.post(self.url, {'action':'approve_round','round_index':0,'tab':'results','selected_round_index':0})
        self.assertRedirects(response,self.url+'?tab=results&round=0')
        self.event.refresh_from_db()
        self.assertEqual(self.event.rounds_config[1]['status'],'locked')
        response = self.client.post(self.url, {'action':'unlock_round','round_index':1,'tab':'rounds','selected_round_index':0})
        self.assertRedirects(response,self.url+'?tab=rounds&round=1')
        self.event.refresh_from_db()
        self.assertEqual(self.event.result_processing_config['qualified_participant_ids']['1'],[self.entries[0].pk])
        self.assertEqual(self.event.rounds_config[1]['status'],'open')

    def test_unassigned_and_anonymous_cannot_read_or_mutate(self):
        self.client.force_login(self.other)
        response = self.client.post(self.url, {'action':'unlock_round','round_index':1})
        self.assertRedirects(response,reverse('faculty_my_events'))
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code,302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.rounds_config[1]['status'],'locked')

    def test_mutation_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.faculty)
        response=csrf_client.post(self.url, {'action':'approve_round','round_index':0})
        self.assertEqual(response.status_code,403)
        self.complete()
        page=csrf_client.get(self.url)
        token=page.cookies['csrftoken'].value
        response=csrf_client.post(self.url, {'action':'approve_round','round_index':0}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code,302)
        self.event.refresh_from_db()
        self.assertIn('0',self.event.result_processing_config['confirmed_stages'])

    def test_invalid_round_and_duplicate_unlock_leave_state_unchanged(self):
        response=self.client.post(self.url, {'action':'unlock_round','round_index':'invalid','tab':'rounds'})
        self.assertEqual(response.status_code,302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.rounds_config[1]['status'],'locked')
        self.complete()
        approve_criteria_round(self.event,self.faculty,0)
        unlock_criteria_round(self.event,self.faculty,1)
        metadata=self.event.result_processing_config['unlocked_stages'].copy()
        self.client.post(self.url, {'action':'unlock_round','round_index':1})
        self.event.refresh_from_db()
        self.assertEqual(self.event.result_processing_config['unlocked_stages'],metadata)

    def test_final_submission_uses_existing_queue_and_never_publishes(self):
        self.complete()
        approve_criteria_round(self.event,self.faculty,0)
        unlock_criteria_round(self.event,self.faculty,1)
        self.complete('final')
        approve_criteria_round(self.event,self.faculty,1)
        response=self.client.post(self.url, {'action':'submit_publication','tab':'results','selected_round_index':1})
        self.assertRedirects(response,self.url+'?tab=results&round=1')
        self.event.refresh_from_db()
        self.assertFalse(self.event.results_finalized)
        self.assertTrue(self.event.result_processing_config['ready_for_publication'])

    def test_authorized_publication_reads_approved_final_snapshot(self):
        from events.points_settings_service import criteria_placements
        from events.public_service import criteria_results
        self.complete()
        approve_criteria_round(self.event,self.faculty,0)
        unlock_criteria_round(self.event,self.faculty,1)
        self.complete('final')
        approve_criteria_round(self.event,self.faculty,1)
        submit_criteria_publication(self.event,self.faculty)
        points,error=criteria_placements(self.event,{'tie_policy':'official'})
        self.assertEqual(error,'')
        self.assertEqual([row['id'] for row in points],[self.entries[0].pk])
        self.assertEqual(criteria_results(self.event)[0],[])
        from events.faculty_service import publish_results
        admin = get_user_model().objects.create_user(username='portal-publisher', is_staff=True)
        publish_results(self.event, admin)
        results,_=criteria_results(self.event)
        self.assertEqual([row['name'] for row in results],[self.entries[0].display_name])
        self.assertEqual(results[0]['score'],'')
        self.assertNotIn('judge_scores',results[0])

    def test_tabulator_verifies_each_round_without_publishing_or_unlocking(self):
        from events.faculty_service import confirm_tabulation, FacultyServiceError
        tabulator = get_user_model().objects.create_user(username='portal-tabulator')
        tabulator.groups.add(Group.objects.get_or_create(name='Tabulator')[0])
        self.event.assigned_tabulators.add(tabulator)
        self.event.result_processing_config = {'verification_mode':'tabulator_admin_approval', 'approved':False}
        self.event.save()
        self.complete()
        with self.assertRaises(FacultyServiceError):
            approve_criteria_round(self.event, self.faculty, 0)
        confirm_tabulation(self.event, tabulator)
        self.assertFalse(self.event.result_processing_config['ready_for_publication'])
        self.assertEqual(set(CriteriaScoreSubmission.objects.values_list('status',flat=True)), {'VERIFIED'})
        approve_criteria_round(self.event, self.faculty, 0)
        self.assertEqual(self.event.rounds_config[1]['status'],'locked')
        unlock_criteria_round(self.event, self.faculty, 1)
        self.complete('final')
        with self.assertRaises(FacultyServiceError):
            approve_criteria_round(self.event, self.faculty, 1)
        confirm_tabulation(self.event, tabulator)
        approve_criteria_round(self.event, self.faculty, 1)
        submit_criteria_publication(self.event, self.faculty)
        self.assertFalse(self.event.result_processing_config['approved'])
        self.assertEqual(self.event.result_processing_config['tabulation_confirmed_round_id'],'final')

    def test_legacy_stage_endpoint_cannot_bypass_faculty_unlocking(self):
        response = self.client.post(reverse('faculty_stage_confirm', args=[self.event.pk]),
            {'stage_index':0,'qualifier_ids':[self.entries[0].pk]}, content_type='application/json')
        self.assertEqual(response.status_code,403)
        self.event.refresh_from_db()
        self.assertEqual(self.event.rounds_config[1]['status'],'locked')
