# Mobile Judge and Viewer API

This patch belongs to the authoritative Django Web project (`eventtabs`), not
Flutter's separate `backend/` project. No models, migrations, authentication
models, dependencies, scoring POST handlers, or publication actions are added.

## Files

- `events/api_urls.py`: additive mobile GET routes; existing router registrations retained.
- `events/mobile_judge.py`: assigned-event, own-history, profile, dashboard fallback and notification projections.
- `events/mobile_viewer.py`: public catalogue, fixtures, published placements and championship standings.
- `events/judging_views.py`: restrict existing Judge reads to assignments, honor `round_id` for `my_scores`, validate candidate IDs, remove GET audit writes.
- `events/judging_serializers.py`: replace mutating round synchronization during event-detail GET; scope category counts to assignments.
- `events/test_mobile_api.py`: API authorization, contracts, isolation, publication and read-only tests.
- `MOBILE_API_DEPLOYMENT.md`: this guide.

The supplied local Web source has **no Judge dashboard route**, whereas the
reported deployed backend already has a working dashboard. The new dashboard
implementation is a fallback for this local source only. During the production
merge, keep the deployed dashboard route and its implementation unchanged.
Review the deployed branch diff instead of overwriting its URL file wholesale.
All existing Web Admin, Faculty, Tabulator and judging paths remain registered.

## Routes

All paths below start with `/api/events/` and accept GET only.

| Path | Response |
| --- | --- |
| `judge/dashboard/` | Local-source fallback; retain the existing production implementation |
| `judge/assignments/` | `{assignments: [...], counts: {...}, today_display: string}` |
| `judge/assignments/<judging_event_id>/` | Assignment, participants, criteria, stages, Faculty-controlled `can_score` |
| `judge/score-history/` | `{entries: [...], counts: {...}}` |
| `judge/profile/` | Own profile and statistics |
| `judge-notifications/` | List of own assignment notices |
| `viewer/dashboard/` | Counts, featured, ongoing, upcoming, latest_results, top_rankings |
| `viewer/events/` | `{events: [...], counts: {...}}` |
| `viewer/rankings/` | `{rankings: [...], sports: [...], categories: [...]}` |
| `viewer/criteria-events/<judging_event_id>/` | Overview, schedule map, contestants, published results/awards |
| `viewer/match-events/<match_id>/` | Overview, schedule list, bracket and published results |
| `viewer/live/` | Public fixtures; unpublished scores remain null |
| `viewer/bracket/` | Public bracket structure with publication-filtered scores |
| `viewer/profile/` | Guest profile |
| `viewer/announcements/` | `{announcements: []}` |
| `viewer/notifications/` | `{notifications: [], unread_count: 0}` |
| `viewer/about/` | Public system identity from existing settings |

Judge endpoints use the Web role resolver and existing DRF Token authentication.
Missing authentication returns 401, wrong roles return 403, and unavailable or
unassigned event detail returns 404. Viewer endpoints intentionally allow guests,
consistent with Flutter Viewer mode and the existing public Web portal.

Responses use named lists, not DRF `results` pagination envelopes, matching the
current Flutter screens. Judge status values are `upcoming`, `ongoing`,
`completed`; score states are `pending`, `draft`, `submitted`, `returned`.
Criterion scores and weights in assignment responses are JSON numbers.
Existing `my_scores` retains its list and decimal-string serializer format.

Supported filters: Judge assignments `status`; assignment detail `round_id`;
history `status`, `date_from`, `date_to` (YYYY-MM-DD); Viewer events `status`,
`type`, `category`, `q`; rankings `sport`, `category`; live `sport`; bracket `event_id`.

## Read-only and publication guarantees

- No GET calls `mobile_round_context`, `SystemSettings.load`, synchronization,
  record creation, score submission, approval, round unlock or publication.
- Portal Judge assignments are authoritative for linked criteria events. Legacy
  assignments are used only for unlinked JudgingEvents. Staff privileges alone
  do not grant Judge access.
- Stored `Event.rounds_config`, Faculty round gates, and existing mobile ID
  mappings supply round state. Missing mappings fail closed with
  `configuration_required: true` and `can_score: false`; GET never repairs them.
- Normalized own active submissions take precedence over bridged legacy cells
  in history. Superseded rows and other judges' scores are excluded.
- Viewer event visibility uses `public_events()`. Score and placement visibility
  uses the existing public publication, verification, approval and stale-result
  checks. Merely finalizing an event does not publish it.
- Viewer criteria results expose approved placements with `show_scores: false`;
  individual judge scores and confidential totals remain private after publication.
- Championship standings reuse the public Web leaderboard calculations and
  persisted awards; the adapter does not award new points or calculate draft standings.

## Verification performed locally

`manage.py check` passes. The focused suite (mobile API, public portal, Faculty
criteria portal, publication workflow, existing mobile round workflow) passes with 143 tests, including 14 new
mobile API tests. GET tests reject SQL writes through a database execute wrapper.

SQLite verification uses an isolated in-memory database and current-model table
creation for the project apps because historical migrations contain SQL that
cannot bootstrap SQLite. No migration file was edited. This is not PostgreSQL
migration verification.

The broader baseline had six failures and one error in 306 tests. The patched
suite has the same failures in 320 tests (plus the passing new mobile tests). Existing failures:

- `CriteriaRoundAdvancementValidationTests.test_zero_candidates_is_blocked`
- `FacultyMatchWorkspaceTests.test_faculty_cannot_publish`
- `FacultyMatchWorkspaceTests.test_successful_confirmations_progress_authored_links_and_gate_readiness`
- `MatchEventWorkflowTests.test_criteria_event_creation_is_reachable`
- `TabulatorResultsPortalTests.test_results_includes_detailed_review_and_selected_participant`
- `TournamentBracketsListingTests.test_criteria_and_pop_solo_never_appear`
- `PageantCriteriaEventTests.test_pageant_publish_success` (error)

PostgreSQL test database creation was attempted locally and denied because the
configured account lacks CREATEDB. No privileges were changed and no live
PostgreSQL data was modified. PostgreSQL validation remains required in staging.

## Safe Kamatera deployment

1. Back up the current release and PostgreSQL database using the established
   backup procedure. Record the current release/commit for rollback.
2. Merge this patch into the **Web backend's** deployed branch in staging.
   Preserve its working Judge dashboard and any routes absent from this local
   snapshot. Review these seven files only; do not copy Flutter's backend,
   temporary test settings, local `.env`, credentials or databases.
3. Use a separate staging PostgreSQL database and a test account allowed to
   create a disposable test database. Never grant the production runtime account
   extra privileges just to run tests. From the Web project virtual environment:

   ```sh
   python manage.py check
   python manage.py check --deploy
   python manage.py makemigrations --check --dry-run
   python manage.py test events.test_mobile_api core.test_public_portal core.test_faculty_criteria_portal core.test_publication_workflow events.test_faculty_mobile_rounds --noinput
   python manage.py test --noinput
   ```

   Resolve or explicitly track the known baseline failures; investigate any new
   failure. No migration is required by this patch. Do not generate or apply a
   schema change as part of this API deployment.
4. Smoke-test with real staging data: Judge token returns 200, no token 401,
   Faculty token 403, unassigned detail 404. Verify assignments, round selection,
   own history and profile in Flutter. Confirm GET leaves candidate, criterion,
   submission counts and round metadata unchanged.
5. Test Viewer as a guest: published event without published results has an empty
   results list and null match scores; draft and inactive events are absent.
   Verify approved published placements/standings through the existing publication
   workflow. Confirm Web Admin, Faculty and Tabulator pages still operate.
6. Deploy the tested release with the existing Kamatera deployment procedure and
   restart/reload the actual Django application service. No Flutter or database
   migration deployment is included. Verify the same endpoints over HTTPS and
   review application logs. Roll back to the recorded previous release if needed.

## Existing configuration and client limitations

- The Web schema has no public announcement/notification store or saved-event
  store. These endpoints deliberately return empty lists/default guest stats.
- Criteria catalogue entries need an existing linked JudgingEvent; match entries
  need an existing fixture because Flutter's detail navigation uses those IDs.
  GET never creates links or fixtures. Complete event setup through the existing
  authorized administration workflow.
- Missing or stale mobile round mappings remain a configuration issue, not a
  reason to unlock rounds or create scoring records during reads. Normalized
  history can display entries without a mobile candidate mapping, but Flutter
  cannot open such an entry's legacy submission detail until setup is complete.
- Flutter calls POST `judging-events/<id>/save_draft/`, which this Web snapshot
  does not implement. Draft-write behavior is outside this GET API patch; do not
  claim draft saving is fixed. Existing `submit_scores` behavior is retained.
- Opening GET event/scores no longer writes JudgeActivityLog view events; score
  submission audit logging remains unchanged. This enforces the requested
  read-only GET requirement.
