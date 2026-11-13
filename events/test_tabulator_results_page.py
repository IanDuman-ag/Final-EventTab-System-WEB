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

    def publish_for_report(self, event=None):
        from django.utils import timezone
        event = event or self.event
        event.publication_status = Event.PUBLICATION_PUBLISHED
        event.status = Event.STATUS_COMPLETED
        event.results_finalized = True
        event.result_processing_config = {'published_at': timezone.now().isoformat()}
        event.save()

    def test_report_builder_preview_and_exports_share_selected_round_data(self):
        import io
        from openpyxl import load_workbook
        config = SystemSettings.load()
        config.school_name = 'Official School'
        config.campus_name = 'Official Campus'
        config.save()
        self.submit('first', 90)
        self.submit('final', 70)
        self.publish_for_report()
        url = reverse('tabulator_reports')
        query = {'event_id': self.event.pk, 'round_id': 'final', 'report_type': 'breakdown', 'judge_id': self.judge.pk}
        response = self.client.get(url, query)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'tabulator/tabulatorreports.html')
        document = response.context['report']
        self.assertEqual(document['round_name'], 'Final Round')
        self.assertEqual(document['rows'][0][-1], 70)
        self.assertEqual(document['judge_name'], self.judge.username)
        self.assertContains(response, 'Generate Report')
        self.assertTemplateUsed(response, 'reports/_header.html')
        self.assertContains(response, 'Official School')
        self.assertContains(response, 'Official Campus')
        self.assertNotContains(response, 'report-paper-brand')
        excel = self.client.get(url, {**query, 'format': 'excel'})
        self.assertEqual(excel.status_code, 200)
        sheet = load_workbook(io.BytesIO(excel.content)).active
        self.assertEqual(sheet.cell(7, len(document['headers'])).value, 70)
        pdf = self.client.get(url, {**query, 'format': 'pdf'})
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b'%PDF'))

    def test_report_breakdown_orders_by_rank_and_keeps_pending_last(self):
        import io
        from decimal import Decimal
        from types import SimpleNamespace
        from openpyxl import load_workbook
        from core.faculty_views import _tabulator_report_document, _download_tabulator_report

        participants = [('1', 'Second', 2), ('2', 'Pending', None), ('3', 'Winner', 1)]
        page = {
            'is_published': False, 'event_kind': 'criteria', 'round_name': 'Final Round',
            'judges': [{'id': self.judge.pk, 'name': 'Judge One'},
                       {'id': self.tabulator.pk, 'name': 'Judge Two'}],
            'live_rows': [{'number': number, 'name': name, 'rank': rank}
                          for number, name, rank in participants],
            'breakdown_groups': [
                {'number': number, 'name': name, 'rows': [
                    {'name': 'Voice', 'max_score': Decimal(100), 'cells': [
                        {'value': Decimal(80) if rank else None, 'status': 'Submitted'},
                        {'value': Decimal(70) if rank else None, 'status': 'Submitted'}]}]}
                for number, name, rank in participants],
        }
        for judge_id in ('', str(self.judge.pk)):
            document = _tabulator_report_document(
                SimpleNamespace(user=self.tabulator), self.event, page, [], 'breakdown', judge_id)
            expected = [1, 1, 2, 2, None, None] if not judge_id else [1, 2, None]
            self.assertEqual([row[0] for row in document['rows']], expected)
            self.assertEqual(document['rows'][0][1], 'Winner')
            self.assertEqual(document['rows'][-1][1], 'Pending')
            export = _download_tabulator_report(document, 'excel')
            sheet = load_workbook(io.BytesIO(export.content)).active
            self.assertEqual([sheet.cell(index, 1).value for index in range(7, 7 + len(expected))], expected)

    def test_report_builder_restricts_event_judge_round_and_role(self):
        self.publish_for_report()
        url = reverse('tabulator_reports')
        foreign = Event.objects.create(name='Private event', event_date=date.today())
        self.assertEqual(self.client.get(url, {'event_id': foreign.pk}).status_code, 404)
        self.assertEqual(self.client.get(url, {'event_id': self.event.pk, 'report_type': 'breakdown', 'judge_id': self.tabulator.pk}).status_code, 404)
        self.assertEqual(self.client.get(url, {'event_id': self.event.pk, 'round_id': 'foreign', 'format': 'pdf'}).status_code, 400)
        self.assertEqual(self.client.get(url, {'report_type': 'unknown'}).status_code, 400)
        self.assertEqual(self.client.get(url, {'format': 'unknown'}).status_code, 400)
        self.client.force_login(self.judge)
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_report_builder_supports_all_report_choices_and_empty_assignment(self):
        self.submit('first', 80)
        self.publish_for_report()
        for kind in ('results', 'breakdown', 'submissions', 'assigned'):
            response = self.client.get(reverse('tabulator_reports'), {'event_id': self.event.pk, 'report_type': kind})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['report']['headers'])
        self.event.assigned_tabulators.clear()
        self.assertContains(self.client.get(reverse('tabulator_reports')), 'No published event results are available for your assigned events.')

    def test_report_selector_requires_public_result_publication(self):
        url = reverse('tabulator_reports')
        for published_fields in (
            {},
            {'publication_status': Event.PUBLICATION_PUBLISHED},
            {'publication_status': Event.PUBLICATION_PUBLISHED, 'status': Event.STATUS_COMPLETED},
        ):
            for name, value in published_fields.items():
                setattr(self.event, name, value)
            self.event.save()
            response = self.client.get(url)
            self.assertEqual(response.context['events'], [])
            self.assertNotContains(response, 'value="all"')
            for kind in ('results', 'breakdown', 'submissions', 'assigned'):
                for export_format in ('', 'pdf', 'excel'):
                    blocked = self.client.get(url, {'event_id': self.event.pk, 'report_type': kind, 'format': export_format})
                    self.assertEqual(blocked.status_code, 404)
        self.publish_for_report()
        response = self.client.get(url)
        self.assertEqual([event['id'] for event in response.context['events']], [self.event.pk])
        self.assertContains(response, 'All published events')
        self.event.result_processing_config['verification_mode'] = 'tabulator_admin_approval'
        self.event.save()
        self.assertEqual(self.client.get(url).context['events'], [])

    def test_all_published_event_results_share_preview_and_export_rows(self):
        import io
        from openpyxl import load_workbook
        self.submit('first', 90)
        self.submit('final', 70)
        self.publish_for_report()
        from .models import BracketMatch, BracketTeam
        match_event = Event.objects.create(name='Published Chess', scoring_method='match', event_date=date.today())
        match_event.assigned_tabulators.add(self.tabulator)
        winner = BracketTeam.objects.create(event=match_event, name='Chess Winners', points=3)
        opponent = BracketTeam.objects.create(event=match_event, name='Chess Runners')
        BracketMatch.objects.create(event=match_event, match_number=1, round_name='Final',
                                   team_a=winner, team_b=opponent, winner=winner,
                                   status='completed', score_a='1', score_b='0')
        self.publish_for_report(match_event)
        hidden = Event.objects.create(name='Private Draft', event_date=date.today())
        hidden.assigned_tabulators.add(self.tabulator)
        foreign = Event.objects.create(name='Unassigned Published', event_date=date.today())
        self.publish_for_report(foreign)
        url = reverse('tabulator_reports')
        query = {'event_id': 'all', 'report_type': 'results', 'round_id': 'stale', 'judge_id': 'stale'}
        response = self.client.get(url, query)
        self.assertEqual(response.status_code, 200)
        document = response.context['report']
        self.assertTrue(document['final'])
        singing_row = next(row for row in document['rows'] if row[0] == 'Singing')
        self.assertEqual(singing_row[:4], ['Singing', 'Criteria-Based', 'Final Round', 1])
        self.assertEqual(singing_row[6], 70)
        chess_rows = [row for row in document['rows'] if row[0] == match_event.name]
        self.assertEqual(len(chess_rows), 2)
        self.assertEqual(chess_rows[0][1:4], ['Match-Based', 'Event standings', 1])
        self.assertEqual(chess_rows[0][5:7], ['Chess Winners', 3])
        self.assertNotContains(response, hidden.name)
        self.assertNotContains(response, foreign.name)
        self.assertContains(response, 'id="report-round" name="round_id" disabled')
        excel = self.client.get(url, {**query, 'format': 'excel'})
        sheet = load_workbook(io.BytesIO(excel.content)).active
        exported = list(sheet.iter_rows(min_row=7, values_only=True))
        self.assertEqual(next(row for row in exported if row[0] == 'Singing')[6], 70)
        self.assertEqual(len(exported), 3)
        pdf = self.client.get(url, {**query, 'format': 'pdf'})
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        self.assertEqual(self.client.get(url, {'event_id': 'all', 'report_type': 'breakdown'}).status_code, 400)
        self.event.assigned_tabulators.clear()
        match_event.assigned_tabulators.clear()
        self.assertContains(self.client.get(url, query), 'No published event results are available')
        self.assertEqual(self.client.get(url, {**query, 'format': 'excel'}).status_code, 400)

    def test_report_excel_labels_cannot_become_formulas(self):
        import io
        from openpyxl import load_workbook
        self.event.name = '=HYPERLINK("https://example.test")'
        self.event.save(update_fields=['name'])
        self.publish_for_report()
        response = self.client.get(reverse('tabulator_reports'), {'report_type': 'assigned', 'format': 'excel'})
        self.assertEqual(response.status_code, 200)
        cell = load_workbook(io.BytesIO(response.content)).active['A7']
        self.assertEqual(cell.value, self.event.name)
        self.assertEqual(cell.data_type, 's')

    def test_configured_priority_criterion_breaks_equal_totals_without_changing_scores(self):
        other = RegistryCandidate.objects.create(number='S-2', name='Other performer')
        self.event.participant_ids = [self.participant.pk, other.pk]
        self.event.publication_status = Event.PUBLICATION_PUBLISHED
        priority = self.criteria['first']
        priority.weight_percent = 50
        priority.save()
        secondary = EventScoringCriterion.objects.create(
            category=self.categories['first'], name='Stage', display_order=2,
            weight_percent=50, min_score=0, max_score=100,
        )
        self.event.tie_break_rules = [{'method': 'highest_selected_criterion', 'criterion_id': f'esc_{priority.pk}'}]
        self.event.save()
        for participant, voice, stage in [(self.participant, 20, 20), (other, 10, 30)]:
            for criterion, score in [(priority, voice), (secondary, stage)]:
                CriteriaScoreSubmission.objects.create(
                    event=self.event, assigned_round_id='first', category=self.categories['first'],
                    criterion=criterion, judge=self.judge, participant_id=participant.pk,
                    source='MANUAL', status='APPROVED', raw_score=score,
                )
        page = self.page(round_id='first').context['results_page']
        for key in ('live_rows', 'official_rows'):
            self.assertEqual([row['rank'] for row in page[key]], [1, 2])
            self.assertEqual([row['final_score'] for row in page[key]], [20, 20])
            self.assertEqual([row['number'] for row in page[key]], ['S-1', 'S-2'])
        self.event.tie_break_rules = []
        self.event.save()
        tied = self.page(round_id='first').context['results_page']['live_rows']
        self.assertEqual([row['rank'] for row in tied], [1, 1])

    def test_chief_judge_breaks_tie_when_priority_criterion_is_equal(self):
        other = RegistryCandidate.objects.create(number='S-2', name='Other performer')
        second_judge = get_user_model().objects.create_user(username='second-tie-judge')
        self.event.participant_ids = [self.participant.pk, other.pk]
        self.event.assigned_judges.add(second_judge)
        self.event.chief_judge = self.judge
        self.event.tie_break_rules = [{'method': 'highest_chief_judge'}]
        self.event.save()
        for participant, chief, second in [(self.participant, 80, 60), (other, 60, 80)]:
            for judge, score in [(self.judge, chief), (second_judge, second)]:
                CriteriaScoreSubmission.objects.create(
                    event=self.event, assigned_round_id='first', category=self.categories['first'],
                    criterion=self.criteria['first'], judge=judge, participant_id=participant.pk,
                    source='MANUAL', status='APPROVED', raw_score=score,
                )
        rows = self.page(round_id='first').context['results_page']['live_rows']
        self.assertEqual([row['rank'] for row in rows], [1, 2])
        self.assertEqual([row['final_score'] for row in rows], [70, 70])

    def test_event_entry_candidate_number_reaches_all_results_views_and_csv(self):
        from .models import CriteriaEventEntry
        entry = CriteriaEventEntry.objects.create(
            event=self.event, entry_type='individual', display_name='#S-1 Performer',
            source_individual=self.participant, display_order=4,
        )
        self.event.participant_ids = [entry.pk]
        self.event.publication_status = Event.PUBLICATION_PUBLISHED
        self.event.save()
        score = self.submit()
        score.participant_id = entry.pk
        score.save()
        response = self.page(round_id='first')
        page = response.context['results_page']
        self.assertEqual(page['live_rows'][0]['number'], 'S-1')
        self.assertEqual(page['official_rows'][0]['number'], 'S-1')
        self.assertEqual(page['breakdown_groups'][0]['number'], 'S-1')
        self.assertContains(response, 'Candidate No.')
        export = self.client.get(reverse('tabulator_results_csv'), {
            'event_id': self.event.pk, 'round_id': 'first', 'tab': 'official',
        })
        self.assertEqual(export.status_code, 200)
        self.assertIn('S-1', export.content.decode())

    def test_entry_order_numbers_teams_without_using_ranking_position(self):
        from core.tabulator_result_page import _participant_number
        from .models import CriteriaEventEntry
        entry = CriteriaEventEntry(event=self.event, entry_type='team', display_name='Team', display_order=6)
        self.assertEqual(_participant_number(entry), 7)

    def test_results_csv_omits_row_status_but_keeps_publication_metadata(self):
        import csv
        import io
        self.submit()
        self.event.publication_status = Event.PUBLICATION_PUBLISHED
        self.event.save(update_fields=['publication_status'])
        for tab in ('official', 'live'):
            response = self.client.get(reverse('tabulator_results_csv'), {
                'event_id': self.event.pk, 'round_id': 'first', 'tab': tab,
            })
            self.assertEqual(response.status_code, 200)
            rows = list(csv.reader(io.StringIO(response.content.decode())))
            self.assertEqual(rows[1][0], 'Report status')
            self.assertEqual(rows[2], ['Rank', 'Number', 'Name', 'Category', 'Score', 'Result'])
            self.assertEqual(len(rows[3]), 6)
            self.assertNotIn('Complete', rows[3])
            self.assertEqual(rows[3][1], 'S-1')

    def test_results_use_automatic_title_based_svg_instead_of_uploaded_image(self):
        self.event.image = 'event_images/old-photo.jpg'
        self.event.save(update_fields=['image'])
        response = self.page()
        self.assertContains(response, 'data:image/svg+xml;base64,')
        self.assertNotContains(response, '<img src="/media/event_images/old-photo.jpg"')

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
        self.assertEqual(dashboard.context['dashboard_stats']['expected_scores'], 2)
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

    def test_dashboard_overview_counts_all_assigned_events_only(self):
        from datetime import timedelta
        upcoming = Event.objects.create(name='Upcoming assigned event', event_date=date.today() + timedelta(days=30))
        upcoming.assigned_tabulators.add(self.tabulator)
        Event.objects.create(name='Unassigned event', event_date=date.today() + timedelta(days=30))
        response = self.client.get(reverse('tabulator_dashboard'), {'event_id': self.event.pk})
        overview = response.context['assigned_overview']
        self.assertEqual(overview['total'], 2)
        counts = {row['key']: row['count'] for row in overview['rows']}
        self.assertEqual(counts, {'upcoming': 1, 'ongoing': 0, 'completed': 1})
        self.assertEqual([row['percent'] for row in overview['slices']], [50, 50])
        self.assertContains(response, 'Assigned Events Overview')
        self.assertContains(response, 'Submitted Score Entries')

    def test_dashboard_chart_handles_empty_and_single_status(self):
        from core.faculty_views import _assigned_events_overview
        empty = _assigned_events_overview([])
        self.assertEqual(empty['total'], 0)
        self.assertEqual(empty['slices'], [])
        single = _assigned_events_overview([{'status_key': 'upcoming'}])
        self.assertTrue(single['slices'][0]['full'])
        self.assertEqual(single['slices'][0]['percent'], 100)
