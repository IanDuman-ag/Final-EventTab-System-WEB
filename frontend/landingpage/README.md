# EventTab public viewer

These Bootstrap 5 Django templates use the existing EventTab database. Public
visitors do not need an account. Staff login remains at `/login/` with its
existing password handling and role redirects; Judges retain the existing
mobile-app instruction after web authentication.

## Routes and implementation

- `/`: community homepage, featured competitions and upcoming schedules.
- `/schedules/`: searchable, paginated event and match schedules.
- `/games/`: competition directory; `/games/<id>/`: participants and fixtures.
- `/leaderboard/`: official department championship points.
- `/results/`: published competitions; `/results/<id>/`: published outcomes.

Routes are registered in `core/urls.py`; read-only views live in
`core/public_views.py`; whitelisted public projections and publication checks
live in `events/public_service.py`. Filters use GET query parameters `q`,
`type`, `category`, `status`, `date`, `venue` and `page`. Criteria result
details additionally accept `round_id`. Dates and times are displayed in
Asia/Manila, matching the existing official report timezone.

The shared public layout uses the local Bootstrap 5.3.8 CSS already installed
for EventTab. `public.js` implements the mobile menu, filter loading indicators
and image fallbacks. The generated local hero is decorative promotional
photography, not an image of a recorded competition. No sample result data,
new database tables or dependencies were added.

## Public result policy

Published event information does not authorize score disclosure. Results
require a completed, finalized, published event and the `published_at` stamp
from the existing authorized publication workflow. Its configured tabulation
and approval requirements must also be met. Draft and inactive events cannot
be opened directly through public detail routes.

Match scores and winners are public only for completed/forfeited matches whose
outcomes were recorded before publication. Later edits remain private until
republished. Criteria placements require complete approved judge sheets from
the existing scoring engines; raw judge scores, notes and breakdowns are never
returned. No public policy for numerical criteria totals exists, so totals are
withheld.

The existing modern criteria engine calculates individual round rankings but
does not calculate combined multiround standings. The portal shows clearly
labeled published round rankings and does not substitute preliminary ranks
for overall winners. Legacy criteria scores without round association cannot
produce public multiround placements. Unsupported overall outcomes do not
contribute to public championship standings.

Public standings call the existing `leaderboard_rows(event_ids=...)` helper
with only eligible published event IDs. Calls without this optional argument
retain their existing staff behavior. Standings display the stored department
points and ranking order; medal counts and new point formulas are not invented.
Announcements are omitted because the project has no announcement model.

## Verification

With a PostgreSQL role permitted to create a test database:

```powershell
.\venv\Scripts\python.exe manage.py test core.test_public_portal core.test_faculty_leaderboard_sync events.test_tabulator_results_page --noinput
```

The current development PostgreSQL role lacks that permission. The 30 targeted
tests were run in an isolated in-memory SQLite database with the current event
model schema (`MIGRATION_MODULES={'events': None}`), because an existing event
migration references a missing `abbreviation` column on SQLite. Production
PostgreSQL data and schema were unchanged. Public pages and filters were also
checked against the running PostgreSQL-backed development server at desktop,
tablet and mobile widths.

For deployment, use the project's existing static-file collection process so
the public CSS, JavaScript and hero are included. No migration is required.
