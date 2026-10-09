from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from .models import (
    CriteriaScoreSubmission, Event, EventScoringCategory, EventScoringCriterion,
    RegistryCandidate, SystemSettings,
)


class TabulatorResultsPageTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.tabulator = users.objects.create_user(username='results-monitor')
        self.tabulator.groups.add(Group.objects.create(name='Tabulator'))
        self.judge = users.objects.create_user(username='results-judge')
        self.participant = RegistryCandidate.objects.create(number='S-1', name='Performer')
        self.event = Event.objects.create(
            name='Singing', category='Special Event', scoring_method='criteria',
            event_date=date.today(), venue='Hall', participation_type='individual',
            participant_ids=[self.participant.pk],
            rounds_config=[{'id': 'first', 'name': 'First Round'},
                           {'id': 'final', 'name': 'Final Round', 'is_final': True}],
            results_finalized=True,
            publication_status=Event.PUBLICATION_DRAFT,
        )
        self.event.assigned_tabulators.add(self.tabulator)
        self.event.assigned_judges.add(self.judge)
        self.categories = {}
        self.criteria = {}
        for index, rid in enumerate(('first', 'final'), 1):
            self.categories[rid] = EventScoringCategory.objects.create(
                event=self.event, assigned_round_id=rid, name=rid.title(),
                display_order=index, overall_weight_percent=100, judge_mode='scoring',
            )
            self.criteria[rid] = EventScoringCriterion.objects.create(
                category=self.categories[rid], name='Voice', display_order=1,
                weight_percent=100, min_score=0, max_score=100,
            )
        self.client.force_login(self.tabulator)

    def submit(self, rid='first', score=90, status='APPROVED'):
        return CriteriaScoreSubmission.objects.create(
            event=self.event, assigned_round_id=rid, category=self.categories[rid],
            criterion=self.criteria[rid], judge=self.judge,
            participant_id=self.participant.pk, source='MANUAL', status=status, raw_score=score,
        )

    def page(self, **filters):
        return self.client.get(reverse('tabulator_results'), {'event_id': self.event.pk, **filters})

    def test_round_selection_uses_existing_engine_and_never_falls_back(self):
        self.submit('first', 90)
        self.submit('final', 70)
        first = self.page(round_id='first').context['results_page']
        final = self.page(round_id='final').context['results_page']
        self.assertEqual(first['live_rows'][0]['final_score'], 90)
        self.assertEqual(final['live_rows'][0]['final_score'], 70)
        self.assertNotIn('overall', [row['id'] for row in first['rounds']])

    def test_official_results_require_finalization_and_publication(self):
        self.submit()
        draft = self.page().context['results_page']
        self.assertFalse(draft['is_published'])
        self.assertEqual(draft['official_rows'], [])
        self.assertEqual(draft['default_tab'], 'live')
        self.event.publication_status = Event.PUBLICATION_PUBLISHED
        self.event.save(update_fields=['publication_status'])
        published = self.page().context['results_page']
        self.assertTrue(published['is_published'])
        self.assertEqual(published['default_tab'], 'official')
        self.assertEqual(published['official_rows'][0]['prize'], 'Round rank 1')
        self.event.results_finalized = False
        self.event.save(update_fields=['results_finalized'])
        self.assertFalse(self.page().context['results_page']['is_published'])

    def test_hidden_round_standings_are_not_exposed(self):
        self.submit()
        self.event.rounds_config[0]['hide_standings'] = True
        self.event.save(update_fields=['rounds_config'])
        page = self.page().context['results_page']
        self.assertEqual(page['live_rows'], [])
        self.assertIn('hidden', page['scope_notice'])

    def test_live_report_stays_unofficial_when_event_is_published(self):
        self.submit()
        self.event.publication_status = Event.PUBLICATION_PUBLISHED
        self.event.save(update_fields=['publication_status'])
        response = self.page(print='1', tab='live')
        self.assertContains(response, 'UNOFFICIAL')

    def test_incomplete_judge_submissions_have_no_rank(self):
        self.submit()
        self.event.assigned_judges.add(get_user_model().objects.create_user(username='missing-judge'))
        page = self.page().context['results_page']
        self.assertIsNone(page['live_rows'][0]['rank'])
        self.assertFalse(page['scope_complete'])
        self.assertEqual(len(page['breakdown_groups'][0]['rows'][0]['cells']), 2)

    def test_unapproved_scores_visible_for_review_but_never_official(self):
        self.submit(status='SUBMITTED')
        page = self.page().context['results_page']
        self.assertEqual(page['live_rows'], [])
        self.assertEqual(page['official_rows'], [])
        self.assertEqual(page['breakdown_groups'][0]['rows'][0]['cells'][0]['value'], 90)
        self.assertIsNone(page['breakdown_groups'][0]['total'])

    def test_category_breakdown_does_not_mislabel_full_round_total(self):
        self.submit()
        page = self.page(category=str(self.categories['first'].pk)).context['results_page']
        self.assertEqual(page['live_rows'], [])
        self.assertIsNone(page['breakdown_groups'][0]['total'])
        self.assertEqual(page['breakdown_groups'][0]['rows'][0]['category'], 'First')

    def test_export_respects_round_and_rejects_unpublished_official_report(self):
        self.submit('first', 90)
        self.submit('final', 70)
        csv_url = reverse('tabulator_results_csv')
        response = self.client.get(csv_url, {'event_id': self.event.pk, 'round_id': 'final'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('70', response.content.decode())
        self.assertIn('Unofficial', response.content.decode())
        response = self.client.get(csv_url, {'event_id': self.event.pk, 'tab': 'official'})
        self.assertEqual(response.status_code, 409)

    def test_explicit_provisional_csv_is_unofficial_and_does_not_publish(self):
        self.submit()
        self.event.results_finalized = False
        self.event.save(update_fields=['results_finalized'])
        response = self.client.get(reverse('tabulator_results_csv'),
                                   {'event_id': self.event.pk, 'round_id': 'first', 'tab': 'live'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Unofficial', response.content.decode())
        self.event.refresh_from_db()
        self.assertFalse(self.event.results_finalized)
        self.assertEqual(self.event.publication_status, Event.PUBLICATION_DRAFT)

    def test_invalid_filter_and_unassigned_event_cannot_export(self):
        response = self.page(round_id='foreign')
        self.assertTrue(response.context['results_page']['error'])
        response = self.client.get(reverse('tabulator_results_csv'),
                                   {'event_id': self.event.pk, 'round_id': 'foreign'})
        self.assertEqual(response.status_code, 400)
        self.event.assigned_tabulators.clear()
        self.assertIsNone(self.page().context['event'])
        self.assertEqual(self.client.get(reverse('tabulator_results_csv'),
                                        {'event_id': self.event.pk}).status_code, 404)

    def test_print_report_uses_existing_brand_and_marks_unofficial(self):
        SystemSettings.objects.create(school_name='Test University')
        self.submit()
        response = self.page(print='1', round_id='first')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'tabulator/results-report.html')
        self.assertContains(response, 'Test University')
        self.assertContains(response, 'UNOFFICIAL')
        self.assertContains(response, 'First Round')

    def test_criteria_events_appear_across_tabulator_portal(self):
        self.submit('first', 90)
        dashboard = self.client.get(reverse('tabulator_dashboard'), {'event_id': self.event.pk})
        self.assertEqual(dashboard.context['payload']['event_kind'], 'criteria')
        self.assertEqual(dashboard.context['payload']['stats']['participant_count'], 1)
        self.assertEqual(dashboard.context['dashboard_stats']['submitted_scores'], 1)
        self.assertEqual(dashboard.context['dashboard_stats']['pending_scores'], 1)
        self.assertEqual(dashboard.context['payload']['results'][0]['final_score'], 90)
        self.assertIn('First Round', dashboard.context['standings_scope'])
        live = self.client.get(reverse('tabulator_live_rankings'), {'event_id': self.event.pk})
        self.assertEqual(live.context['payload']['results'][0]['final_score'], 90)
        self.assertContains(live, 'Performer')
        events = self.client.get(reverse('tabulator_my_events'))
        self.assertContains(events, 'Singing')
        self.submit('final', 75)
        events = self.client.get(reverse('tabulator_my_events'))
        self.assertEqual(events.context['payload']['events'][0]['progress'], 100)
