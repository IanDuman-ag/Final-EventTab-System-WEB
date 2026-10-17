# Historical migration compatibility review

**Decision: replace the conditional edits with an additive Django replacement
migration. Keep the original migration files unchanged.**

Scope: PostgreSQL 18.3 disposable local clusters and synthetic legacy schemas.
The stated Kamatera history through `events.0044` was supplied by the operator;
the production schema, server and database were not contacted or inspected.
No production migration, migration-history write, upload or deployment occurred.

## Code review

**Verdict: APPROVE** the additive replacement for the tested compatibility
scope. The conditional historical edits are rejected and have been removed.
**Confidence:** high for the tested histories and local PostgreSQL behavior.

| Priority | Finding in the reviewed conditional version | Resolution |
| --- | --- | --- |
| P1 | `reverse_legacy_team()` infers ownership from a constraint name. Forward can do nothing while reverse removes populated modern columns. | Restore original file; do not use this conditional reversal. Fresh databases use a canonical replacement instead. |
| P1 | The inherited `0019` SQL searches `pg_constraint` globally by name. An unrelated relation can suppress creation of the intended FK. The conditional wrapper does not fix that. | No repair SQL runs for fresh or already-completed histories. Preserve original legacy code for historical continuity; document its limits. |
| P2 | Conditional `0020` tests table existence, without proving which constraints or nullability it changed. Original reverse SQL is asymmetric. | Keep original migration immutable. Do not use schema reversal as application rollback. |

### P1: reversing a no-op loses modern data

The conditional forward checks for `abbreviation` without `code`. A modern Team
already has `code`, so it does nothing. The conditional reverse instead checks
for `events_team_department_id_fkey`. That name can belong to a pre-existing FK
on the Team table; it does not prove this migration created any columns.

A real PostgreSQL probe created a modern Team with that FK, `members='real
members'` and `coach='real coach'`. Forward left the row unchanged. Reverse
removed both populated columns, renamed `code` to `abbreviation`, and recreated
legacy display columns with defaults. The Team ID/code survived; those data
values did not. There is no reliable provenance journal in that implementation.

### P1: constraint name is not relation ownership

The `0019` query has only `WHERE conname = ...`; it has no `conrelid`, namespace,
referenced relation, column or constraint-type check. PostgreSQL allows matching
constraint names on different relations. A probe placed that FK name on
`collision_other.child`; repair of `collision_target.events_team` then omitted
the Team department FK entirely. The conditional version executes the same SQL.

The replacement avoids this test on fresh databases. Regression queries check
FK ownership by relation OIDs (`conrelid`/`confrelid`), as well as actions and
deferrability. Already-applied histories are left exactly as found, including
their existing constraint/index names. This change does not silently repair a
pre-existing production schema discrepancy.

## Exact original schema effects

### 0019: Team legacy repair

Forward:

- Rename `events_team.abbreviation` to `code`; fail if `abbreviation` is absent.
- Add missing `department_id bigint NULL`, `members text NOT NULL DEFAULT ''`,
  `coach varchar(200) NOT NULL DEFAULT ''`, and
  `status varchar(20) NOT NULL DEFAULT 'active'`.
- If the globally searched FK name is absent, add
  `events_team_department_id_fkey`, referencing `events_department(id)`,
  `DEFERRABLE INITIALLY DEFERRED`. Its database delete action defaults to
  `NO ACTION`; Django's ORM `on_delete=SET_NULL` is a separate behavior.
- Drop `logo_icon`, `color` and `description`, losing their stored values.
- Create the named unique code index `events_team_code_uniq` if that index name
  does not already exist in the resolved namespace. Name existence alone does
  not validate the index's relation or definition.

Reverse:

- Drop that named code index and Team department FK if present.
- Drop `department_id`, `members`, `coach` and `status`, losing stored values.
- Add missing legacy display columns with defaults `A`, `#00C5D9`, and `''`;
  the original display data is not recovered.
- Rename `code` back to `abbreviation`.

Both operations have **no migration-state changes**. `0018` already declares
the canonical Team with `code`, modern fields, a department FK and code
uniqueness. Thus original `0019` fails after a genuine fresh `0018`. Reversing
only the legacy repair also does not reconstruct `0018`'s declared modern
database shape. Treat it as a historical legacy operation, not a safe rollback.

Fresh and legacy physical schemas need not be byte-identical. Fresh canonical
`code` is NOT NULL with a model-generated unique constraint; a legacy rename
inherits the old column's nullability and existing indexes, and the repair adds
another named unique index. Legacy SQL retains database defaults on the three
new text/status fields; fresh Django creation supplies their defaults in Python
instead. A live read of the disposable fresh schema confirmed no persisted
database defaults for `members`, `coach` and `status`. Fresh department-FK names
are Django-generated, while legacy repair uses the explicit name. These
differences are preserved rather than normalized on the already-migrated site.

### 0020: legacy match references

Forward:

- Require `events_match` to exist; drop only the two specified old FK names.
- Remove `NOT NULL` from `team_a_id` and `team_b_id`.
- Add the two new named Team FKs with `ON DELETE SET NULL`, both deferrable and
  initially deferred. Repeating this SQL without reversing it fails when the
  new named constraints already exist.

Reverse:

- Drop those new named FKs and recreate the two old FK names.
- Team A returns to default `NO ACTION`; Team B remains `ON DELETE SET NULL`.
- Both columns remain nullable; `NOT NULL` is not restored.
- Team deletions and resulting null match references are not undone.

There is no `events_match` model/table creation in the current Django migration
chain. This SQL repairs an inherited table; a fresh database should not invent
that legacy table just to make the repair run.

## Chosen replacement

Added `events/migrations/0018_squashed_0020_fresh_schema.py`, with `replaces`
listing the original `0018`, `0019` and `0020`. It depends on the original
`0017` and creates precisely the Team declared by `0018`. It adds no business
fields, data transformations, custom ownership markers or legacy match table.
Regression tests compare its complete `ProjectState.models` to the original
state through `0020`.

This is a manually constructed replacement, not a claim that automatic SQL
optimization can infer the legacy intent. The original repair SQL cannot simply
be replayed on a new database. An ordinary `0045` cannot solve that: execution
would fail at `0019` before reaching it.

Django uses a replacement when all or none of its replaced migrations are
applied. If only some are applied, it retains the original graph. The behavior
was checked against the installed Django loader/executor and the official
[Django migration documentation](https://docs.djangoproject.com/en/6.0/topics/migrations/#squashing-migrations).

| Database/history | Selected behavior | Result/limit |
| --- | --- | --- |
| Empty PostgreSQL 18 database | Canonical Team creation through the replacement, then normal `0021`–`0044` | Real DDL; no legacy rename or nonexistent-table repair |
| Fresh replacement reversed to `0017`, then replayed | Unapply the whole replacement (`CreateModel` reverses to table removal), then create it again | Deliberately destructive Team-table reversal, tested only on disposable data; unrelated Department row survives |
| Legacy schema adopted through `0018`, repairs pending | Django selects original `0019`/`0020` | Original SQL tested with populated Team/match rows; delete actions and known lossy reverse preserved |
| Legacy original history completed through `0044` | Replacement recognized as applied in loader memory | Empty migration plan; deployment-style checks execute SELECTs only; schema, rows, constraints and recorded timestamps unchanged |

The legacy fixture explicitly records the historical adoption of `0018` over
pre-existing tables, then executes the real remaining original migrations. A
loader omitting the new replacement simulates the old release while building
that fixture. It does not claim the current `0018` creates the old schema.

**Partial-history boundary:** a database stopped after canonical/fresh `0018`
but before `0020` still falls back to the original broken SQL. Unrecorded legacy
tables, ambiguous mixed `code`/`abbreviation` states, and existing FK-name
collisions need separate operator investigation. Do not force/fake migration
history to bypass them. The supplied Kamatera history is complete through
`0044`, so it is outside this partial-history limitation.

## Recorded history and deployment

The regression fixture reaches the original complete graph with no replacement
record. Introducing the new file and running the loader/history/plan checks
leaves `django_migrations` rows, including applied timestamps, unchanged. The
replacement is applied **virtually** for graph resolution; no Team creation or
repair SQL is scheduled against that completed history.

A later explicit `manage.py migrate` can record the replacement alias even when
no schema operation is pending: Django's `check_replacements()` does that. This
is why this task does not run that command on production. Do not confuse an
empty schema plan with permission to write migration-history metadata.

Keep all original files and dependencies in this release. Do not remove original
files, edit the existing history, or migrate into the middle of the replaced
range. Removing squashed history would be a separate future transition.

`execution/deploy_server.py` remains unchanged: it never runs `migrate`, pip
installation or migration reversal. Its pending-migration/history probes only
construct the executor and read the plan/history. Its conservative file gate
still blocks the new replacement file until a separate operator review approves
its installation. Such a review need not apply SQL or run `migrate` for a
database already through `0044`; the read-only empty-plan check is the relevant
compatibility evidence. No automated exception/bypass was added.

Original-file SHA256 verified against exact Git HEAD bytes:

| File | SHA256 |
| --- | --- |
| `0019_align_team_schema.py` | `e4321be0f1c0c7d29a28c476d990775945c25e718aedf87cdc8db1f2fa98574c` |
| `0020_team_delete_set_null_on_legacy_match.py` | `5eebdaeb3aecd55ab925bd728132a6654be19c77cbccd144a02fa89c0db2930c` |

`0018` and `0021`–`0044` were not edited. No model, scoring, publication,
authorization, deployment gate or production configuration was changed.

## Evidence and reproduction

The three PostgreSQL migration tests exercise real schema changes, original SQL
and migration graphs, not mocked execution. Each uses a separate transactional
schema in a newly created local cluster. The complete application suite also
initializes a brand-new PostgreSQL database through the replacement and all
subsequent migrations.

```powershell
.\venv\Scripts\python.exe execution\deploy_checks.py events.test_legacy_migration_repairs
.\venv\Scripts\python.exe execution\deploy_checks.py
.\venv\Scripts\python.exe -m unittest discover -s execution -p "test_deploy*.py"
```

Ignored local evidence under `.tmp/deploy/migration-review/` includes the
reviewed conditional-source snapshots, `conditional-evidence.json`,
`replacement-tests.log`, and `full-suite.log`. No secrets or production data are
included. These artifacts are not packaged for deployment.

## Final verification

- Full application suite: **470 tests, 469 passed, 1 existing Windows-only
  `O_NOFOLLOW` skip, zero failures/errors**. Runtime: 272.395 seconds.
- Focused migration matrix: all 3 tests passed on PostgreSQL 18.3 before the
  full run. The full run also includes all 3 migration tests.
- Deployment safety suite: **39 tests, 38 passed, 1 Linux-only lock skip**.
- Django system checks, model/migration drift check, Python compilation and Git
  whitespace checks passed.
- The full run used a newly created local cluster with real migrations enabled;
  no existing development/production database or credentials were used.
- Generated test clusters were stopped. Source deployment/rollback behavior on
  Kamatera was not exercised. Production was neither contacted nor changed.

**Deployment disposition:** use the additive replacement after reviewing the
new file and completing the separate server setup/file-approval step. Do not
deploy the discarded conditional versions. Keep automatic migrations disabled
as implemented; no production migration-history transition is authorized by
this review.
