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

        dashboard = self.client.get(reverse('faculty_dashboard'))
        self.assertEqual([row['id'] for row in dashboard.context['publication_queue']], [self.event.pk])
        self.assertEqual(dashboard.context['publication_ready_count'], 1)
        self.assertContains(dashboard, '1 Ready for Publication')
        self.assertContains(dashboard, 'Final round approved')
        self.assertNotContains(dashboard, '0/0 matches confirmed')
        self.assertNotContains(dashboard, 'data-open-publication="publication-')
        ready = self.client.get(reverse('faculty_my_events'), {'status': 'ready'})
        self.assertEqual([row['id'] for row in ready.context['event_rows']], [self.event.pk])
        self.assertEqual(ready.context['event_rows'][0]['status'], 'Ready for Publication')

    def test_unsubmitted_criteria_is_pending_even_when_event_visibility_is_published(self):
        self.event.publication_status = 'published'
        self.event.save(update_fields=['publication_status'])
        response = self.client.get(reverse('faculty_dashboard'))
        self.assertEqual(response.context['publication_queue'], [])
        self.assertEqual(response.context['recent_events'][0]['status'], 'Pending Review')

    def test_single_round_requires_confirmation_then_publishes_and_leaves_queue(self):
        from events.public_service import criteria_results
        self.event.rounds_config = [self.event.rounds_config[0]]
        self.event.save()
        self.complete()
        approve_criteria_round(self.event, self.faculty, 0)
        self.assertContains(self.client.get(self.url + '?tab=results'), 'Confirm &amp; Publish')
        dashboard = self.client.get(reverse('faculty_dashboard'))
        self.assertEqual(dashboard.context['publication_ready_count'], 1)
        self.assertContains(dashboard, 'Review &amp; Publish')
        self.client.post(self.url, {'action': 'publish'})
        self.event.refresh_from_db()
        self.assertFalse(self.event.results_finalized)
        self.assertNotIn('faculty_submitted_at', self.event.result_processing_config)
        response = self.client.post(self.url, {'action': 'publish', 'confirm': 'yes', 'tab': 'results'}, follow=True)
        self.assertContains(response, 'Official results published.')
        self.event.refresh_from_db()
        self.assertTrue(self.event.results_finalized)
        self.assertEqual(self.event.result_processing_config['published_by'], self.faculty.pk)
        self.assertEqual(len(criteria_results(self.event)[0]), 2)
        self.assertEqual(self.client.get(reverse('faculty_dashboard')).context['publication_queue'], [])

    def test_legacy_criteria_publication_saves_department_points_snapshot(self):
        from events.faculty_service import leaderboard_rows, publish_results
        from events.models import Department
        department = Department.objects.create(name='Arts', code='ART')
        self.event.rounds_config = [self.event.rounds_config[0]]
        self.event.event_classification = 'major'
        self.event.apply_championship_points = True
        self.event.championship_points_config = [{'points': 100}, {'points': 75}]
        self.event.save()
        for entry in self.entries:
            entry.department = department
            entry.save()
        self.complete()
        approve_criteria_round(self.event, self.faculty, 0)
        response = self.client.post(self.url, {'action': 'publish', 'confirm': 'yes'}, follow=True)
        self.assertContains(response, 'Official results published.')
        self.event.refresh_from_db()
        self.assertEqual(self.event.result_processing_config['published_championship_awards'],
                         {str(department.pk): 175})
        self.assertEqual(leaderboard_rows()[0]['points'], 175)
        publish_results(self.event, self.faculty)
        self.assertEqual(leaderboard_rows()[0]['points'], 175)

    def test_previously_submitted_multi_round_results_can_be_published(self):
        self.complete()
        approve_criteria_round(self.event, self.faculty, 0)
        unlock_criteria_round(self.event, self.faculty, 1)
        self.complete('final')
        approve_criteria_round(self.event, self.faculty, 1)
        submit_criteria_publication(self.event, self.faculty)
        self.assertContains(self.client.get(self.url + '?tab=results'), 'Confirm &amp; Publish')
        self.client.post(self.url, {'action': 'publish', 'confirm': 'yes'})
        self.event.refresh_from_db()
        self.assertTrue(self.event.results_finalized)

    def test_criteria_removal_requires_confirmation_preserves_rankings_and_allows_republication(self):
        from events.public_service import criteria_results
        self.event.rounds_config = [self.event.rounds_config[0]]
        self.event.save()
        self.complete()
        approve_criteria_round(self.event, self.faculty, 0)
        self.client.post(self.url, {'action': 'publish', 'confirm': 'yes'})
        self.event.refresh_from_db()
        approved = self.event.result_processing_config['confirmed_stages']
        scores = list(CriteriaScoreSubmission.objects.values_list('pk', 'raw_score', 'status'))
        for url in [reverse('faculty_dashboard'), reverse('faculty_my_events'), self.url + '?tab=results']:
            self.assertContains(self.client.get(url), 'Remove Publish')
        removal = reverse('faculty_publication_review', args=[self.event.pk])
        self.client.post(removal, {'action': 'unpublish'})
        self.event.refresh_from_db()
        self.assertTrue(self.event.results_finalized)
        response = self.client.post(removal, {'action': 'unpublish', 'confirm': 'yes', 'return_to': 'dashboard'}, follow=True)
        self.assertContains(response, 'Publication removed.')
        self.assertNotContains(response, 'Remove Publish')
        self.event.refresh_from_db()
        self.assertFalse(self.event.results_finalized)
        self.assertEqual(self.event.result_processing_config['confirmed_stages'], approved)
        self.assertEqual(list(CriteriaScoreSubmission.objects.values_list('pk', 'raw_score', 'status')), scores)
        self.assertEqual(criteria_results(self.event)[0], [])
        self.assertEqual(self.client.get(reverse('public_result_detail', args=[self.event.pk])).status_code, 404)
        self.assertEqual(response.context['publication_ready_count'], 1)
        self.client.post(self.url, {'action': 'publish', 'confirm': 'yes'})
        self.event.refresh_from_db()
        self.assertTrue(self.event.results_finalized)
        self.assertEqual(len(criteria_results(self.event)[0]), 2)
        response = self.client.post(removal, {'action': 'unpublish', 'confirm': 'yes', 'return_to': 'my_events'}, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], reverse('faculty_my_events'))
        self.assertContains(response, 'Ready for Publication')

    def test_criteria_removal_rejects_other_faculty_and_missing_csrf(self):
        from events.faculty_service import unpublish_results, FacultyServiceError
        self.event.rounds_config = [self.event.rounds_config[0]]
        self.event.save()
        self.complete()
        approve_criteria_round(self.event, self.faculty, 0)
        self.client.post(self.url, {'action': 'publish', 'confirm': 'yes'})
        removal = reverse('faculty_publication_review', args=[self.event.pk])
        self.client.force_login(self.other)
        self.client.post(removal, {'action': 'unpublish', 'confirm': 'yes'})
        self.event.refresh_from_db()
        self.assertTrue(self.event.results_finalized)
        with self.assertRaises(FacultyServiceError):
            unpublish_results(self.event, self.other)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.faculty)
        self.assertEqual(csrf_client.post(removal, {'action': 'unpublish', 'confirm': 'yes'}).status_code, 403)
        self.event.refresh_from_db()
        self.assertTrue(self.event.results_finalized)

    def test_publish_rejects_unapproved_round_and_other_faculty(self):
        self.client.post(self.url, {'action': 'publish', 'confirm': 'yes'})
        self.event.refresh_from_db()
        self.assertFalse(self.event.results_finalized)
        self.client.force_login(self.other)
        self.client.post(self.url, {'action': 'publish', 'confirm': 'yes'})
        self.event.refresh_from_db()
        self.assertFalse(self.event.results_finalized)

    def test_publish_service_preserves_tabulator_and_admin_requirements(self):
        from events.faculty_service import publish_results, FacultyServiceError
        self.event.rounds_config = [self.event.rounds_config[0]]
        self.event.save()
        self.complete()
        approve_criteria_round(self.event, self.faculty, 0)
        submit_criteria_publication(self.event, self.faculty)
        self.event.result_processing_config.update(verification_mode='tabulator_admin_approval', approved=False)
        self.event.save()
        with self.assertRaises(FacultyServiceError):
            publish_results(self.event, self.faculty)
        self.event.result_processing_config['tabulation_confirmed_at'] = timezone.now().isoformat()
        self.event.save()
        with self.assertRaises(FacultyServiceError):
            publish_results(self.event, self.faculty)
        self.event.result_processing_config['approved'] = True
        self.event.save()
        publish_results(self.event, self.faculty)
        self.event.refresh_from_db()
        self.assertTrue(self.event.results_finalized)

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
        dashboard = self.client.get(reverse('faculty_dashboard'))
        self.assertEqual(dashboard.context['publication_queue'], [])
        self.assertEqual(dashboard.context['publication_ready_count'], 0)
        self.assertEqual(dashboard.context['recent_events'][0]['status'], 'Published Results')
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

    def test_my_events_summary_filters_and_scoped_list(self):
        match = Event.objects.create(name='Basketball', category='Sports', venue='Court',
            event_date=date.today(), scoring_method='match', faculty_account=self.faculty)
        Event.objects.create(name='Other Faculty Event', category='Private', venue='Hall',
            event_date=date.today(), scoring_method='criteria', faculty_account=self.other)
        url = reverse('faculty_my_events')
        response = self.client.get(url, {'type': 'criteria', 'category': 'Arts', 'q': 'Dance'})
        self.assertEqual([row['id'] for row in response.context['event_rows']], [self.event.pk])
        self.assertEqual(response.context['event_summary'], {'total': 2, 'published': 0, 'ready': 0, 'criteria': 1, 'match': 1})
        self.assertEqual(response.context['categories'], ['Arts', 'Sports'])
        self.assertContains(response, 'events-summary')
        self.assertNotContains(response, 'Other Faculty Event')
        response = self.client.get(url, {'type': 'match', 'category': 'Sports'})
        self.assertEqual([row['id'] for row in response.context['event_rows']], [match.pk])
        self.assertEqual(response.context['event_summary']['total'], 2)
        self.assertEqual(self.client.get(url, {'type': 'criteria', 'category': 'Sports'}).context['event_rows'], [])

    def test_my_events_pagination_keeps_active_filters(self):
        for index in range(11):
            Event.objects.create(name=f'Dance {index}', category='Arts', venue='Hall',
                event_date=date.today(), scoring_method='criteria', faculty_account=self.faculty)
        response = self.client.get(reverse('faculty_my_events'), {
            'type': 'criteria', 'category': 'Arts', 'q': 'Dance', 'sort': 'name', 'status': 'all', 'page': 2})
        self.assertEqual(response.context['page_obj'].number, 2)
        self.assertEqual(len(response.context['event_rows']), 2)
        query = response.context['pagination_query']
        self.assertIn('type=criteria', query)
        self.assertIn('category=Arts', query)
        self.assertIn('q=Dance', query)
        self.assertNotIn('page=', query)
        self.assertContains(response, 'Previous page')
