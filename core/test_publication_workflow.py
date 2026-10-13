from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import DatabaseError
from django.urls import reverse

from core.test_championship_rules import ChampionshipRulesTests
from events.faculty_service import FacultyServiceError, dashboard_stats, publish_results, save_match_result
from events.models import AuditLog, Event
from events.publication_service import match_publication_review


class PublicationWorkflowTests(ChampionshipRulesTests):
    def test_remove_publication_hides_results_and_points_and_allows_republication(self):
        from events.faculty_service import unpublish_results, leaderboard_rows
        event, a, _, match = self.fixture()
        faculty = get_user_model().objects.create_user('withdraw-faculty')
        faculty.groups.add(Group.objects.create(name='Faculty'))
        event.faculty_account = faculty
        event.save()
        publish_results(event, faculty)
        before = (match.winner_id, match.loser_id, match.score_a, match.score_b, match.status)
        self.client.force_login(faculty)
        self.assertContains(self.client.get(reverse('faculty_dashboard')), 'Remove Publish')
        url = reverse('faculty_publication_review', args=[event.pk])
        self.client.post(url, {'action': 'unpublish', 'return_to': 'dashboard'})
        event.refresh_from_db()
        self.assertTrue(event.results_finalized)
        response = self.client.post(url, {'action': 'unpublish', 'confirm': 'yes', 'return_to': 'dashboard'}, follow=True)
        self.assertContains(response, 'Publication removed')
        self.assertNotContains(response, 'Remove Publish')
        event.refresh_from_db()
        match.refresh_from_db()
        self.assertFalse(event.results_finalized)
        self.assertTrue(event.result_processing_config['approved'])
        self.assertEqual(before, (match.winner_id, match.loser_id, match.score_a, match.score_b, match.status))
        self.assertEqual(self.client.get(reverse('public_result_detail', args=[event.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse('public_results')), event.name)
        self.assertEqual(leaderboard_rows(), [])
        self.assertEqual([row['id'] for row in response.context['publication_queue']], [event.pk])
        unpublish_results(event, faculty)
        self.assertEqual(AuditLog.objects.filter(action='Removed Official Results Publication').count(), 1)
        publish_results(event, faculty)
        a.refresh_from_db()
        self.assertEqual(a.points, 10)
        self.assertEqual(leaderboard_rows()[0]['points'], 10)
        my_events = self.client.get(reverse('faculty_my_events'))
        self.assertContains(my_events, 'Remove Publish')
        self.assertContains(my_events, 'Confirm Removal')
        response = self.client.post(url, {'action': 'unpublish', 'confirm': 'yes', 'return_to': 'my_events'}, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], reverse('faculty_my_events'))
        self.assertContains(response, 'Publication removed')
        self.assertNotContains(response, 'Remove Publish')
        self.assertEqual(response.context['event_rows'][0]['status'], 'Ready for Publication')
        self.assertEqual(leaderboard_rows(), [])

    def test_remove_publication_rejects_unassigned_user_and_rolls_back_audit_failure(self):
        from events.faculty_service import unpublish_results
        event, _, _, _ = self.fixture()
        publish_results(event, self.admin)
        outsider = get_user_model().objects.create_user('withdraw-outsider')
        outsider.groups.add(Group.objects.create(name='Faculty'))
        with self.assertRaises(FacultyServiceError):
            unpublish_results(event, outsider)
        with patch('events.audit_service.record_audit', side_effect=DatabaseError('unavailable')):
            with self.assertRaises(DatabaseError):
                unpublish_results(event, self.admin)
        event.refresh_from_db()
        self.assertTrue(event.results_finalized)
        self.assertIn('published_at', event.result_processing_config)

    def test_queue_tracks_completion_independently_of_publication_checks(self):
        from events.models import ScoreSheet
        event, a, _, match = self.fixture()
        faculty = get_user_model().objects.create_user('queue-faculty')
        faculty.groups.add(Group.objects.create(name='Faculty'))
        event.faculty_account = faculty
        event.result_processing_config = {}
        event.save()
        self.client.force_login(faculty)
        response = self.client.get(reverse('faculty_dashboard'))
        self.assertEqual([row['id'] for row in response.context['publication_queue']], [event.pk])
        self.assertEqual(response.context['publication_ready_count'], 0)
        self.assertEqual(response.context['recent_events'][0]['status'], 'Placements Incomplete')
        self.assertContains(response, '0 Ready for Publication')
        self.assertContains(self.client.get(reverse('faculty_event_manage', args=[event.pk]) + '?tab=matches'),
                            'Placements Incomplete</span>')
        events_page = self.client.get(reverse('faculty_my_events') + '?status=ready')
        self.assertEqual([row['id'] for row in events_page.context['event_rows']], [])
        html = response.content.decode()
        assigned = html.split('id="assigned-events-heading">My Assigned Events</h2>', 1)[1].split('</main>', 1)[0]
        self.assertNotIn('Publish Results', assigned)
        self.assertIn('Open Event', assigned)
        queue = html.split('id="publication-queue-heading">Publication Queue</h2>', 1)[1].split('id="assigned-events-heading">My Assigned Events</h2>', 1)[0]
        self.assertIn('Review Placements', queue)
        match.status = 'pending'
        match.save()
        incomplete = self.client.get(reverse('faculty_dashboard'))
        self.assertEqual(incomplete.context['publication_queue'], [])
        self.assertEqual(incomplete.context['recent_events'][0]['status'], 'In Progress')
        match.status = 'completed'
        match.save()
        sheet = ScoreSheet.objects.create(event=event, match=match, tabulator=faculty, winner=a,
                                         score_team_a=10, score_team_b=7, status='pending')
        self.assertEqual(self.client.get(reverse('faculty_dashboard')).context['publication_queue'], [])
        self.assertEqual(self.client.get(reverse('faculty_dashboard')).context['recent_events'][0]['status'], 'Pending Confirmation')
        sheet.status = 'finalized'
        sheet.save()
        event.result_processing_config = {'championship_scoring': self.rules(), 'approved': True}
        event.save()
        response = self.client.get(reverse('faculty_dashboard'))
        self.assertEqual(response.context['publication_ready_count'], 1)
        publish_results(event, faculty)
        self.assertEqual(self.client.get(reverse('faculty_dashboard')).context['publication_queue'], [])
        self.assertEqual(self.client.get(reverse('faculty_dashboard')).context['recent_events'][0]['status'], 'Published')

    def test_assigned_faculty_can_publish_approved_event_from_dashboard(self):
        event, _, _, _ = self.fixture()
        faculty = get_user_model().objects.create_user('faculty-publisher')
        faculty.groups.add(Group.objects.create(name='Faculty'))
        event.faculty_account = faculty
        event.save()
        self.client.force_login(faculty)
        response = self.client.get(reverse('faculty_dashboard'))
        self.assertContains(response, 'data-open-publication=')
        self.assertContains(response, 'Confirm Publication')
        url = reverse('faculty_publication_review', args=[event.pk])
        self.client.post(url, {'action': 'publish', 'confirm': 'yes', 'return_to': 'dashboard'})
        event.refresh_from_db()
        self.assertTrue(event.results_finalized)
        self.assertEqual(event.result_processing_config['published_by'], faculty.pk)
        publish_results(event, faculty)
        self.assertEqual(AuditLog.objects.filter(action='Published Official Results').count(), 1)
        other, _, _, _ = self.fixture()
        with self.assertRaises(FacultyServiceError):
            publish_results(other, faculty)

    def test_missing_configuration_shows_publication_requirements(self):
        event, _, _, _ = self.fixture()
        faculty = get_user_model().objects.create_user('faculty-blocked')
        faculty.groups.add(Group.objects.create(name='Faculty'))
        event.faculty_account = faculty
        event.result_processing_config = {}
        event.save()
        self.client.force_login(faculty)
        response = self.client.get(reverse('faculty_dashboard'))
        self.assertNotContains(response, 'data-open-publication=')
        self.assertContains(response, 'Championship scoring configuration is unavailable')
        self.assertNotContains(response, '>Confirm Publication</button>')
        with self.assertRaises(FacultyServiceError):
            publish_results(event, faculty)

    def test_legacy_placement_points_publish_public_results_once(self):
        from events.faculty_service import leaderboard_rows
        event, a, b, _ = self.fixture()
        faculty = get_user_model().objects.create_user('legacy-faculty')
        faculty.groups.add(Group.objects.create(name='Faculty'))
        event.faculty_account = faculty
        event.championship_points_config = [{'label': 'Champion', 'points': 100},
                                             {'label': 'Runner-up', 'points': 75}]
        event.result_processing_config = {'ready_for_publication': True, 'legacy_championship_awarded': True}
        event.save()
        self.assertTrue(match_publication_review(event)['can_publish'])
        self.assertNotIn('championship_scoring', event.result_processing_config)
        self.client.force_login(faculty)
        url = reverse('faculty_publication_review', args=[event.pk])
        response = self.client.post(url, {'action': 'publish', 'confirm': 'yes', 'return_to': 'dashboard'}, follow=True)
        self.assertContains(response, 'Official results published')
        event.refresh_from_db()
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual((a.points, b.points), (100, 75))
        self.assertEqual(event.result_processing_config['published_by'], faculty.pk)
        self.assertContains(self.client.get(reverse('public_results')), 'Team A')
        self.assertContains(self.client.get(reverse('public_result_detail', args=[event.pk])), 'Runner-up')
        self.assertEqual(leaderboard_rows()[0]['points'], 100)
        publish_results(event, faculty)
        self.assertEqual(leaderboard_rows()[0]['points'], 100)

    def test_legacy_configuration_preserves_required_admin_approval(self):
        event, _, _, _ = self.fixture()
        event.championship_points_config = [{'points': 10}, {'points': 7}]
        event.result_processing_config = {'verification_mode': 'tabulator_admin_approval',
                                           'tabulation_confirmed_at': '2026-10-10T00:00:00+00:00'}
        event.save()
        with self.assertRaises(FacultyServiceError):
            publish_results(event, self.admin)
        event.refresh_from_db()
        self.assertFalse(event.results_finalized)

    def test_faculty_confirmation_and_direct_publication(self):
        event, a, b, match = self.fixture()
        faculty = get_user_model().objects.create_user('assigned-faculty')
        faculty.groups.add(Group.objects.create(name='Faculty'))
        event.faculty_account = faculty
        event.save()
        match.status = 'pending'
        match.winner = match.loser = None
        match.save()
        self.assertEqual(dashboard_stats(faculty)['pending_results'], 0)
        save_match_result(event, match, faculty, score_a=10, score_b=7, winner_id=a.pk, remarks='', confirm=False)
        self.assertEqual(dashboard_stats(faculty)['pending_results'], 1)
        save_match_result(event, match, faculty, score_a=10, score_b=7, winner_id=a.pk, remarks='', confirm=True)
        event.refresh_from_db()
        self.assertTrue(match_publication_review(event)['review_ready'])
        self.client.force_login(faculty)
        self.assertEqual(self.client.get(reverse('faculty_dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('faculty_my_events')).status_code, 200)
        url = reverse('faculty_publication_review', args=[event.pk])
        self.assertContains(self.client.get(url, follow=True), 'Publish Official Results')
        self.assertContains(self.client.get(reverse('faculty_dashboard')), 'Review &amp; Publish')
        self.client.post(url, {'action': 'submit'})
        event.refresh_from_db()
        self.assertNotIn('publication_submitted_by', event.result_processing_config)
        self.assertTrue(match_publication_review(event)['can_publish'])
        self.assertEqual(self.client.get(reverse('public_result_detail', args=[event.pk])).status_code, 404)
        self.assertContains(self.client.get(url, follow=True), 'Publish Official Results?')
        self.client.post(url, {'action': 'publish'})
        event.refresh_from_db()
        self.assertFalse(event.results_finalized)
        self.client.post(url, {'action': 'publish', 'confirm': 'yes'})
        event.refresh_from_db()
        self.assertTrue(event.results_finalized)
        self.assertTrue(event.result_processing_config['approved'])
        self.assertEqual(event.result_processing_config['published_by'], faculty.pk)
        self.assertEqual(self.client.get(reverse('public_result_detail', args=[event.pk])).status_code, 200)
        published_at = event.result_processing_config['published_at']
        publish_results(event, faculty)
        event.refresh_from_db()
        self.assertEqual(event.result_processing_config['published_at'], published_at)
        self.assertEqual(AuditLog.objects.filter(action='Published Official Results').count(), 1)

    def test_publication_audit_failure_rolls_back(self):
        event, a, _, _ = self.fixture()
        with patch('events.audit_service.record_audit', side_effect=DatabaseError('unavailable')):
            with self.assertRaises(DatabaseError):
                publish_results(event, self.admin)
        event.refresh_from_db()
        a.refresh_from_db()
        self.assertFalse(event.results_finalized)
        self.assertEqual(a.points, 0)

    def test_assigned_faculty_still_cannot_publish_incomplete_or_invalid_results(self):
        event, a, b, match = self.fixture()
        faculty = get_user_model().objects.create_user('validation-faculty')
        faculty.groups.add(Group.objects.create(name='Faculty'))
        event.faculty_account = faculty
        event.result_processing_config['approved'] = False
        event.save()
        match.status = 'pending'
        match.save()
        with self.assertRaises(FacultyServiceError):
            publish_results(event, faculty)
        match.status = 'completed'
        match.save()
        event.result_processing_config['championship_placements'] = {str(a.pk): 2, str(b.pk): 1}
        event.save()
        self.assertFalse(match_publication_review(event)['can_publish'])
        with self.assertRaises(FacultyServiceError):
            publish_results(event, faculty)
        event.refresh_from_db()
        self.assertFalse(event.results_finalized)

    def test_published_matches_are_read_only(self):
        from events.bracket_progression import apply_scoresheet_to_bracket
        from events.models import ScoreSheet
        event, a, _, match = self.fixture()
        faculty = get_user_model().objects.create_user('locked-faculty')
        faculty.groups.add(Group.objects.create(name='Faculty'))
        event.faculty_account = faculty
        event.save()
        sheet = ScoreSheet.objects.create(event=event, match=match, winner=a,
                                         score_team_a=10, score_team_b=7, status='confirmed')
        publish_results(event, faculty)
        with self.assertRaises(FacultyServiceError):
            save_match_result(event, match, faculty, score_a=20, score_b=7,
                              winner_id=a.pk, remarks='', confirm=True)
        with self.assertRaises(ValueError):
            apply_scoresheet_to_bracket(sheet)
        match.refresh_from_db()
        self.assertEqual(match.score_a, '10')

    def test_direct_faculty_publication_failure_rolls_back_approval_and_points(self):
        event, a, _, _ = self.fixture()
        faculty = get_user_model().objects.create_user('rollback-faculty')
        faculty.groups.add(Group.objects.create(name='Faculty'))
        event.faculty_account = faculty
        event.result_processing_config['approved'] = False
        event.save()
        with patch('events.audit_service.record_audit', side_effect=DatabaseError('unavailable')):
            with self.assertRaises(DatabaseError):
                publish_results(event, faculty)
        event.refresh_from_db()
        a.refresh_from_db()
        self.assertFalse(event.result_processing_config['approved'])
        self.assertFalse(event.results_finalized)
        self.assertEqual(a.points, 0)

    def test_match_results_api_allows_only_assigned_faculty_with_confirmation(self):
        event, _, _, _ = self.fixture()
        faculty = get_user_model().objects.create_user('api-publisher')
        faculty.groups.add(Group.objects.create(name='Faculty'))
        event.faculty_account = faculty
        event.result_processing_config['approved'] = False
        event.save()
        self.client.force_login(faculty)
        url = reverse('faculty_results_action', args=[event.pk])
        self.assertEqual(self.client.post(url, {'action': 'publish'}).status_code, 400)
        response = self.client.post(url, {'action': 'publish', 'confirm': 'yes'})
        self.assertEqual(response.status_code, 200)
        event.refresh_from_db()
        self.assertTrue(event.results_finalized)
        self.assertEqual(event.result_processing_config['published_by'], faculty.pk)

    def test_unassigned_faculty_cannot_review(self):
        event, _, _, _ = self.fixture()
        user = get_user_model().objects.create_user('unassigned')
        user.groups.add(Group.objects.create(name='Faculty'))
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('faculty_publication_review', args=[event.pk])).status_code, 403)
        with self.assertRaises(FacultyServiceError):
            publish_results(event, user)

    def test_invalid_rules_and_pending_results_block_publication(self):
        from events.models import ScoreSheet
        event, a, _, match = self.fixture()
        event.result_processing_config['championship_scoring']['placements'][0]['major'] = -10
        event.save()
        self.assertFalse(match_publication_review(event)['can_publish'])
        with self.assertRaises(FacultyServiceError):
            publish_results(event, self.admin)
        event.result_processing_config['championship_scoring']['placements'][0]['major'] = 10
        event.save()
        ScoreSheet.objects.create(event=event, match=match, tabulator=self.admin, winner=a,
                                  score_team_a=10, score_team_b=7, status='pending')
        self.assertFalse(match_publication_review(event)['review_ready'])

    def test_database_error_shows_message_without_publishing(self):
        event, _, _, _ = self.fixture()
        url = reverse('faculty_publication_review', args=[event.pk])
        with patch('events.audit_service.record_audit', side_effect=DatabaseError('unavailable')):
            response = self.client.post(url, {'action': 'publish', 'confirm': 'yes'}, follow=True)
        self.assertContains(response, 'no publication changes were committed')
        event.refresh_from_db()
        self.assertFalse(event.results_finalized)


class AutomaticTournamentPlacementTests(ChampionshipRulesTests):
    def test_six_team_double_elimination_pending_submission_publishes_directly(self):
        from events.faculty_service import leaderboard_rows
        event, teams = self.generated_event(6, 'double_elimination')
        self.complete(event)
        self.assertEqual(event.bracket_matches.filter(is_automatic_advance=False).count(), 10)
        faculty = get_user_model().objects.create_user('table-tennis-faculty')
        faculty.groups.add(Group.objects.create(name='Faculty In-Charge'))
        event.faculty_account = faculty
        cfg = event.result_processing_config
        cfg['approved'] = False
        cfg['publication_submitted_by'] = faculty.pk
        cfg['publication_submitted_at'] = '2026-10-11T23:00:00+08:00'
        cfg['championship_scoring']['placements'] = [
            {'rank': rank, 'major': points, 'minor': points}
            for rank, points in enumerate((100, 75, 50, 25, 15, 15), 1)
        ]
        event.save()
        review = match_publication_review(event)
        self.assertTrue(review['can_publish'], review['errors'] + review['approval_errors'])
        self.assertEqual(len(review['standings']), 6)
        self.client.force_login(faculty)
        self.assertContains(self.client.get(reverse('faculty_dashboard')), '1 Ready for Publication')
        url = reverse('faculty_publication_review', args=[event.pk])
        response = self.client.post(url, {'action': 'publish', 'confirm': 'yes'}, follow=True)
        self.assertContains(response, 'Official results published')
        event.refresh_from_db()
        self.assertEqual(event.result_processing_config['published_by'], faculty.pk)
        self.assertEqual(leaderboard_rows()[0]['points'], 280)
        publish_results(event, faculty)
        self.assertEqual(leaderboard_rows()[0]['points'], 280)
        self.assertEqual(AuditLog.objects.filter(action='Published Official Results').count(), 1)
        self.assertEqual(self.client.get(reverse('public_result_detail', args=[event.pk])).status_code, 200)

    def generated_event(self, count, format='single_elimination'):
        from events.models import BracketTeam, BracketMatch
        from events.match_event_service import build_match_blueprint
        event, _, _, _ = self.fixture()
        event.bracket_matches.all().delete()
        event.bracket_teams.all().delete()
        event.tournament_type = format
        event.save()
        teams = [BracketTeam.objects.create(event=event, name=f'Team {i}', department=self.department) for i in range(count)]
        ids = [t.pk for t in teams]
        blueprint = build_match_blueprint(ids, format, draw_order=ids)
        nodes = {}
        for row in blueprint['matches']:
            if row.get('is_automatic_advance'):
                continue
            nodes[row['key']] = BracketMatch.objects.create(event=event, match_number=row['number'], round_name=row['round'],
                team_a_id=row.get('team_a_id'), team_b_id=row.get('team_b_id'))
        for row in blueprint['matches']:
            if row['key'] not in nodes:
                continue
            match = nodes[row['key']]
            match.next_match_winner = nodes.get(row.get('next_winner_key'))
            match.next_match_loser = nodes.get(row.get('next_loser_key'))
            match.save()
        return event, teams

    def complete(self, event, lower_wins_grand_final=False):
        from events.models import ScoreSheet
        from events.bracket_progression import apply_scoresheet_to_bracket
        for _ in range(40):
            ready = event.bracket_matches.filter(status='pending', team_a__isnull=False, team_b__isnull=False).first()
            if not ready:
                break
            winner = ready.team_a
            if lower_wins_grand_final and ready.round_name == 'Grand Final':
                # Determine the undefeated finalist from the actual loss history.
                losses_a = event.bracket_matches.filter(loser=ready.team_a, status='completed').count()
                winner = ready.team_a if losses_a else ready.team_b
            sheet = ScoreSheet.objects.create(event=event, match=ready, winner=winner,
                status='confirmed', score_team_a=10, score_team_b=5)
            apply_scoresheet_to_bracket(sheet)
        event.refresh_from_db()
        if not event.bracket_matches.exclude(status__in=['completed', 'forfeit']).exists():
            from events.faculty_service import approve_results
            approve_results(event, self.admin)

    def test_single_formats_playoff_progression_and_publication(self):
        for count in (2, 3, 4, 5, 8):
            with self.subTest(count=count):
                event, teams = self.generated_event(count)
                self.assertEqual(event.bracket_matches.count(), count - 1 + int(count >= 4))
                self.complete(event)
                review = match_publication_review(event)
                self.assertTrue(review['can_publish'], review['errors'])
                self.assertEqual(review['completed'], review['total'])
                self.assertEqual(event.status, Event.STATUS_COMPLETED)
                if count >= 4:
                    third = event.bracket_matches.get(round_name='Third Place')
                    semis = event.bracket_matches.filter(next_match_loser=third)
                    self.assertEqual({m.loser_id for m in semis}, {third.team_a_id, third.team_b_id})
                    self.assertFalse(third.winner_id == event.bracket_matches.get(round_name='Finals').winner_id)
                publish_results(event, self.admin)
                first = event.result_processing_config.copy()
                publish_results(event, self.admin)
                event.refresh_from_db()
                self.assertEqual(first, event.result_processing_config)
                self.assertEqual(self.client.get(reverse('public_result_detail', args=[event.pk])).status_code, 200)

    def test_double_five_teams_and_reset(self):
        for reset in (False, True):
            with self.subTest(reset=reset):
                event, teams = self.generated_event(5, 'double_elimination')
                self.assertEqual(event.bracket_matches.count(), 8)
                self.complete(event, lower_wins_grand_final=reset)
                review = match_publication_review(event)
                self.assertTrue(review['can_publish'], review['errors'])
                self.assertEqual(event.bracket_matches.count(), 9 if reset else 8)
                self.assertEqual(sorted(row['rank'] for row in review['standings']), [1, 2, 3, 4, 5])

    def test_missing_playoff_repair_is_additive_authorized_and_idempotent(self):
        from events.publication_service import insert_missing_third_place
        from events.models import BracketMatch
        event, teams = self.generated_event(5)
        BracketMatch.objects.filter(event=event, round_name='Third Place').delete()
        self.complete(event)
        before = list(event.bracket_matches.values_list('pk', 'winner_id', 'loser_id', 'score_a', 'score_b', 'status'))
        self.assertIn('Third-Place Playoff', ' '.join(match_publication_review(event)['errors']))
        outsider = get_user_model().objects.create_user('playoff-outsider')
        with self.assertRaises(ValueError):
            insert_missing_third_place(event, outsider)
        insert_missing_third_place(event, self.admin)
        insert_missing_third_place(event, self.admin)
        self.assertEqual(event.bracket_matches.count(), 5)
        self.assertEqual(before, list(event.bracket_matches.filter(pk__in=[r[0] for r in before]).values_list('pk', 'winner_id', 'loser_id', 'score_a', 'score_b', 'status')))
        self.assertFalse(match_publication_review(event)['can_publish'])
        self.complete(event)
        self.assertTrue(match_publication_review(event)['can_publish'])
        publish_results(event, self.admin)
        with self.assertRaises(ValueError):
            insert_missing_third_place(event, self.admin)

    def test_missing_double_match_cannot_publish(self):
        event, _ = self.generated_event(5, 'double_elimination')
        self.complete(event)
        event.bracket_matches.filter(round_name='Losers Round 1').delete()
        review = match_publication_review(event)
        self.assertTrue(review['all_confirmed'])
        self.assertFalse(review['can_publish'])
        with self.assertRaises(FacultyServiceError):
            publish_results(event, self.admin)


    def test_reset_is_required_until_deciding_match_confirmed(self):
        from events.models import ScoreSheet
        from events.bracket_progression import apply_scoresheet_to_bracket
        event, _ = self.generated_event(5, 'double_elimination')
        # Stop just before the first Grand Final, then let the lower finalist win.
        for _ in range(20):
            match = event.bracket_matches.filter(status='pending', team_a__isnull=False, team_b__isnull=False).first()
            self.assertIsNotNone(match)
            if match.round_name == 'Grand Final':
                break
            sheet = ScoreSheet.objects.create(event=event, match=match, winner=match.team_a, status='confirmed')
            apply_scoresheet_to_bracket(sheet)
        undefeated_a = not event.bracket_matches.filter(loser=match.team_a, status='completed').exists()
        sheet = ScoreSheet.objects.create(event=event, match=match,
            winner=match.team_b if undefeated_a else match.team_a, status='confirmed')
        apply_scoresheet_to_bracket(sheet)
        self.assertEqual(event.bracket_matches.filter(round_name='Grand Final Reset').count(), 1)
        review = match_publication_review(event)
        self.assertFalse(review['can_publish'])
        self.assertIn('Grand Final reset is awaiting confirmation.', review['errors'])
        with self.assertRaises(FacultyServiceError):
            publish_results(event, self.admin)
        apply_scoresheet_to_bracket(sheet)
        self.assertEqual(event.bracket_matches.filter(round_name='Grand Final Reset').count(), 1)

    def test_round_robin_third_place_tie_and_configured_resolution(self):
        from events.models import BracketMatch
        event, teams = self.generated_event(5, 'round_robin')
        # Wins: 4, 3, 1, 1, 1. Bottom three have unresolved shared third.
        for match in event.bracket_matches.all():
            a, b = teams.index(match.team_a), teams.index(match.team_b)
            winner = match.team_a
            if a >= 2 and (a, b) == (2, 4):
                winner = match.team_b
            match.winner = winner
            match.loser = match.team_b if winner == match.team_a else match.team_a
            match.score_a, match.score_b = '10', '5'
            match.status = 'completed'
            match.save()
        event.tie_break_rules = []
        event.save()
        review = match_publication_review(event)
        self.assertIn('third-place tie', ' '.join(review['errors']))
        self.assertFalse(review['can_publish'])
        event.result_processing_config['championship_scoring']['tie_policy'] = 'shared'
        event.save()
        review = match_publication_review(event)
        self.assertTrue(review['can_publish'], review['errors'])
        self.assertEqual([row['rank'] for row in review['standings']], [1, 2, 3, 3, 3])

    def test_authorized_repair_appears_in_existing_event_page(self):
        from events.models import BracketMatch
        event, _ = self.generated_event(5)
        BracketMatch.objects.filter(event=event, round_name='Third Place').delete()
        self.complete(event)
        url = reverse('faculty_event_manage', args=[event.pk]) + '?tab=matches'
        response = self.client.get(url)
        self.assertContains(response, 'Insert Missing Third-Place Playoff')
        self.assertContains(response, 'Final Placements')
        response = self.client.post(reverse('faculty_publication_review', args=[event.pk]), {'action': 'insert_playoff'}, follow=True)
        self.assertContains(response, 'Third-Place Playoff added')
        self.assertEqual(event.bracket_matches.count(), 5)


    def test_admin_table_exposes_additive_repair_and_returns_to_table(self):
        from events.models import BracketMatch
        event, _ = self.generated_event(5)
        event.sport_type = 'Basketball'
        event.save()
        BracketMatch.objects.filter(event=event, round_name='Third Place').delete()
        self.complete(event)
        response = self.client.get(reverse('admin_match_events'))
        self.assertContains(response, 'Insert Missing Third-Place Playoff')
        self.assertNotContains(response, 'Include Third-Place Match')
        self.assertContains(response, 'Automatic Third-Place Determination')
        response = self.client.post(reverse('faculty_publication_review', args=[event.pk]),
            {'action': 'insert_playoff', 'return_to': 'admin_matches'})
        self.assertEqual(response.url, reverse('admin_match_events'))
        event.refresh_from_db()
        self.assertNotIn('approved', event.result_processing_config)
        self.assertEqual(event.bracket_matches.count(), 5)
