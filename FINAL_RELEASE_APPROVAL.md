# EventTab first-deployment approval package

Review date: 2026-10-17, Asia/Manila. **GO to isolated Linux rehearsal preparation
and candidate verification; NO-GO for production deployment.**
The operator approved local ReportLab alignment and retention of the additive
replacement. The exact candidate is prepared locally for final commit-content
review. Real end-to-end helper rehearsal still requires a clean
reviewed commit/archive through the existing packaging gate; do not bypass that
gate or label a dirty-tree manifest a committed release. No staging/commit is
authorized by this review. Rehearsal is the next phase, not evidence already done.
This document supersedes stale current-status claims in FIRST_DEPLOYMENT_REVIEW.md
and RECONCILIATION_PLAN.md; retain those documents as historical evidence.
In the current local-candidate preparation, only requirements.txt and this review
were edited outside ignored artifacts. No Kamatera connection, staging, commit,
production installation/configuration change, production migration or restart
occurred. Production observations below are saved evidence from earlier reviews,
not fresh checks in this local-only task. Tests use disposable local databases.

## Findings and decisions

| Priority/gate | Finding | Required disposition |
| --- | --- | --- |
| Resolved | Both approved permissions batches completed: 260 files and 24 directories in total | Independent post-verification passed. Fresh full-archive scan has zero group/other-writable managed files or required ancestors. No further permission changes proposed. |
| Resolved locally | Approved reportlab==5.0.1 line appended to requirements.txt | Actual LF working bytes match saved production/root-approved hash; verify staged/committed bytes again. No package installation occurred. |
| Gate | Additive replacement migration is absent on production | Separate file-only installation approval and read-only graph/history checks; follow the procedure below. |
| Gate | Working tree is dirty and deployment files are untracked | Approve the exact commit manifest, stage only its paths, review staged bytes and make a clean reviewed commit under separate authorization. |
| Gate | Production-equivalent Linux/Python 3.12/systemd/Nginx rehearsal is incomplete | Complete the isolated integration/recovery matrix below; Windows and mocked service tests do not replace it. |
| Gate | First release has no managed current.json | Approve the entire committed archive-to-live comparison under a quiet window. Keep current.json absent until a genuine successful deployment; never synthesize it. |
| Resolved against saved evidence | Root-owned configuration approves the actual local requirements hash; installed helpers previously matched reviewed LF source | Keep production configuration/packages unchanged. Current local requirements now pass the existing hash gate against the saved approved value. |
| Gate | Complete production-equivalent service/static behavior and backup retention acceptance remain release checks | Rehearse privately with no live services/data. Operator-confirmed isolated source extraction and DB restore evidence is accepted; complete checksum/receipt binding and full integration remain separately documented limits. |

Both production remediation batches completed successfully: 239 plus 21 files
0664 to 0644 and 15 plus nine directories 0775 to 0755, totaling **260 files and
24 directories**, with independent verification and healthy service. Their
exact manifests and private audits remain under .tmp/reconciliation/. The
operator explicitly accepted residual manual/external group-only writer risk.
No scheduled-job absence was treated as proof that manual writers do not exist.

The current exact 26-path commit proposal, 261-path prospective archive and
change comparison against the previous proposal are under
.tmp/reconciliation/local-release-candidate/. The previous 26-path proposal
already included requirements.txt; the actual working set is now 26 paths,
with no path addition/removal. This approval document's fingerprint is updated.
The requirements bytes are the exact previously reviewed candidate. No other
app/helper/migration implementation changed in this preparation.

## Migration decision

**Operator-approved retention of events/migrations/0018_squashed_0020_fresh_schema.py additively.**
This is local candidate approval only; production migration-file installation
and live deployment remain unauthorized.
It depends on 0017 and replaces exactly 0018_team_model,
0019_align_team_schema and 0020_team_delete_set_null_on_legacy_match. Its only
operation creates the canonical Team from 0018. It deliberately does not replay
the legacy abbreviation rename or legacy events_match FK repair on a fresh DB.
The regression suite compares complete migration model states, exercises fresh
forward/reverse/replay, actual partial legacy repair SQL, and completed 0044
history with unchanged rows, constraints and recorded timestamps.

The refreshed read-only production check at 2026-10-17 05:41:56 UTC confirmed consistent
history and an empty current migration plan. All 44 current original events
migrations through 0044 are applied. Production has 53 events history records:
nine older branch records are absent from the current disk graph. Preserve all
53 rows and timestamps, including those nine; do not fake, delete or prune them.
The actual production-history replica must preserve this full history rather
than substituting the smaller synthetic fixture history.

| File | Reviewed Git/LF SHA256 |
| --- | --- |
| 0018_squashed_0020_fresh_schema.py | e03be2e4c43576fc2efb7cc9640bb096ae3dfe4484e3c68cba94c8f7a412810b |
| 0019_align_team_schema.py | e4321be0f1c0c7d29a28c476d990775945c25e718aedf87cdc8db1f2fa98574c |
| 0020_team_delete_set_null_on_legacy_match.py | 5eebdaeb3aecd55ab925bd728132a6654be19c77cbccd144a02fa89c0db2930c |

0019 and 0020 have no Git diff, and their checkout content normalized to LF
matches exact HEAD blobs. No historical migration was edited. Their current
production hashes are also checked in the machine evidence.

For completed original history, Django resolves the replacement virtually and
should schedule no schema operations. Do not run manage.py migrate: its
replacement bookkeeping can record the alias even with an empty schema plan.
Partial canonical histories, missing original records, altered originals or
mixed legacy schemas require separate investigation and are outside approval.
Fresh reversal deletes Team data and is only a disposable-test exercise, not a
production recovery strategy. This follows Django's coexistence mechanism:
https://docs.djangoproject.com/en/6.0/topics/migrations/#squashing-migrations.

## Dependency decision

**Approved and applied locally: append only reportlab==5.0.1.** The only Git
text diff is that line; the file uses the reviewed LF bytes so its actual raw
SHA256 matches the saved production fingerprint. requirements.txt and the prior
candidate .tmp/reconciliation/proposed-requirements.txt both have SHA256
f74239e40b816d1d71b1e67d28c5b7b419f0c6d1f00b2f187a20cf7f5e5ef56f.
Those bytes match the previously saved production requirements and root-owned
configuration approval. Saved production metadata reports ReportLab 5.0.1; no
server connection was made in this task. No production dependency
install, downgrade or upgrade is needed by this change. Preserve all other
pins/ranges; do not incidentally pin Pillow or charset-normalizer.

The prior read-only inspection of /etc/eventtab-deploy.json confirmed a regular,
non-symlink root:root 0644 file approving exactly that f74239...e5ef56f hash.
Its host-identity comparison passed without exporting the machine identifier or
other private configuration fields. Installed helpers match the reviewed local
LF source; helper parents are root-owned 0755 and deployment state/incoming/
releases/logs are eventtab-owned 0700. The actual requirements_gate() now accepts
the working requirements.txt against that saved approved value. This is a local
test of the existing gate, not a fresh production claim, configuration update or bypass.

Minimal diff (LF candidate bytes; no other line change):

```diff
 pytz==2024.1
+reportlab==5.0.1
```

The local venv remains ReportLab 4.5.1. Application verification explicitly uses
the existing private overlay with ReportLab 5.0.1, Pillow 12.3.0 and
charset-normalizer 3.5.2. The ordinary deploy.ps1 default checks still run the
venv's packages; their output alone does not establish ReportLab 5.0.1 coverage.
Repeat the production-version checks for the final committed candidate and
rehearse PDFs on Linux/Python 3.12. Approve representative rendered PDFs visually;
structural PDF tests do not establish visual equivalence.

## Exact proposed Git scope

The current machine staging proposal is
.tmp/reconciliation/local-release-candidate/commit-manifest.json. It pins the current
base commit, exact path set, working-byte SHA256 and proposed LF SHA256. It is
not a commit or deployable archive. Git attributes and final staged/committed
blobs remain authoritative; inspect them after separately authorized staging.

There are **26 current changed/untracked paths**: ten tracked modifications and
16 new files, exactly matching the previous 26-path proposal. Nothing is staged
or committed. Present these exact paths for operator commit approval:

| Exact path | Purpose | Normal application archive? |
| --- | --- | --- |
| accounts/urls.py | Preserve deployed access-code route | Yes |
| accounts/views.py | Preserve deployed access-code implementation | Yes |
| accounts/test_access_code_login.py | Synthetic auth contract regressions | No |
| core/faculty_views.py | Criteria ready queue/status/filter | Yes |
| core/test_faculty_criteria_portal.py | Ready/pending/published regressions | No |
| events/api_urls.py | Preserve live dashboard and both route-name contracts | Yes |
| events/judge_mobile_api.py | Exact imported deployed dashboard | Yes |
| events/test_judge_portal_flow.py | Preserve live dashboard expectations and receipt checks | No |
| events/test_mobile_api.py | Dashboard/notification/authorization contracts | No |
| events/tests.py | Existing workflow fixture/assertion corrections | No |
| events/migrations/0018_squashed_0020_fresh_schema.py | Additive migration, separate installation gate | Yes |
| events/test_legacy_migration_repairs.py | Real disposable PostgreSQL migration matrix | No |
| events/test_pdf_exports.py | Real PDF rendering regressions | No |
| frontend/facultydash/dashboard.html | Criteria publication-status presentation | Yes |
| deploy.ps1 | Gated local entry point | No |
| execution/deploy_checks.py | Isolated application gate | No |
| execution/deploy_release.py | Exact committed-byte allowlisted packaging | No |
| execution/deploy_server.py | Installed-helper source; no implicit helper installation | No |
| execution/local_test_database.py | Disposable local PostgreSQL isolation | No |
| execution/test_deploy.py | Deployment/recovery safety regressions | No |
| EVENTTAB_DEPLOYMENT_GUIDE.md | Operating guide | No |
| FIRST_DEPLOYMENT_REVIEW.md | Historical review | No |
| MIGRATION_COMPATIBILITY_REVIEW.md | Migration reasoning/evidence | No |
| RECONCILIATION_PLAN.md | Approved reconciliation/historical evidence | No |
| FINAL_RELEASE_APPROVAL.md | Current final approval package | No |
| requirements.txt | Approved one-line ReportLab pin, applied locally | Yes |

Proposed commit title: `Preserve production API contracts and prepare controlled first release`.
No git add, commit, reset, clean, stash or index mutation was performed. Future
staging must use the exact manifest path list, not git add . or directory globs.
After approved edits, regenerate/review fingerprints, inspect git diff --cached
and staged names, verify 0019/0020 unchanged, scan candidate bytes for credentials,
commit only after authorization, then require a clean tree and re-run all gates.

Exclude .env/credentials/keys, all .tmp data and snapshots, database files/dumps,
test output/caches/fixtures generated at runtime, venv, staticfiles, media/uploads,
maintenance/reset tools, owner authorization/evidence/signature files, and
server-only configuration. Regression test source in the table belongs in Git
but never in the application archive. Previously tracked maintenance files are
not newly staged; existing deploy_release.allowed() continues to exclude their
runtime implementations. Existing tracked core/settings.py is normal app source,
has no proposed commit change, and must not be replaced with server secret values.

## Full archive comparison and additional permissions review

The prospective archive contains **261 paths**, not just dirty runtime files.
The current detailed path/hash/disposition inventory is
.tmp/reconciliation/local-release-candidate/prospective-release-manifest.json.
It includes exact production hashes and proposed LF text/raw binary fingerprints.
It is a prospective working-tree comparison, not an assertion about an as-yet
nonexistent release commit. No new archive was uploaded or installed.

Accounts URLs/views and judge_mobile_api still preserve the saved live code.
The intentional content differences are faculty views/template, the deliberate
API alias merge and missing additive migration. requirements.txt now matches the
saved production bytes exactly. Four shared runtime files differ only by CRLF/LF; they retain the approved
scoring, audit and read-only review semantics. Binary design files must be hashed
as raw bytes, never newline-normalized. The inspected binaries match production.
Server-only maintenance and private artifacts are outside this archive and must
remain untouched. Do not apply the old server-to-local.diff as a patch.

Historical second-batch scope (now all verified 0644; no further changes proposed):

- assets/USTP BG.png
- assets/departmentlogo/OSA.png
- assets/departmentlogo/USG.png
- assets/departmentlogo/USTP.png
- assets/landingpagelogo/landingpage.jpg
- assets/landingpagelogo/logoeventtab (1).png
- assets/loginlogo/adminlogo.png
- assets/loginlogo/ustpentrance.png
- events/management/__init__.py
- events/management/commands/__init__.py
- frontend/admindash/vendor/LICENSE.bootstrap
- frontend/landingpage/assets/competitions/badminton.webp
- frontend/landingpage/assets/competitions/basketball.webp
- frontend/landingpage/assets/competitions/dance.webp
- frontend/landingpage/assets/competitions/duet.webp
- frontend/landingpage/assets/competitions/pageant.webp
- frontend/landingpage/assets/competitions/singing.webp
- frontend/landingpage/assets/hero.webp
- frontend/superadmindash/ustp-bg.png
- frontend/tabulator/vendor/LICENSE.bootstrap-icons
- frontend/tabulator/vendor/fonts/bootstrap-icons.woff2

Historical second-batch ancestor scope (now all verified 0755):

- assets
- assets/departmentlogo
- assets/landingpagelogo
- assets/loginlogo
- events/management
- events/management/commands
- frontend/landingpage/assets
- frontend/landingpage/assets/competitions
- frontend/tabulator/vendor/fonts

All are relative to the production application. The exact second batch was
separately approved, including residual risk, applied and independently verified
with standard ACLs and zero discrepancies. assets contains runtime media and
management directories contain excluded maintenance tools; their parent-mode
implications were reviewed. Do not infer permission to change child uploads or
install/run commands. Public Nginx paths /var/www/eventtab/assets and static are
separate real root-owned 0755 trees. No recursive change or trust-gate weakening
was used. These historical lists are retained as scope evidence, not a new plan.

## Separate migration-file installation approval procedure

This is a future operator procedure, not execution authorization:

1. The local dependency decision is approved/applied; separately approve/commit the exact Git scope. Build
   and locally validate an archive from the clean reviewed commit. Pin commit,
   archive SHA256 and the exact replacement blob hash above. Confirm original
   0019/0020 and every other migration file exactly match production. Dirty-tree
   dry-run manifests omit untracked files and cannot prove replacement inclusion.
2. Complete the actual-history Linux rehearsal and verify the two restored
   baseline backups remain private, integrity-checked and recoverable. Their
   current presence/privacy was verified previously; off-host retention and
   restore receipts remain operator evidence, not inferred from file existence.
3. Capture production migration-directory inventory and full applied-history
   timestamps with SELECT-only connections using default_transaction_read_only.
   Require completed original 0018/0019/0020 through 0044, no inconsistent
   dependencies and no pending plan. Preserve the nine additional history rows.
4. Request explicit approval naming: server eventtab@103.125.217.125; target
   /srv/eventtab/Final-EventTab-System-WEB/events/migrations/0018_squashed_0020_fresh_schema.py;
   final commit; archive hash; exact blob hash; eventtab:eventtab 0644; prior
   target absence; backup references and read-only before/after history evidence.
   Scope must say: install this file only; retain originals; no SQL, migrate,
   fake records, config/dependency edits, helper changes or service restart.
5. Only after that separate approval, install exact archive bytes atomically
   using a reviewed no-symlink-following procedure and exact target. Validate
   ancestors, target absence, ownership/mode and hash immediately before and
   after installation. If a target already exists, stop unless the exact existing
   bytes are explicitly reviewed; do not overwrite differing files. Keep audit
   evidence of prior absence and resulting bytes. This package provides a
   procedure, not an unreviewed executable production installer.
6. Without executing migrations, construct MigrationExecutor, call
   loader.check_consistent_history and inspect migration_plan(leaf_nodes).
   Require an empty plan and byte-identical before/after SELECT snapshots of
   every migration-history row/timestamp. Verify all original migration hashes.
   Never call executor.migrate(), check_replacements(), record_applied() or the
   migrate command on production. Do not fake or prune history.
7. Require the normal deploy helper's migration-file equality gate to pass
   unchanged against every archive migration. Its wording about separately
   applying migrations is generic: this completed-history file-only operation
   schedules no SQL and authorizes no migrate invocation. Do not edit the gate.

Installation must precede any real deployment attempt. The helper allocates a
release staging directory before detecting the missing migration; a blocked
attempt can consume that release ID. Do not delete gate evidence or reuse a
failed release directory; inspect it and package a new reviewed release ID.
The future code checkpoint is taken after file-only maintenance, so automatic
code rollback retains this migration file. Its removal requires separate review.

## Remaining Linux rehearsal and final release gates

Use an isolated Ubuntu 24.04 LTS/Python 3.12 replica, no live DB/services or credentials,
the same unprivileged account/layout, protected helper/config ownership, current
production package versions, systemd unit/drop-ins and Nginx routing behavior.
Restore only into isolation, disable external email/storage/jobs, and preserve
private data. Inspect complete hooks privately; a healthy service or matching
helper hash does not prove restart hooks are safe.

Rehearse a first release from no current manifest, actual source/static changes,
collectstatic, systemd stop/start behavior and Nginx health, explicit rollback
to baseline, second deployment, failed health/partial copy recovery, interrupted
manual recovery, lock contention, and corrupted checkpoint/archive/backup
rejection before service stop. Require unchanged config/media, package inventory
and complete migration-history timestamps. Keep archive/checkpoint receipts
and operator recovery instructions; never fabricate missing receipts.

Explicitly check static publication in that replica: the helper collects to
application/staticfiles, while operator-verified public Nginx assets/static
paths live separately under /var/www/eventtab and are root-owned. Do not assume
collectstatic updates those distinct public trees. Establish the existing
approved routing/synchronization behavior and test a changed static asset plus
rollback before accepting end-to-end deployment. Do not add a production copy
step, broaden sudo access or change Nginx configuration to bypass this question.

Run the complete application and deployment suites on the final committed
candidate with production dependencies; exercise the three PostgreSQL migration
cases plus a restored full 53-row production-history replica. Validate representative
PDFs visually and with the installed mobile client test dashboard JSON, today/
upcoming assignments, notification aliases, access-code auth and own-score
receipts. Exercise faculty ready/pending/published status, tabulator submissions,
publication authority and public visibility only with isolated synthetic data.

Before eventual deployment: permissions and local requirements alignment are
complete; approve the final commit and resolve migration-file installation gates; verify root
helper hashes, state/incoming/releases/log permissions, static collection access,
backup/retention evidence, disk, no pending journal, frozen full archive drift
inventory and clean reviewed commit. Re-run default full deploy.ps1 -DryRun and
production-PDF checks after the commit. Only a later explicit deployment approval
may authorize the ordinary DEPLOY flow. No security check may be bypassed.

### Rehearsal handoff sequence (outline only; not executed in this task)

1. After operator commit-content approval, stage only the final 26-path manifest,
   verify staged LF/blob fingerprints and unchanged 0019/0020, obtain commit
   authorization, and require a clean tree. Build/validate the archive through
   the existing packager; pin commit, archive and all 261 member hashes. Do not
   identify the current dirty tree as a committed release or bypass that gate.
2. Provision a disposable Ubuntu 24.04 VM with actual systemd and Nginx, Python
   3.12 and the approved dependency versions. Keep it isolated from live IPs,
   databases, mail, storage and jobs. Use synthetic fixtures or separately approved
   private restore material confined to the replica. No live credentials.
3. Reproduce eventtab UID/GID 1000, the application/state directory layout and
   observed 0644/0755/private modes. Install the reviewed helper bytes and a
   root-owned configuration containing the replica's own machine identity and
   approved requirements hash. Reproduce expected loopback DB identity/port in
   isolation, completed original history through 0044 and the nine preserved
   historical rows. Do not copy the live machine identity or manufacture a
   managed current.json for an unmanaged baseline.
4. Configure an eventtab-owned application service and isolated Nginx routing,
   including the helper's expected Host header. Reproduce separate real public
   /var/www/eventtab/assets and /var/www/eventtab/static trees, root-owned 0755,
   rather than silently substituting symlinks to application assets/staticfiles.
   Record and approve any static synchronization mechanism. Prove a changed
   static file reaches the served public URL and is restored by the rehearsed
   recovery procedure. A sync gap is a rehearsal failure, not permission to
   change production routing or sudo policy.
5. Exercise the additive migration file-only step on the replica, retaining all
   originals and comparing complete SELECT-only history/timestamps and empty
   graph plan before/after. Completed-history installation must require no SQL
   or alias-record write. Run full Python 3.12 application/PDF/mobile tests on
   isolated fixtures. Production file installation remains separately unauthorized.
6. Run the genuine helper first-deploy flow from no managed manifest, real HTTP
   health and static collection, then explicit rollback to the original source
   and public assets, then a second deployment. Exercise failed health/partial
   copy, lock contention, corruption rejection before stop and interrupted
   recovery. Check unchanged migration history, packages, secrets, uploads and
   excluded maintenance artifacts, and preserve the checkpoint/archive receipts.
7. Produce a signed-off rehearsal report tied to the exact committed archive.
   Only then review remaining production migration-file, drift, backup/retention
   and deployment approvals. None is granted by local candidate preparation.

## Verification evidence

Current local-only logs and machine evidence are under
.tmp/reconciliation/local-release-candidate/ and are excluded from Git/deployment.
Full application suite after applying the approved pin: **483 tests, 482 passed,
one existing Windows skip, zero failures/errors**, 290.947 seconds.
The existing overlay supplied ReportLab 5.0.1, Pillow 12.3.0 and charset-normalizer
3.5.2. The project venv remains ReportLab 4.5.1; no package install occurred.
Full deployment safety suite: **62 tests, 59 passed, three Linux-only skips,
zero failures/errors**, 10.586 seconds. Django system checks and
model/migration drift checks passed. The three real PostgreSQL migration-matrix
tests are included in the full application run; no migration/test was disabled.
The test database is a newly provisioned disposable local cluster, not production,
with temporary media/static, in-memory email and external storage disabled.

The prior network-disabled Linux safety run recorded 51 passes/no skips; its
helper source is unchanged. It was not rerun in this local requirements-only
step, and its service/history probes were mocked. Prior permission fixture and
Linux safety passes do not establish real Ubuntu/Python 3.12/systemd/Nginx readiness.
Earlier production probes were read-only and their evidence was reused locally;
**no Kamatera connection or production database operation occurred in this task**.

Original 0019/0020 remain identical to Git HEAD and saved production hashes.
The retained replacement has the reviewed e03be2e4...412810b LF hash. Current
requirements pass the unchanged helper hash gate against the saved root-approved
value. The actual committed-byte packager still rejects this dirty working tree;
no staging, commit, archive fabrication or gate bypass occurred. Git whitespace
and Python/PowerShell syntax checks pass. Exact candidate fingerprints and
requirements hash are recorded in commit-manifest.json and verification-summary.json.

**Approval decisions requested:** the local ReportLab pin and additive migration
retention are already approved. Review and approve the exact unchanged 26-path
commit scope and current fingerprints before future staging/commit; approve the exact migration
file-only installation only after prerequisites; accept Linux/PDF/mobile/backup
evidence. Permissions require no further remediation. Proceed to isolated Linux
rehearsal preparation/verification; a clean approved commit/archive remains a
prerequisite for real helper deployment/rollback rehearsal. Production deployment
authorization remains a later, separate decision.
