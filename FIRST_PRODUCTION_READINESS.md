# EventTab final readiness candidate

Baseline: `79f45446fde9db78d922c0085fef812b6aff5934`.
Review date: 2026-10-17, Asia/Manila.
This document describes an unstaged local candidate. No production writes,
staging, commits, deployment, permission changes, dependency installation,
migration execution or service restarts are authorized by this document.

## Gate status

| Gate | Status | Evidence / next boundary |
| --- | --- | --- |
| Required report dependency | PASS locally; pending production approval | `openpyxl==3.1.5` added; existing WSL installation and `et-xmlfile==2.0.0` validated. No package installation performed in this task. Production install and protected requirements-hash change require separate approval. |
| Bounded public publication and rollback | PASS component/safety checks; pending committed integration and production approval | Exact 335-file shadow publication/rollback, interrupted recovery, symlink/hardlink rejection, root ownership and drift refusal tested. No production installation. |
| Static directory permissions | PASS read-only manifest; pending production approval | Exactly 35 real `eventtab:eventtab 0775` directories, basic ACLs and file identities verified. Proposed `0755`; 329 regular `0644` files unchanged. No chmod. |
| Additive replacement migration | PASS procedure/static/read-only checks; pending file-only approval | Replacement still absent, originals unchanged and predecessors applied. Full history/schema fingerprints captured. Post-installation checks are mandatory future work, not claimed complete. |
| Backup proof | BLOCKED | Private DB dump validates; root-only source archive cannot be read through current SSH authentication. No off-host receipt found in the documented readable search scope. No backup sign-off. |
| New committed archive / genuine-helper rehearsal | Blocked pending clean committed candidate | No dirty-tree packaging, staging or commit. Existing baseline rehearsal remains historical evidence. |

**Production remains NO-GO.** Local implementation and evidence preparation are
complete; backup proof, a separately approved clean commit/rebuilt archive,
genuine new-companion integration rehearsal and each production write approval
remain required. Passing component tests does not authorize production changes.

## Actual local validation and changed paths

- Windows: 82 deployment/publication safety tests, zero failures/errors,
  20 Linux-only skips. WSL Linux root: all 82 tests pass with no skips.
- WSL Python 3.12.3 / PostgreSQL 18.6: all 483 application tests pass, zero
  failures/errors; Django system checks and model/migration drift checks pass.
  `deploy_checks.py core events accounts` provisions a NEW verified disposable
  loopback cluster, never the existing rehearsal DB or production DB.
- Initial full application attempt had three missing-SQL-fixture failures
  because the WSL source copy omitted existing test SQL. Copied the unchanged
  fixtures into the isolated test tree and reran the complete 483-test suite
  successfully. Both logs are retained; no application fix was invented.
- Exact 335-file root-only publication, source-version change and rollback ran
  against independent WSL shadow directories. All prior public hashes restored;
  two private checkpoints retained; pending journal cleared. The final guarded
  publisher rechecked all 335 files. Existing running rehearsal app, public
  trees and original helper were not modified or redeployed.
- The real packager correctly REFUSES the dirty candidate and creates no real
  archive. Full genuine-helper installation/recovery with the new publisher is
  intentionally stopped until a separately approved clean commit exists.
- No production writes, package installs, migration execution, chmod, service
  restart, production authorization change, staging or commit occurred.

Seven proposed Git paths (one existing modification plus six new files):
`requirements.txt`, `FIRST_PRODUCTION_READINESS.md`,
`execution/deploy_public.py`, `execution/test_deploy_public.py`,
`execution/publication-allowlist.json`, `execution/eventtab-public.service.conf`,
`execution/review_release_gates.py`.
All application runtime paths remain the same 261; ONLY `requirements.txt`
changes runtime content. The original historical migrations and original
deployment helper files remain unchanged.

New requirements LF SHA256:
`aaddfda321ff5b76b9dd92fe06aad6e3f4189f58806d826b4a949d46bc09dff7`.
Old protected production requirements SHA256 remains:
`f74239e40b816d1d71b1e67d28c5b7b419f0c6d1f00b2f187a20cf7f5e5ef56f`.
The current production gate MUST reject the new hash until separate approval.

Every working SHA256, LF SHA256 and filtered Git blob ID is recorded in
`.tmp/reconciliation/final-readiness-candidate/commit-proposal.json`.
The prospective 261-file manifest is alongside it. These are proposals, not
committed-byte archive receipts. Actual new commit/archive SHA values do not yet
exist and must not be substituted with the old baseline SHA.

## Consolidated evidence

Local evidence under `.tmp/reconciliation/final-readiness-candidate/`:

- `production-read-only-manifest.json`: exact 35-path owner/type/ACL/inode plan;
  canonical inventory SHA256
  `141c193e8a3f9fcf6ac14cedc25d6d57ac8c062379804b5d76c0f2703726f0f4`.
- `migration-procedure-evidence.json`: read-only connection enforced; PostgreSQL
  18.6, 78 total migration rows / 53 events rows, all original predecessors
  applied; replacement file and alias history still absent. Complete history
  SHA256 `e6fb51072d3eeafd7ac9ab8fad400cc828515d7c4071e09894a52966ce1c881a`;
  schema-definition SHA256
  `67e236a4b8e9f74c47080485c4ae4d8089cfc5bea2e3b4ee78faaeb1032f9608`.
- `source-backup-access.json`: existing key-only root SSH authentication denied;
  no permission changes or extraction attempted.
- `local-validation-results.json`: test counts, actual dependency metadata,
  component checks and log/source hashes.
- `commit-proposal.json`, `prospective-release-manifest.json` and
  `STAGING_COMMIT_PROPOSAL.md`: exact proposed paths/hashes and release boundary.

Actual WSL logs remain under `/srv/eventtab-rehearsal/final-rehearsal/`:
`gate-application-tests.log`, `gate-application-tests-fixture-missing.log`,
`gate-safety-tests.log`, `gate-publication-component.json`,
`gate-dependencies.json`. The first full-run fixture failure is preserved.
Test-only sources are separate under `state/gate-app-source`; root publisher
fixtures/shadow state are separate under `gate-candidate` / `gate-public-shadow`.

## Dependency decision

`openpyxl` is a runtime dependency, not only a test dependency. Top-level imports
in `events/admin_report_service.py` and `core/reports_generator.py` make report
pages and PDF paths depend on it; Excel exports also use it directly. The
reviewed production venv did not contain it. Pin `openpyxl==3.1.5` locally.
Its runtime dependency is `et-xmlfile` (tested version `2.0.0`); record exact
resolved inventory and Python compatibility with test results. Do not silently
upgrade any other dependency or modify the existing production venv.

Changing requirements invalidates the previously approved requirements SHA256.
After local validation, a new release commit/archive and explicit protected
production hash approval are required. No production dependency work is performed
as part of this candidate preparation.

## Public-file companion design and installation scope

The existing `deploy_server.py` and `deploy_release.py` stay unchanged.
The companion runs only as Linux root, with fixed paths and no user-supplied
source, destination, checkpoint ID or shell command. Its fixed configuration is
`/etc/eventtab-public.json`; its exact allowlist is installed separately at
`/usr/local/lib/eventtab-deploy/publication-allowlist.json`.

Configuration fields are exactly `machine_id` and `allowlist_sha256`. The
machine identity must be read on the target by the authorized installer; do not
copy a rehearsal identity. Pin the checksum of the final installed allowlist.
Keep config/code/allowlist root-owned and not group/world writable; recommended
config and files `0644`, helper directory `0755`. No application sudo entry for
the companion is needed or proposed.

`execution/publication-allowlist.json` freezes exactly 327 static paths and eight
design assets from the prior successful candidate rehearsal. Static destinations
are `/var/www/eventtab/static/<exact-name>`, sourced from application
`staticfiles`; asset destinations are `/var/www/eventtab/assets/<exact-name>`,
sourced from application `assets`. Uploads and other unmanaged public files are
never traversed for deletion or changed. Extending/removing allowlist entries
requires a separate review and recovery plan. Missing required source files
block publication rather than removing a live file silently.

The companion uses descriptor-relative, no-follow traversal; rejects symlinks,
hard-linked files, unsafe keys and non-regular files; enforces root-controlled
destinations, configuration and checkpoint ancestors; and caps total payload
size. Public files are installed root-owned `0644`, new public directories
root-owned `0755`. Source modes are never copied into public/checkpoint trees.

Root-only state is `/srv/eventtab-public` (`0700`). Each publication retains
private `0700` checkpoint directories / `0600` backup files, before/after hashes,
the exact allowlist hash and a protected previous-checkpoint reference. A lock
serializes operations. A pending journal blocks new publication. Recoverable
publication failures restore the complete prior allowed file set; unrecoverable
drift/corruption leaves the journal and blocks service startup. No recursive
public-tree deletion occurs; only a newly introduced exact allowlisted file
whose before-state was absent may be unlinked during recovery.

The separately reviewed root-owned service drop-in is
`execution/eventtab-public.service.conf`. It runs fixed `sync` before the
application starts. Original helper rollback restores application source and
collected static files; the next service start then republishes those restored
versions. The companion does not claim to restore application source or database
state itself. Explicit root-only `rollback` additionally requires application
source hashes to match the public checkpoint's before-version; `recover` handles
an interrupted public publication while service startup is blocked.

Publication is per-file atomic, not a globally atomic directory swap. Keep
deployment quiet/frozen and recognize that independent Nginx static requests can
observe successive file updates. Backups, state and all versions remain private.
Before the first real helper deployment, establish a known-good application
baseline whose complete allowlisted source and served-public hashes agree. If
the existing public trees are stale, initial publication is itself an explicitly
approved operation with a retained checkpoint, not a silently inferred baseline.
Subsequent publication rejects any public-file drift from its last checkpoint.
The genuine-helper rerun must prove initial baseline alignment and exact public
restoration; republishing application files alone is not proof of restoring a
previously divergent public tree.

Separate production approval must name exact final publisher/allowlist/drop-in
hashes, local root state creation, config creation and the intended service
configuration/reload boundary. Do not install anything or restart a production
service until that scope is approved. Do not broaden application public-write
permissions or add blanket sudo.

## Exact static permission procedure: preparation only

Run `python -B execution/review_release_gates.py --collect-production-read-only`
to collect metadata into `.tmp/reconciliation/final-readiness-candidate/`.
It does not have an apply operation. It requires exactly the reviewed 35 real
directories, `eventtab:eventtab` UID/GID 1000, mode `0775`, no extended/default
POSIX ACL and the exact basic ACL `user::rwx, group::rwx, other::r-x`.
It records ancestors, device/inode/ctime, owner, mode, type, `getfacl` and POSIX ACL
xattrs. Any changed inventory, owner, type, ACL or mode stops the proposal.

Proposed change only after separate approval: the exact listed 35 directories
become `0755` with basic ACL `user::rwx, group::r-x, other::r-x`; owners and
regular `0644` files remain unchanged. Revalidate every identity and ACL just
before the approved operation and verify afterward. No recursive blanket chmod,
ownership changes, public-tree changes or ACL bypass is proposed.

This is necessary because helper `snapshot_static()` uses `copytree` (preserving
directory modes), and checkpoint trust checks reject group/world-writable
backup directories. Do not weaken that validation instead.

## Replacement migration: separate file-only installation procedure

Target:
`/srv/eventtab/Final-EventTab-System-WEB/events/migrations/0018_squashed_0020_fresh_schema.py`.
Bytes: 1760; committed SHA256:
`e03be2e4c43576fc2efb7cc9640bb096ae3dfe4484e3c68cba94c8f7a412810b`.
Desired owner/mode: `eventtab:eventtab`, `0644`.

1. Obtain separate approval binding target host, new reviewed commit/archive,
   the unchanged file hash above, exact target, absence prerequisite, owner/mode,
   backup references and complete read-only history timestamps. Do not install
   from the uncommitted working tree.
2. Before installation, verify every target ancestor is a real directory with
   reviewed ownership/mode/ACL, target absent, and original 0018/0019/0020 plus
   all other migration hashes match the reviewed originals. Reject symlinks and
   raced file identities; do not overwrite an existing differing target.
3. Record SELECT-only full `django_migrations` including timestamps. Require
   original 0018/0019/0020 and through 0044 applied; retain all nine additional
   events-history rows. Confirm the graph has no pending plan using a read-only
   connection. No `migrate`, `--fake`, recorder writes or schema editor calls.
4. Under the approved file-only scope, create a same-directory exclusive
   no-follow temporary regular file, write only verified archive bytes, fsync,
   set approved owner/mode, and atomically install without replacing any existing
   target (for example exclusive hard-link installation followed by removal of
   the temporary name). Reject any existing target and revalidate ancestors.
   Do not execute/import the migration as an installation step.
5. Recheck exact installed hash/mode/owner and unchanged originals. Rebuild only
   the in-memory Django migration graph/plan with an enforced read-only DB
   connection; require no inconsistencies or pending operations. Compare all
   applied rows AND timestamps before/after and schema-definition fingerprint.
   Stop on any difference. Retain the local audit/backup receipt.

Writing a verified Python file does not execute its operations or invoke SQL.
The replacement declares original 0018/0019/0020 as `replaces`; all are already
applied on the reviewed server, so its `CreateModel` must not be executed there.
This is not a claim that a future `migrate` command cannot write alias history:
`migrate` is specifically prohibited. No automated production installer or
production SQL is part of this candidate. Any history/graph mismatch requires
new review, not fake records or schema changes.

## Backup proof boundaries

The read-only collector validates the readable DB archive with PostgreSQL 18
`pg_restore --file=-` to a discarded stream, never a database. It records file
identity, mode, checksum and parse exit status. This is not a restore test.
The root-owned `0600` source backup can only be checksum/archive-validated using
already-authorized privileged read access; do not chmod it or grant broad sudo.
If access is unavailable, report integrity as unverified.

Observed source backup: root-owned `0600`, 18,728,053 bytes, inside the private
`/home/eventtab/eventtab-baseline-backup` directory. Metadata/privacy PASS;
source checksum, gzip/archive integrity and restore proof remain UNVERIFIED.
No chmod or new privilege grant was attempted to bypass the access boundary.

The readable `eventtab_db.dump` is 276,707 bytes, `eventtab:eventtab 0600`;
full parse/read exit status is zero and SHA256 is
`4539fa9cd9a0706b48112d528677a53d01ee25d348de83b1a3f2d848e12f92a4`.
Existing review also confirmed backup/current score counts of 196 JudgeScore
and 136 normalized rows, with matching per-judge counts. This is evidence of
archive readability/coverage, not a claim of a fresh isolated restore.
The search of readable baseline-backup, eventtab-backups and deploy-log
directories returned zero checksum/restore/retention/off-host receipt candidates.
No cloud account/provider receipt was available; global nonexistence is not
claimed. Obtain privileged source checksum/gzip/tar validation and a provider
receipt binding backup checksums, retention/location and restore evidence before
sign-off. Do not perform a restore on production to obtain that proof.

Search known readable backup/log directories for checksum/restore/retention/
off-host receipt metadata, not production data contents. A local receipt name
does not itself prove remote retention or restore success; a missing result
does not prove no receipt exists elsewhere. Operator-supplied privileged source
checksum/archive proof and provider/retention/isolated restore receipts remain
required before backup sign-off. Do not create, copy or restore production
backups during this preparation.

## Proposal and rehearsal boundary

`execution/review_release_gates.py` also builds local working/LF SHA256 and Git
blob-ID proposals, checks an empty index and unchanged baseline HEAD, compares
all 261 prospective runtime paths and verifies originals 0019/0020 unchanged.
Only `requirements.txt` is expected to differ in the application archive; the
new publisher, tests, allowlist, service drop-in and this review are separately
reviewed tooling, not application runtime members.

No real release archive is built from dirty or uncommitted bytes. After tests,
approve the exact proposal, then separately authorize staging/commit, build a
clean committed-byte archive and rerun genuine-helper deployment/recovery with
the approved new root companion. Do not reuse the old archive while describing
it as the updated dependency/publication candidate.

Separate production write scopes remain: dependency/requirements-hash change;
root public companion/config/drop-in installation; exact 35-directory permission
plan; additive migration file-only installation; and finally live deployment.
None is executed by local readiness work or by this document.
