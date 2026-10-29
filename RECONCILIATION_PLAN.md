# Local / Kamatera reconciliation plan

Original review: 2026-10-16. **Review verdict: REQUEST CHANGES before release.** Confidence is
high for the collected source differences; runtime/client/database compatibility
still requires isolated verification. The user subsequently authorized local
implementation on 2026-10-17; that work and evidence are recorded below. No
production deployment, permission change or Git commit is authorized here.

## Local implementation update: 2026-10-17

Implemented only the approved local application reconciliation:

| File changed/added | Exact result |
| --- | --- |
| `accounts/urls.py` | Imported the saved server source, preserving `auth_access_code` at `access-code/` and every existing auth route. |
| `accounts/views.py` | Imported the saved server source, preserving access-code normalization, ambiguous/missing-code rejection, active Judge/Scorer checks, token reuse and response fields. All other auth functions remain intact. |
| `events/judge_mobile_api.py` (new locally) | Imported the exact saved live dashboard source. Existing `core.views.get_assignment_role`, `events.models.JudgingEvent`, Django and DRF dependencies were already present; no new dependency or scoring handler is needed. |
| `events/api_urls.py` | Uses the deployed `judge_dashboard` implementation; retains every local endpoint, adds live `judge/notifications/`, retains local `judge-notifications/`, and registers all deployed hyphenated reverse names alongside the existing underscore names. Router registrations are preserved. |
| `accounts/test_access_code_login.py` (new) | Six tests for normalization, existing-token reuse/authenticated access, Scorer login, invalid/oversized/missing/ambiguous codes, disabled/wrong-role rejection, existing password login and POST-only behavior. |
| `events/test_mobile_api.py` | Three new tests for the actual live dashboard JSON/status/assignment contract, notification aliases and authorization/read-only behavior, and both deployed/local reverse-name sets. |
| `events/test_judge_portal_flow.py` | Updated one existing test's fallback-dashboard expectations to the deployed event-status stats and null `last_submission`. It still checks own submitted score/candidate, history counts, receipt weights, retry deduplication, assignment detail progress and unchanged other-judge score rows. |
| `events/test_pdf_exports.py` (new) | Four tests exercise real multipage admin/audit exports, match/criteria scoresheet rendering in both orientations with embedded images/fonts, and the Superadmin numbered-canvas report. |
| `RECONCILIATION_PLAN.md` | This implementation record, dependency proposal, narrowly scoped permissions remedy and actual test evidence. |

Accounts URLs/views and the imported dashboard were independently SHA256-checked
against the saved server source: exact byte matches. No new SSH connection was
made for implementation. Work is isolated on the local branch
`reconciliation/local-20261017`, with existing uncommitted changes preserved and
no staging or commit. Existing mobile/scoring/model services, Vocal Duet
runtime, faculty dashboard fixes, publication code and original migrations 0019/
0020 were preserved. Shared runtime files have no Git diff from local HEAD.

The dashboard's exact deployed stats keys are `total_events`, `ongoing_events`
and `completed_events`. The earlier prose describing them as
`total_assignments/completed` was inaccurate; the implementation and new tests
follow the actual saved server source, not that prose.

### ReportLab decision: tested proposal, requirements unchanged

The existing venv remains ReportLab 4.5.1 / Pillow 12.2.0 /
charset-normalizer 3.4.7. Production-compatible PDF packages were downloaded
as wheels and installed only under `.tmp/reconciliation/pdf-overlay`:
ReportLab 5.0.1 / Pillow 12.3.0 / charset-normalizer 3.5.2. Fresh test processes
select that overlay explicitly; the project venv and production packages were
not altered. Both variants use verified disposable PostgreSQL clusters and
temporary media/static directories, with external mail/storage disabled.

**Proposal:** add only `reportlab==5.0.1` to `requirements.txt` after reviewing
the evidence. The exact proposed requirements bytes are saved locally in
`.tmp/reconciliation/proposed-requirements.txt`; their SHA256 matches the
previously collected server requirements fingerprint below. The other existing
requirements remain unchanged. Do not pin Pillow/charset-normalizer or upgrade
other libraries incidentally; their production versions were used for testing.

`requirements.txt` has deliberately not been changed: the user requested testing
before a requirements proposal. The pin remains an explicit release decision.
The existing dependency hash gate is unchanged and must match the final reviewed
requirements file. Current default Windows tests still use ReportLab 4.5.1;
the full run recorded below explicitly selects the 5.0.1 overlay.

Passing PDF tests verifies generation, page structure, pagination, image/font
embedding and existing header/logo behavior. It does not prove pixel-identical
output. Physical/visual PDF approval and production-equivalent Linux/Python 3.12
PDF rehearsal remain required; the comparative application runs here use the
existing Windows Python 3.14 interpreter.

### Minimal permissions remediation proposal: no production changes

The saved inventory identifies **239 existing allowlisted managed source files**
with group/other write bits. Exact observed paths, owners, hashes and proposed
modes are in `.tmp/reconciliation/permission-candidates.json`. This is an input
to operator review, not an executable chmod list or complete server inventory:
binary design assets, ancestor directories and ACLs still need inspection under
future separate authorization.

1. Preserve source contents, UID/GID, original modes and ACLs in a verified
   independent baseline backup. Recheck each selected hash/owner/path against a
   frozen live inventory before authorizing any change. Reject symlinks, unknown
   owners and unexpected drift.
2. Review only final release-managed regular source paths and the exact ancestor
   directories checked by `trusted_artifact()` up to the application root. Review
   deployment state/incoming/checkpoint/backup directories separately for the
   existing private-mode policy. Do not traverse unrelated application children.
3. For each explicitly approved source file/directory, remove only group/other
   write: `new_mode = old_mode & ~0o022`. Observed regular files 0664 become 0644;
   an inspected directory 0775 would become 0755. Preserve ownership, owner
   read/write/execute and other existing bits. Do not grant broader access or
   replace private modes with a blanket 0644/0755 policy. Review nontrivial ACLs
   separately; do not silently clear ACLs, setgid or other special bits.
4. Make no changes to `.env`, uploads/media, authorization artifacts, reset/
   maintenance files, venv, database data or root-controlled helpers. No recursive
   chmod/chown, no ownership transfer and no relaxation of helper validation.
   Source-only permission adjustment does not justify a service restart.
5. Rehearse exactly this approved path/mode list on a replica first, checking
   application/static access and successful code rollback. Record old/new modes
   for precise metadata recovery; authorize any production application of this
   remedy separately after the directory/ACL gaps are closed.

A network-disabled, read-only-root Linux container ran an unprivileged disposable
fixture rehearsal: setting one managed file to 0664 and its directory to 0775
caused strict rollback rejection before service calls or application/journal
changes. Removing only g/o write on those two exact temporary paths allowed
rollback. Fixture secrets and media contents/modes were unchanged. No helper
security code or production permissions changed. Evidence:
`.tmp/reconciliation/permissions-linux.log` and `permission_rehearsal.py`.

### Test results and remaining release gates

- Authentication/mobile contract selection: **26 tests passed**, zero skips,
  failures or errors, using the disposable PostgreSQL runner.
- ReportLab 4.5.1 baseline: **14 PDF/header tests passed**.
- ReportLab 5.0.1 with production Pillow/charset-normalizer versions: **the same
  14 PDF/header tests passed**.
- Full application rerun with the production PDF overlay: **483 tests, 482
  passed, one existing Windows `O_NOFOLLOW` skip**, zero failures/errors;
  runtime **197.752 seconds**. Real migrations, Django system checks and
  `makemigrations --check --dry-run` passed. The owned temporary cluster stopped
  after completion; no tests or migrations were disabled.
- Deployment safety suite: **62 tests, 59 passed, three Linux-only skips**.
- Isolated Linux permissions remedy rehearsal: **passed** as described above.
- Python compilation, preserved server/local URL-path/name inventory checks,
  imported-source SHA256 comparisons and Git whitespace checks passed.
- New tests add 13 cases to the prior 470-test application suite. Logs are
  `.tmp/reconciliation/contracts.log`, `pdf-4.5.1.log`, `pdf-5.0.1.log`,
  `full-suite.log`, `deployment-tests.log` and `permissions-linux.log`.

Reproduce with the existing local venv and saved isolated overlay:

```powershell
.\venv\Scripts\python.exe execution\deploy_checks.py
.\venv\Scripts\python.exe .tmp\reconciliation\pdf_checks.py --production-pdf
.\venv\Scripts\python.exe -m unittest discover -s execution -p "test_deploy*.py"
```

The first command uses the unchanged project PDF stack; the second uses the
temporary production PDF overlay. Both application commands create their own
verified local PostgreSQL cluster and never fall back to an existing database.
The wrapper/overlay/logs are ignored temporary evidence, not release payload.
The new durable PDF and auth/mobile tests remain in the normal test suite.

The first complete 483-test run found one `KeyError: submitted` in an older
portal test expecting the fallback dashboard contract. The runtime already
preserved the saved deployed dashboard correctly. Only that test's dashboard
assertions were reconciled; own-score/receipt/duplicate/other-judge protection
checks remain. The initial run (one error, one expected skip) is retained in
`full-suite-before-contract-update.log`, followed by a full rerun. No test was
disabled or marked successful without execution.

Pending release gates: dependency-pin decision/approved hash; explicit
source/directory/ACL permissions remediation approval; additive migration-file
approval; verified independent baseline backup/restore; production-equivalent
systemd/Nginx and Linux/Python 3.12 PDF/mobile client rehearsal; refreshed server
drift inventory under separate authorization; final reviewed commit approval and
clean tree. No first deployment is authorized by local test success.

Only local application/test/document files and ignored temporary test artifacts
were changed. No Kamatera access, production migration, deployment, live service
restart, Git staging or commit occurred during this implementation.

## Evidence and scope

Read-only SSH to `eventtab@103.125.217.125` succeeded. The actual server HEAD is
`f35dab8ba8139eb6b2e794e920ef34cfb2649fb0`, not the supplied `f35da8ba`.
Local HEAD is `6eb98714bef4b02c13164d0d04d52eb07002ed0d`. The actual server
commit is already in local Git and is the merge base of these two commits.
There is no need to pull or reset either working tree to obtain a comparison.
The server does not contain the local HEAD object.

Compared complete selected application Python/migration files, frontend textual
source, tests, `manage.py` and requirements, including relevant untracked mobile
modules. Source contents were read as data; no Django imports or database access
occurred. Git used `GIT_OPTIONAL_LOCKS=0`; Python used `-B`. Byte hashes were
retained, with CRLF normalized only in the local comparison, never on the server.
Settings contents, `.env`, keys, authorization evidence, uploads, database data,
backup-file contents and maintenance/reset implementation contents were excluded.
Static image/font bytes were outside the textual-source comparison.

The comparison has 270 entries: 248 identical after newline normalization,
11 different, eight local-only, one server-only, one settings hash comparison
that matches, and one root-owned untracked test intentionally not read.
This is a snapshot, not an atomic freeze of the live tree; repeat hashes under
an approved quiet window before any eventual release.

Ignored local evidence (do not deploy or commit the source snapshots):

- `.tmp/reconciliation/server-source.json`: full selected server HEAD/worktree
  source, hashes and file metadata.
- `.tmp/reconciliation/server-uncommitted.diff`: complete selected server changes
  relative to its own HEAD, including all eight modified tracked files.
- `.tmp/reconciliation/server-to-local.diff`: full selected textual worktree
  differences, including relevant untracked source. Direction is server to local;
  **do not apply this as a patch** because it removes working server behavior.
- `.tmp/reconciliation/comparison.json`: exact per-file comparison/inventory.
- `.tmp/reconciliation/server-environment.json`: installed package metadata and
  untracked-file ownership, without application data or credentials.
- `.tmp/reconciliation/server-helper-hashes.json`: installed helper fingerprints.

## Exact application reconciliation proposal

The server has exactly the eight reported modified tracked files. Four already
match local HEAD/current code. The proposed application changes are limited to
the four differing tracked files below and importing one server-only module.

| File | Verified difference | Proposed disposition, after approval |
| --- | --- | --- |
| `accounts/urls.py` | Server adds `access-code/`, named `auth_access_code`; local omits it | Add the existing server route locally, preserving all local routes. Never replace this file wholesale with its local version during release. |
| `accounts/views.py` | Server adds `access_code_login()`; other collected content agrees | Port that function with its current validation/response contract. Preserve whitespace/hyphen normalization, ambiguous-code rejection, active-account requirement, Judge/Scorer roles, existing token behavior and `role` response. Do not collect actual access codes or tokens. Add auth regression tests before release. |
| `events/api_urls.py` | Live dashboard uses `judge_mobile_api.judge_dashboard`; local uses a different fallback. Notifications path and route names differ | Merge deliberately: retain the server dashboard import/view/path and existing production URL names; retain all assignment/viewer/router endpoints. Keep live `judge/notifications/` and add local `judge-notifications/` as an alias to the same existing notifications view. Preserve any local underscore URL names needed by callers via explicit aliases, without changing production hyphenated names. Test both paths and reverse lookups. |
| `events/judge_mobile_api.py` | Untracked server-only 82-line module; absent locally | Bring the exact reviewed server source under local Git after approval, retaining the deployed dashboard implementation. It passes the existing source allowlist once tracked. Do not depend on an untracked server-only file surviving forever. |
| `requirements.txt` | Server adds only `reportlab==5.0.1` | Carry this exact production pin into the proposed local requirements; test it in a disposable environment first. Preserve all existing pins/ranges. Do not downgrade production packages or run pip on production as reconciliation. |
| `events/criteria_mobile_rounds.py` | Modified on server but identical locally | Keep local code; no merge needed. Preserve read-only round resolution and stable mappings. |
| `events/judging_serializers.py` | Modified on server but identical locally | Keep local code, including scoped detail reads, submitted-state interpretation and typed nonempty score input. |
| `events/judging_views.py` | Modified on server but identical locally | Keep local code, including assignment/round checks, transactions, idempotent receipts, own-score reads and submission error handling. |
| `events/models.py` | Modified on server but identical locally | Keep local code. The shared modification isolates optional secondary audit failure inside a savepoint; it adds no model field or schema change. Preserve mandatory primary activity logging. |

The dashboard conflict is behavioral, not cosmetic: the server computes today's
and upcoming assignment lists and `stats.total_events/ongoing_events/completed_events`; the local
fallback supplies different stats keys, detailed projections, and empty today/
upcoming lists. The live view also selects legacy assigned JudgingEvents, while
the shared `mobile_judge.assigned_events()` applies portal ownership rules.
Do not silently substitute these implementations or alter their authorization
semantics as part of deployment. Preserve the existing live view first and test
its contract with the installed mobile client in staging. Any ownership-policy
change needs separate review.

The local `MOBILE_API_DEPLOYMENT.md` explicitly says to retain the deployed
dashboard. The reconciliation above follows that existing instruction.

## Shared untracked server modules: already present locally

These six files are untracked on the server but tracked locally and match in
full after newline normalization. Keep the local versions, include them through
normal committed-source packaging, and verify final hashes. No copying or
scoring rewrite is necessary:

- `events/mobile_configuration.py`
- `events/mobile_historical_review.py`
- `events/mobile_judge.py`
- `events/mobile_scoring_compatibility.py`
- `events/mobile_submit_support.py`
- `events/mobile_viewer.py`

Normal Vocal Duet judging uses the shared scoring/runtime services, not the
development reset command. Preserve existing round IDs, candidate/entry mapping,
criterion/subcriterion IDs and weights, submitted locks, receipt IDs, retries,
Faculty gates, final approval and publication visibility. No live scoring data
was inspected; source equality is not a claim that scores or mappings were
validated against production data.

## Local changes to retain

| Files | Disposition |
| --- | --- |
| `core/faculty_views.py`, `frontend/facultydash/dashboard.html` | Retain local criteria publication queue/status/count/filter fix. These are missing on the server and do not overlap the server's eight edits. Preserve approval/publication authority. |
| `core/test_faculty_criteria_portal.py`, `events/tests.py` | Retain local regression coverage and previously reviewed test corrections. Tests stay in Git, outside the application archive. |
| `events/test_faculty_mobile_rounds.py`, `events/test_mobile_scoring_readiness.py` | Retain newer local assertions: identical submitted-score retries return the existing receipt with HTTP 200 and do not duplicate normalized submissions. Server runtime already matches this implementation; its tests still expect 400. Do not revert the runtime to satisfy stale server assertions. |
| `events/test_mobile_api.py` | Retain broader local tests for portal assignment access and canonical preview/progress. Add live dashboard, both notification routes/names, and access-code contract coverage after approval. Server's untracked version lacks some local tests. |
| `events/test_judge_portal_flow.py`, `events/test_judge_score_schema_repair.py`, `events/test_mobile_configuration.py`, `events/test_mobile_historical_review.py`, `events/test_mobile_submit.py` | Local-only tests: retain in Git, never deploy or execute schema-repair/maintenance tooling on production during reconciliation. |
| `events/test_legacy_migration_repairs.py` | Retain local-only replacement-migration regression coverage; excluded from application archive. |
| `events/migrations/0018_squashed_0020_fresh_schema.py` | Retain additive replacement; missing on server. Separate file-only approval and read-only compatibility checks are required before deployment. Do not run migrations to register it. |
| Original `0019_align_team_schema.py`, `0020_team_delete_set_null_on_legacy_match.py` and other collected original migrations | Source matches. Keep originals unchanged. The reported completed production history through 0044 was not queried in this source-only task. |
| `deploy.ps1`; `execution/deploy_checks.py`, `deploy_release.py`, `deploy_server.py`, `local_test_database.py`, `test_deploy.py`; `EVENTTAB_DEPLOYMENT_GUIDE.md`, `FIRST_DEPLOYMENT_REVIEW.md`, `MIGRATION_COMPATIBILITY_REVIEW.md` | Retain reviewed local automation/tests/docs. They are not normal application archive members. Installed helper reconciliation is described below. |

`events/test_vocal_duet_development.py`, `events/test_vocal_duet_host_identity.py`
and `events/test_vocal_duet_reset.py` match their untracked server counterparts;
retain local tests without deploying/running maintenance on production.
`events/test_vocal_duet_owner_evidence.py` is local-only in the source inventory;
keep it as maintenance regression coverage, excluded from deployment.
`events/test_vocal_duet_simple.py` exists on both sides, but the root-owned server
copy was not read or claimed equal; retain the local test and keep the server
artifact outside this application merge/deployment.

## Explicit exclusions

Do not deploy, overwrite, normalize permissions, import into ordinary runtime,
or execute any of these as part of reconciliation:

- `accounts/urls.py.before-access-code`, `accounts/views.py.before-access-code`,
  `events/api_urls.py.backup`.
- `events/management/commands/inspect_judge_mobile_history.py`,
  `inspect_judge_portal.py`, `inspect_mobile_submit_schema.py`,
  `prepare_judge_mobile.py`, `reset_development_vocal_duet.py`,
  `reset_vocal_duet_test_scores.py`, `vocal_duet_simple.py`.
- `events/vocal_duet_development.py`, `vocal_duet_host_identity.py`,
  `vocal_duet_owner_evidence.py`, `vocal_duet_reset.py`, `vocal_duet_simple.py`.
- Root-owned authorization/evidence/signature files, owner helper scripts,
  maintenance SQL under `execution/sql`, `deploy-mobile-api` staging copies,
  secrets/keys/`.env`, media/uploads, database files/dumps and local snapshots.

Some maintenance files are already in local Git; their presence in Git does not
authorize deployment. The existing allowlist excludes management commands,
`vocal_duet*` modules, tests, execution tooling and other non-runtime roots.
Only explicitly named design images are allowed from `assets`; user media stays
excluded. Do not broaden that policy to reconcile a maintenance file.

Metadata-only inspection found root-owned `vocal_duet_simple.py` command,
`events/vocal_duet_development.py` and its simple test with mode 0666. Root
ownership does not make those writable files trusted. Keep them out; have the
owner review their maintenance permissions separately. No chmod/chown or
maintenance-file content collection was performed.

## Dependency compatibility review

The complete requirements diff is one added line: `reportlab==5.0.1`. All other
declared dependency lines match. Server requirements raw SHA256:
`f74239e40b816d1d71b1e67d28c5b7b419f0c6d1f00b2f187a20cf7f5e5ef56f`.

Read-only distribution metadata from the production venv establishes:

| Component | Production | Local Windows | Interpretation |
| --- | --- | --- | --- |
| Python | 3.12.3 | Existing local venv | Rehearse with production Python 3.12, not only Windows |
| Django | 6.0.3, requires Python >=3.12 | 6.0.3 | Production Python meets declared requirement |
| DRF | 3.16.1, declares Django >=4.2 | Existing pinned requirement | Django 6.0.3 meets declared dependency bound; metadata alone does not prove behavior |
| ReportLab | 5.0.1, requires Python >=3.9,<4 | 4.5.1 | Real dependency divergence; do not claim local PDF tests cover the server version |
| Pillow | 12.3.0 | 12.2.0 | Server satisfies ReportLab's Pillow >=9 requirement |
| charset-normalizer | 3.5.2 | 3.4.7 | Required ReportLab dependency is installed on server |

Server also has psycopg2-binary 2.9.12, python-decouple 3.8, Cloudinary 1.46.3,
django-cloudinary-storage 0.3.0 and pytz 2024.1, matching declared pins/range.
Local `pip check` reports no broken installed requirements. That check does not
detect an undeclared dependency or prove production compatibility. No remote
pip installation, upgrade, dependency resolver or package mutation was run.

ReportLab is already imported by reports, audit PDF export, score sheets and
header rendering. Dropping the production pin would leave clean installs unable
to reliably reproduce those functions. Recommend retaining 5.0.1 because it is
the existing production version, then testing PDF generation, headers/logos,
font/image rendering and exports using production-compatible versions in an
isolated Linux environment. Do not downgrade production to local 4.5.1.
Keep dependency approval/hash and installed-package inventory gates intact.
If the final pin merely matches the existing server, reconciliation need not
install or change any production package. Verify the root-approved requirements
hash separately before release; configuration was not read in this task.

## Deployment helper reconciliation and new blocker

Both installed root-owned helpers are mode 0644 and match local LF source:

| Helper | SHA256 |
| --- | --- |
| `deploy_server.py` | `0c47e97cc71247d037f0c8441f31e74c0f076f85044e131f014a7a153be96409` |
| `deploy_release.py` | `625523b95e3c2774475e3617798cce0fe66a0170ff89ef2f8644faca24e80aeb` |

No helper upload is needed to reconcile these observed bytes. Do not assume
configuration, checkpoint state or systemd integration was verified from hashes.

**P1: source-permission incompatibility is confirmed.** Existing application
files such as `core/urls.py`, `core/settings.py`, `events/models.py` and shared
mobile modules are owned by UID 1000 (`eventtab`) with mode 0664. The current
helper's `trusted_artifact()` rejects group/other-writable artifacts and their
ancestors. Its new checkpoint preflight also checks unchanged source files, so
these files block first deployment before service stop, even if code hashes match.

Plan a separate, owner-approved, narrowly scoped permission review for managed
source and relevant ancestor directories. Preserve the baseline metadata backup
first, prove service/collection/rollback access in a replica, and identify the
exact files/directories before any future permission change. Do not recursively
chmod/chown the project, media, `.env`, root-controlled maintenance or evidence.
Do not weaken `trusted_artifact()` to make production preflight pass.

## Proposed order of work, requiring approval before code changes

1. Approve this file-by-file plan. Preserve current local work and the read-only
   source snapshot. Use a separate local reconciliation branch/checkout when
   implementing; no reset/clean/pull of either dirty tree is needed.
2. Port only the access-code function/route, exact live dashboard module and
   deliberate URL merge. Add the ReportLab pin. Keep all shared scoring/model
   code and local faculty fixes intact. Present the resulting diff for review;
   do not automatically merge, stage or commit.
3. Add contract tests for live dashboard JSON keys/status lists, notification
   paths/names, access-code success and invalid/ambiguous/disabled/wrong-role
   rejection, ordinary token login and unauthorized judges. Never use live
   access codes, tokens or private data in tests.
4. In isolated PostgreSQL, verify Vocal Duet and other score submissions,
   stable IDs/weights, duplicate-retry receipts, historical locked cells,
   normalized scores, audit transaction safety, Faculty round approval and
   ready queue, authorized publication and public visibility. Run the full
   application suite without disabling migrations/tests, plus deployment tests.
   Rehearse on Linux/Python 3.12 with the reviewed production dependency versions
   and verify PDF output. None of these tests may use the live database.
5. Before an eventual release, obtain the separately approved baseline backup
   and isolated restore verification, source-permission plan, migration-file-only
   approval and read-only empty-plan/history checks. Reconcile approved
   requirements/config hashes, preserve all excluded artifacts, and complete
   real systemd/Nginx deployment and rollback rehearsal in an isolated replica.
6. Recompare server hashes/HEAD against the frozen inventory; investigate drift.
   Present exact commit contents and final manifest for approval. Only after
   explicit later deployment authorization may the normal gated workflow run.

Local code reconciliation is implemented as recorded above. Deployment remains
blocked pending the dependency-pin decision, production-equivalent PDF verification,
source permissions, replacement-file approval, baseline backup verification,
approved clean commit and systemd/Nginx rehearsal. Use the new test results above
for the reconciled local version rather than historical pre-reconciliation passes.

In the original read-only comparison phase, no server file, Git history,
permissions, service, package or production data
was intentionally modified. No upload, migration, merge, commit, deployment,
reset, clean, pull or restart was performed. That phase created only this plan
and ignored comparison artifacts; the later authorized local implementation is
recorded at the beginning of this document.
