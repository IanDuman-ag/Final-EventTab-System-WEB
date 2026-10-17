# First controlled deployment review

Review date: 2026-10-16. **Verdict: REQUEST CHANGES / NO-GO for deployment.**
This review and subsequent rollback fix are local only. No SSH, upload,
production database connection, production deployment, migration execution,
Git staging, or Git commit was performed. Local deployment/rollback simulations
ran only against disposable test directories and an isolated Linux container.
The operator reports that the helper/configuration are installed, `-Status`
succeeded, and `-DryRun` passed. Their installed bytes and server baseline have
not been supplied for comparison. Local success does not verify those bytes.

## Proposed commit contents: approval required

All 14 initially dirty/untracked files belong to the proposed first-release
change set. Add this review as the fifteenth file. Nothing should be committed
automatically. Review the complete diff and these exact paths before staging:

| Paths | Purpose | Application archive? |
| --- | --- | --- |
| `core/faculty_views.py` | Include approved criteria results in the publication queue, ready count/filter and result status | Yes |
| `frontend/facultydash/dashboard.html` | Describe criteria readiness and link to publication status | Yes |
| `core/test_faculty_criteria_portal.py` | Queue, pending and published regression coverage | No |
| `events/tests.py` | Correct outdated fixtures/assertions and prevent an inline thread mock from closing the test transaction | No |
| `events/migrations/0018_squashed_0020_fresh_schema.py` | Additive fresh-schema replacement, retaining the originals | Yes; separate file approval required |
| `events/test_legacy_migration_repairs.py` | Real PostgreSQL fresh/legacy/completed-history migration coverage | No |
| `deploy.ps1` | Windows entry point and confirmation/gates | No; runs locally |
| `execution/deploy_release.py` | Committed-byte packaging, allowlist and archive validation | No; also separately installed server support |
| `execution/deploy_server.py` | Server preflight, checkpoint, switch and rollback helper | No; separately installed, root controlled |
| `execution/deploy_checks.py` | Isolated application checks/test gate | No |
| `execution/local_test_database.py` | Provision/verify a disposable local PostgreSQL cluster | No |
| `execution/test_deploy.py` | Offline deployment safety tests | No |
| `EVENTTAB_DEPLOYMENT_GUIDE.md` | Setup, operational workflow and test investigation | No |
| `MIGRATION_COMPATIBILITY_REVIEW.md` | Migration decision, SQL effects and compatibility evidence | No |
| `FIRST_DEPLOYMENT_REVIEW.md` | Release manifest proposal, blockers and first-release checklist | No |

The only application behavior change is faculty queue/status presentation.
The test corrections do not change application scoring, publication authority,
or authentication. The criteria queue link does not grant direct publication.
The replacement changes fresh database construction, not the completed server
schema. No settings, credentials, dependency versions or production data changes
are proposed.

The archive contains the **whole tracked allowlisted application**, not just
these three changed runtime files. Thus other server-only fixes could be
overwritten on the first deployment. Tests, deployment tooling and documents
belong in Git but are deliberately excluded from the application archive.
Untracked files are absent from the dry-run manifest even though Django can
import an untracked migration during local tests. Repeat the default full
dry-run after the approved commit and verify replacement-file inclusion.
Keep `.tmp`, databases, `.env`, uploads, backups and credentials excluded.

## Findings blocking release

| Priority | Location | Finding and required resolution |
| --- | --- | --- |
| P1 | `execution/deploy_server.py`, `deploy()` current-manifest drift check | An unmanaged baseline has no recorded manifest. Its existing files are not checked against an approved baseline before replacement. Obtain a complete inventory, reconcile every difference with the committed release, freeze edits and approve the resulting replacement list before proceeding. Do not manufacture `current.json` to pretend a deployment occurred. |
| P1 | Server inventory / Linux integration | Installed helper hashes, unit hooks, filesystem permissions and real Linux deploy/recovery behavior are unverified. Rehearse on an isolated Linux replica and compare installed helpers before approval. Inspect the full systemd unit/drop-ins: `User` and `WorkingDirectory` alone do not prove its start hooks avoid automatic migrations. |
| Resolved locally; server update pending | `execution/deploy_server.py`, `rollback()` before `restore()` | The revised helper validates the complete checkpoint before journaling/stopping, then rechecks source/static inputs immediately before restoration. Regression coverage and isolated Linux HTTP-process recovery pass. The corrected helper still needs separate approval, installed-byte reconciliation and systemd/Nginx rehearsal before production use. |
| Gate | Migration-file comparison in `deploy()` | The untracked replacement is not approved on the server. Ordinary deployment correctly blocks a missing/different migration file. Follow the explicit procedure below; never relax this gate. |
| Gate | Git working tree | Changes remain uncommitted. Approval of this exact manifest, any corrective helper changes, and a clean reviewed commit are required before packaging. |

The original rollback finding was reproduced using the existing offline `ServerFlowTests`
fixture: deploy in a temporary directory, corrupt its source backup, invoke
confirmed manual rollback. Result: `Rollback backup verification failed:
frontend/page.html`, a recorded `stop` call, and a retained recovery journal.
No real service, SSH or database was involved. At that time existing corruption
tests called `restore()` directly and did not catch the preceding service stop.

The subsequent authorized local fix adds format-2 checkpoints containing the
release manifest, complete baseline hashes and independent baseline-settings
hash. `current.json` and the pending journal bind a release ID to its checkpoint
SHA256. Before service stop, validation checks that reference, archive/manifest,
deployment state, previous release receipt, exact backup inventory, source and
static hashes, safe paths/permissions and dependency/history compatibility.
Corrupt, missing, incompatible or legacy checkpoints abort without changing the
application or journals. The original source/static validation also runs again
inside `restore()` before any restoration writes. Concurrent changes after a
successful preflight still block restoration and leave the recovery journal.

These hashes detect corruption within the existing deployment-account trust
boundary; they are not signatures against a compromised deployment account.
Linux rejects symlinks and artifacts/ancestors owned by other users or writable
by group/others. Checkpoints without the format-2 receipt need separate operator
review; do not synthesize a receipt for an old checkpoint to bypass validation.
Retain incoming release archives along with checkpoints/backups: rollback now
validates their manifest and byte hashes, including the previous managed release.
The installed version remains unknown and unchanged by this local fix.

## Migration decision and file-only approval procedure

**Retain the additive replacement; keep original 0019 and 0020 unchanged.**
The replacement depends on 0017, replaces 0018/0019/0020, and creates the same
Team migration state as original 0018. Fresh databases avoid legacy-only SQL.
An existing history with all three originals applied resolves the replacement
virtually; read-only graph/plan checks need no schema or recorded-history writes.
The regression fixture completed the original history through 0044 and verified
unchanged schema, constraints, rows and recorded applied timestamps.
Partial histories are outside this first-release approval.

Reviewed SHA256 values of original Git blobs:

| File | SHA256 |
| --- | --- |
| `0019_align_team_schema.py` | `e4321be0f1c0c7d29a28c476d990775945c25e718aedf87cdc8db1f2fa98574c` |
| `0020_team_delete_set_null_on_legacy_match.py` | `5eebdaeb3aecd55ab925bd728132a6654be19c77cbccd144a02fa89c0db2930c` |

The current replacement candidate with LF newlines has SHA256
`e03be2e4c43576fc2efb7cc9640bb096ae3dfe4484e3c68cba94c8f7a412810b`.
This is a review fingerprint, not an authorization. The final **committed blob
and validated archive manifest** must independently reproduce it before use.
Do not transfer the Windows working-tree file and assume its CRLF hash matches.

Future operator procedure, requiring separate explicit authorization; none of
these server actions is authorized or executed by this review:

1. Obtain approval for the Git manifest and create the reviewed commit. Build
   a local archive using `execution/deploy_release.py`; do not upload it yet.
   Validate its SHA256, manifest, replacement bytes and unchanged originals.
2. Obtain the server inventory and independent baseline backups described below
   **before** installing the replacement. Preserve the migration directory,
   complete applied history including timestamps, permissions and hashes.
3. On an isolated Linux replica of the original completed history, introduce
   exactly the reviewed file and execute only loader consistency/empty-plan
   checks. Compare recorded history before/after, including timestamps. Require
   all original events migrations through 0044 and no unexpected pending leaves.
4. Request explicit file-only approval naming the server/application path,
   committed SHA, replacement path/SHA256, baseline backup reference and
   evidence. Suggested scope: "Install only the reviewed replacement migration
   file with SHA256 [verified value]; retain all original files; execute no
   migrations and make no migration-history changes."
5. Only after that approval, an authorized operator installs the exact archive
   bytes atomically at the corresponding application migration path. If the
   target already exists with different bytes, stop and investigate. Preserve
   the recorded prior absence/content and file ownership/mode. Do not modify
   root configuration, requirements approvals or the deployment helper gate.
6. Repeat the read-only consistency/empty-plan and history comparison on the
   approved server. Prefer a read-only database session/role so checks cannot
   write. Do not invoke the executor's `migrate()` or a management `migrate`
   command, even with no pending schema operations: it can record the alias.
7. Require every migration file in the release to match the reviewed server
   byte hash. Any other mismatch needs its own review. This lets the existing
   migration-file gate pass through its normal equality check.

The deployment checkpoint is created **after** file-only maintenance. Code
rollback therefore retains that approved replacement. Reverting the migration
file installation is separate maintenance, not automatic code rollback.
Never remove originals or fake records to make a plan pass.

## Required divergence and backup evidence

Actual local/server differences are **unknown**; a successful status command
does not export source or installed-helper hashes. Before release, the operator
must supply a privately stored inventory for offline comparison containing:

- SHA256 and size for all incoming application paths, marking missing paths;
  separately enumerate server-only source/templates/static design files and
  symlinks. Do not recursively hash/upload user media or secret contents.
- Raw server hashes compared against committed Git bytes, not Windows checkout
  bytes. Classify each difference as intended release change, newline-only
  difference, server-only fix needing preservation, or unexplained blocker.
  Do not normalize production files as part of inventory collection.
- Hashes of both installed helper modules, approved requirements hash and the
  actual requirements bytes; privately compare relevant configuration, systemd
  unit/drop-ins, Nginx routes, Python version and installed package inventory.
  Do not publish `.env`, config secrets or machine identifiers in this repo.
- Applied migration names/timestamps, consistency and empty-plan evidence,
  current baseline health, app/static directory metadata, free space and
  effective sudo permissions. Capture these under separate server authorization.

The helper backs up only changed managed files and, when needed, collected
static files. For the first release, `previous` is empty and rollback returns
to that unmanaged baseline. It does not remove unrelated server files, restore
SQL/media/config/venv, or preserve original source mode/ownership (replacement
uses mode 0644). Require compatible source permissions or resolve this in a
reviewed helper change. Server-only modules/templates remain in the live overlay,
while staged checks use a clean release directory; review these extras too.

Before first file-only maintenance, obtain an independent, protected baseline
backup of source with metadata, static files, uploads/media, configuration,
service configuration and the database via the existing approved backup process.
Keep credentials and backup data outside Git/releases and restrict access.
Record timestamps, checksums, locations, retention and recovery owner. Verify
archive integrity and restore to an isolated Linux/PostgreSQL environment;
listing a database dump alone is not a restore test. Keep restored private data
isolated and disable external email/storage/jobs. Retain a verified copy outside
the server failure domain. No database restore belongs in automatic code rollback.

## First deployment checklist, in order

1. **Local approval:** review the locally resolved rollback finding and all fifteen proposed
   paths plus corrective helper/tests; obtain commit-content approval. Only then
   stage those exact paths, inspect the staged diff for secrets, commit, and
   require clean `git status`. Do not alter 0019/0020 or unrelated files.
2. **Full local gate:** run `.\deploy.ps1 -DryRun` with its default full suite.
   Require 470 application tests / 469 passed / one expected Windows skip,
   deployment safety tests passing, system checks and migration drift checks.
   Investigate any changed count/skip. Confirm the replacement is in the
   now-tracked prospective manifest; build/validate the committed archive locally.
3. **Inventory approval:** under future separate authorization, obtain the
   evidence above. Compare all incoming paths and server-only files offline;
   resolve unexpected differences. Pin local/archive/helper hashes, baseline
   inventory and a quiet maintenance window. Preserve console/recovery access.
4. **Backup verification:** complete the independent baseline backup and isolated
   restore verification before migration-file installation or code deployment.
5. **Linux rehearsal:** use an isolated Linux replica with the same layout,
   unprivileged account, helper/config permissions, venv packages, systemd and
   Nginx behavior. Keep it disconnected from live DB/services. Verify first
   deployment from no current manifest, changed source/static collection, real
   HTTP health, explicit rollback to baseline and a second deployment. Exercise
   failed health/partial copy, interrupted recovery, lock contention, corrupted
   checkpoint rejection before service stop, and unchanged migration history,
   config and uploads. Unit mocks and a Windows run do not replace this evidence.
6. **File approval:** perform the separately authorized replacement-file-only
   procedure. Verify no SQL/history write and all migration hashes match.
   Reconcile installed helper hashes with the approved and rehearsed version;
   separately authorize helper installation if a corrected version is needed.
7. **Final go/no-go:** require baseline health, no pending recovery journal,
   sufficient disk, valid checkpoint/backup plan, unchanged baseline inventory,
   matching requirements and unchanged package/history inventories. Inspect full
   service hooks to prove restart cannot automatically run migrations. Freeze
   concurrent code, config, dependency and schema maintenance.
8. **Future authorized release:** only after all blockers close and deployment
   is explicitly authorized, run `.\deploy.ps1`, review release/commit/SHA256 and
   type `DEPLOY`. Keep every existing gate enabled. This review does not grant
   that deployment authorization.
9. **Immediate health:** verify service active, local Nginx `/api/health/` returns
   HTTP 200 JSON with `status=ok`, `backend=django`, and the normal external URL
   works. Inspect private service/helper logs for errors, verify installed
   manifest hashes, current release ID, checkpoint backup hashes, no pending
   journal, unchanged migration history/dependencies, and unchanged config/media.
10. **Manual faculty QA:** first exercise the write workflow on the isolated
    replica: judge scoring/submission, faculty round approval and final submission;
    confirm the criteria event enters Publication Queue, increments Ready count,
    appears in Ready filter, shows "Final round approved" and links to publication
    status without granting faculty unauthorized direct publication. Confirm
    authorized publication removes it from the queue and public results match
    approved scores/rankings. Check match confirmation, department-mapping gates,
    placements, explicit publication confirmation and unauthorized-account denial.
    On production, compare existing approved scores/ranks and statuses read-only.
    Any production write test needs separately approved disposable event/accounts;
    do not resubmit, publish or unpublish real event results as a smoke test.
11. **Recovery/acceptance:** on health failure inspect the recorded automatic
    rollback outcome and restored health. If manual recovery is authorized,
    `.\deploy.ps1 -Rollback` requires `ROLLBACK` and the release ID. Recheck source,
    static files, health, read-only faculty pages and migration history afterward.
    On corruption/drift, stop and preserve the journal; use the rehearsed operator
    recovery process rather than deleting the gate. Do not restore the live DB
    merely to undo source changes. Retain baseline/first checkpoint until release
    acceptance and an explicit retention decision.

## Verification performed for this review

Updated rollback-fix verification:

- Full deployment safety suite on Windows: **62 tests, 59 passed, three Linux-only
  skips**, zero failures/errors.
- Isolated Linux server-flow/database-isolation suite: **51 tests passed**, no
  skips. Ran as UID/GID 65534 in a disposable cached Linux container with network
  disabled, read-only test source/root filesystem and a private 1 GiB tmpfs.
- Linux includes real loopback HTTP subprocess availability across successful
  rollback, corrupt-backup refusal without interruption, and simulated failed
  deployment with automatic baseline recovery. It also verifies interrupted
  manual recovery, artifact permissions, file hashes and actual flock behavior.
  Systemd, database and dependency probes remain mocked in these tests; this
  does not establish production systemd/Nginx integration readiness.
- An initial full Linux attempt failed because the cached image lacks Git and
  a 128 MiB tmpfs correctly failed the existing 256 MiB minimum-space gate. No
  gate was relaxed: the final Linux run used 1 GiB and the server-flow/isolation
  suite; Git/archive packaging coverage ran in the full Windows suite.
- Logs: `.tmp/deploy/rollback-safety/windows-tests.log` and `linux-tests.log`.
  No application/scoring/migration code changed; the full application suite was
  not repeated for this helper-only fix.

Original review evidence (before the rollback fix):

- Deployment safety suite rerun locally: **39 tests, 38 passed, one Linux-only
  lock skip**, zero failures/errors. These server flow tests use mocked services,
  health, environment and migration probes; they are not Linux integration proof.
- Offline corruption/rollback probe reproduced the service-stop finding above.
- Original 0019/0020 compared with HEAD after checkout newline normalization:
  identical Git contents; neither has a Git diff. Git whitespace check passed.
- The previously recorded full application run is **470 tests, 469 passed,
  one expected Windows `O_NOFOLLOW` skip**, with actual disposable PostgreSQL
  migrations. The operator also reports a passing full dry-run. The full
  application suite was **not rerun during this documentation/review-only task**.
- No live inventory, backup restore or production-equivalent systemd/Nginx
  integration evidence is available. The isolated Linux process tests above
  cover the local rollback fix.
  Deployment remains blocked until the findings and checklist evidence close.
