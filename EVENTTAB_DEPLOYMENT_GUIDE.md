# EventTab deployment workflow

This automation updates the existing Django installation at
`eventtab@103.125.217.125:/srv/eventtab/Final-EventTab-System-WEB`.
It does not provision Ubuntu, PostgreSQL, Nginx, a virtual environment, or Flutter.
**No Kamatera connection or change was made while implementing it.**

## Shortest path

After one-time activation, commit an ordinary update and run `./deploy.ps1`.
It runs native Windows safety/application tests against a disposable database,
packages committed bytes, verifies the server, uploads and invokes the existing
helper. No WSL session, manual SSH commands or repeated typed `DEPLOY` prompt.
Tests and server safeguards remain mandatory; nothing automatically commits,
installs packages or migrates production.

Before activation, `./deploy.ps1` performs local preparation ONLY; no SSH is
permitted. Use `./deploy.ps1 -PrepareOnly` to force local-only operation even
after activation. Preparation still requires reviewed, committed, clean source.
Use `./deploy.ps1 -DryRun` for uncommitted local inspection/checks.

### One-time activation (not performed)

1. Review/commit the workflow changes locally, then prepare its committed archive.
   The existing approved candidate is `bc00155821ac7bcb265a279a7ddbedffb70e1011`;
   these new workflow edits are NOT already covered by that commit.
2. Close missing source-backup integrity/off-host proof. Review the outstanding
   updated genuine-helper publication/recovery integration gap; unprivileged
   filesystem tests are not a replacement. Do not reuse the vulnerable root
   WSL orchestration path or assert it has been repaired.
3. Separately authorize the production maintenance scopes already documented in
   `FIRST_PRODUCTION_READINESS.md`: openpyxl/dependency and protected requirements
   hash maintenance; exact 35-directory permission correction; additive migration
   file-only installation (NO migrate); root-owned bounded publisher/allowlist/
   config/service hook and known-good public baseline alignment. Verify each.
4. Authorize the first live deployment and the ordinary code-only command scope.
   Only after completed setup/evidence review, create the local activation receipt
   below. Its baseline is the reviewed clean commit containing this workflow.

Local receipt: `.tmp/deploy/activation.json` (ignored; never commit secrets):

```json
{
  "format": 1,
  "activated_commit": "<reviewed 40-character commit containing the workflow>",
  "production_write_approval_reference": "<explicit operator authorization reference>",
  "backup_verification_reference": "<verified source/database backup and off-host evidence>",
  "publication_integration_reference": "<reviewed bounded publication and recovery evidence>"
}
```

This is an operator-maintained local interlock, NOT cryptographic proof that
maintenance happened and NOT a substitute for production authorization or the
server's independent checks. No receipt is generated or activated automatically.
Do not fill it with proposed/unverified work. It is deliberately absent today.

### Ordinary versus exceptional

- Ordinary: edits to existing Python views/services, templates, CSS/JS and the
  existing design assets, with unchanged dependencies/schema/settings/tooling
  and unchanged application archive membership. Mobile API edits still run the
  full regression suite; classification is not proof of API compatibility.
- Exceptional: requirements, models, migrations, settings, deployment tooling,
  added/removed runtime paths or public allowlist changes. These stop before SSH
  and require separate review/maintenance approval and renewed activation.
- No new activation receipt is needed for each ordinary commit. Classification
  compares against the activated baseline; unchanged file identities are retained.
- Explicit rollback keeps both interactive confirmations. Recovery/status use
  the existing remote checks and do not require clean local source, but still
  require an activation receipt. No forced unsafe rollback is introduced.

## Detailed safeguards

Before the first deployment, follow [the first-deployment review and checklist](FIRST_DEPLOYMENT_REVIEW.md).
The unmanaged baseline, migration-file approval and Linux rollback verification
are release gates. A passing local dry run and server status are insufficient
for first-release approval. The manual rollback validation-order defect is
fixed locally and regression-tested; separately approve/reconcile the corrected
installed helper and complete systemd/Nginx rehearsal before release.

The normal command, after the one-time setup and passing tests, is:

```powershell
.\deploy.ps1
```

The script shows the branch, commit, release ID, allowed files and SHA256. After
activation, invoking the normal command requests deployment without a second
typed confirmation. Only a clean, reviewed, committed working tree
can deploy. Untracked files also block deployment; `.tmp` is already Git-ignored.
Do not commit secrets to make this check pass.

## Commands on Windows

Run from `C:\Users\Admin\Desktop\eventtabs` in PowerShell 5.1 or later.

```powershell
.\deploy.ps1 -DryRun
.\deploy.ps1 -PrepareOnly
.\deploy.ps1 -Status
.\deploy.ps1
.\deploy.ps1 -Rollback
```

`-DryRun` runs local gates and prints the prospective manifest. It allows a dirty
tree for inspection, writes a local transcript under `.tmp/deploy/logs`, and
does not connect, upload, package a deployable release, or change the server.
`-Status` connects and checks the installed helper, fixed host identity, service,
disk space and HTTP health; it writes a private server audit entry and lock.
`-Rollback` displays status, asks for `ROLLBACK`, then opens an interactive SSH
session and asks for the exact release ID being undone. There is no force flag.

The existing `venv\Scripts\python.exe`, Git, Windows OpenSSH `ssh` and `scp` must
be installed. The SSH key must already work through your agent or default SSH
configuration. Batch authentication is required; the script never asks for or
stores a password, passphrase or private key.

If Windows blocks this unsigned local script, review it first. Subject to your
organization's execution policy, this changes policy only in the current shell:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
```

## Local test isolation

`execution/deploy_checks.py` uses `execution/local_test_database.py` to create a
fresh PostgreSQL cluster under `.tmp/deploy/test-databases/pg-tests-*` for each
run. It starts an owned child process bound only to `127.0.0.1` on an unused
port, explicitly refusing ports 5432 and 5433. It verifies the process PID,
ready state, data-directory path and port before any SQL connection, then
confirms `SHOW data_directory` matches its newly created directory.

Django uses only that cluster's `test_eventtab_isolated` database and generated
`eventtab_tests` role. It never falls back to an existing server, local `.env`
database credentials, SSH tunnel or Kamatera. Every database alias is replaced
before Django setup. Media and collected static destinations use temporary
directories, Cloudinary is disabled, and email uses an in-memory backend.

The runner executes Django `check`, `makemigrations --check --dry-run`, and the
full `core`, `events` and `accounts` suite with actual migrations enabled.
PostgreSQL-specific identity, SQL and constraint tests run against real
PostgreSQL. No tests or migration operations are disabled to make the gate pass.
The cluster is stopped on exit, including failed tests. Its ignored data/logs
remain for diagnosis; no Windows service is installed or changed.

Local PostgreSQL binaries are required. Windows automatically checks the
existing `C:\Program Files\PostgreSQL\18\bin`; Linux checks
`/usr/lib/postgresql/18/bin`, then PATH. For another existing installation, set
this variable in the current PowerShell session (adjust the path):

```powershell
$env:EVENTTAB_TEST_POSTGRES_BIN = 'C:\Program Files\PostgreSQL\18\bin'
```

Nothing grants permissions to an existing database or changes its configuration.
The temporary role's privileges exist only in the disposable cluster; trust
authentication is limited to that role on loopback. Never expose this test
cluster to the network. Do not run the Linux test launcher as root; PostgreSQL
requires a regular local user.

Run all application checks directly, without SSH or deployment:

```powershell
.\venv\Scripts\python.exe execution\deploy_checks.py
```

The default deployment/dry-run also runs this full suite. A reviewed limited
change can use `-Tests` to select relevant modules, but never narrow coverage
merely to hide a failure. This does not skip deployment safety tests, Django
checks, drift checks or server gates. Schema maintenance still needs separate
review and backup; a passing disposable-database test does not authorize
production migrations.

## One-time setup: verify the server and existing layout

These instructions are for an operator to run later. They were not executed as
part of implementation. Keep a verified database backup and console access
before the first real deployment. Schedule a quiet period: the service is
briefly stopped while source files change; this is not a zero-downtime deploy.

First compare the server's SSH fingerprint through the trusted Kamatera console.
On Ubuntu through that console:

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

On Windows, connect once, accepting the host key only if it matches that trusted
fingerprint. Never use `StrictHostKeyChecking=no` or blindly trust `ssh-keyscan`.

```powershell
ssh -o StrictHostKeyChecking=ask eventtab@103.125.217.125 "true"
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes eventtab@103.125.217.125 "id -un"
```

The second command must print `eventtab` without prompting for a password.

Ubuntu, logged in as `eventtab`, inspect the existing installation:

```bash
id -un
readlink -f /srv/eventtab/Final-EventTab-System-WEB
test -f /srv/eventtab/Final-EventTab-System-WEB/.env
test -x /srv/eventtab/Final-EventTab-System-WEB/venv/bin/python
systemctl show eventtab --property=User --property=WorkingDirectory --property=FragmentPath
systemctl is-active eventtab
curl --fail --silent --show-error --max-time 5 -H 'Host: 103.125.217.125' http://127.0.0.1/api/health/
```

The service must already use `User=eventtab` and
`WorkingDirectory=/srv/eventtab/Final-EventTab-System-WEB`. The helper refuses
a root-running service, a different working directory, symlink project/state
directories, or a redirected/non-JSON health response. Review any discrepancy
before setup; these scripts do not rewrite the service or Nginx configuration.

The existing `.env` must supply the production settings used by the service and
the helper: PostgreSQL on `localhost`/`127.0.0.1`, port `5433`, database
`eventtab_db`. If the service relies on additional EnvironmentFile settings,
reconcile that configuration separately before enabling this workflow. The
database identity probe also verifies the actual connected database/port. Keep
PostgreSQL listening on loopback; do not open a firewall port for deployment.

The `eventtab` account must already be able to update application source and
generated `staticfiles`, and read `.env` and the existing venv. Review ownership
of specific failing paths if necessary. Do not recursively chown/chmod the
project, `/srv`, `/etc`, media, or any production retirement seal. Existing
root-owned maintenance/seal infrastructure remains outside this workflow.

## One-time setup: install the reviewed helpers

Review these two files locally before installation:

- `execution/deploy_server.py`: fixed-path unprivileged deployment helper.
- `execution/deploy_release.py`: shared archive allowlist/hash validator.

Windows PowerShell:

```powershell
Get-FileHash .\execution\deploy_server.py -Algorithm SHA256
Get-FileHash .\execution\deploy_release.py -Algorithm SHA256
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes eventtab@103.125.217.125 "umask 077; mkdir -p /home/eventtab/eventtab-deploy-setup"
scp -o BatchMode=yes -o StrictHostKeyChecking=yes .\execution\deploy_server.py .\execution\deploy_release.py eventtab@103.125.217.125:/home/eventtab/eventtab-deploy-setup/
```

Ubuntu, as `eventtab`, compare these hashes to Windows before installing:

```bash
sha256sum /home/eventtab/eventtab-deploy-setup/deploy_server.py /home/eventtab/eventtab-deploy-setup/deploy_release.py
sudo install -d -o root -g root -m 0755 /usr/local/lib/eventtab-deploy
sudo install -o root -g root -m 0644 /home/eventtab/eventtab-deploy-setup/deploy_server.py /usr/local/lib/eventtab-deploy/deploy_server.py
sudo install -o root -g root -m 0644 /home/eventtab/eventtab-deploy-setup/deploy_release.py /usr/local/lib/eventtab-deploy/deploy_release.py
sudo install -d -o eventtab -g eventtab -m 0700 /srv/eventtab-deploy
install -d -m 0700 /srv/eventtab-deploy/incoming /srv/eventtab-deploy/releases /srv/eventtab-deploy/logs
```

`sudo install` only installs reviewed files. Never run these project helpers,
`manage.py`, pip, migration code or application commands as root. The helper
explicitly refuses root execution.

Create root-owned host/configuration pins using this fixed inline setup snippet
on Ubuntu. It reads only the machine ID and requirements hash, not `.env`:

```bash
sudo /usr/bin/python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
target = Path('/etc/eventtab-deploy.json')
if target.exists() or target.is_symlink():
    raise SystemExit('Configuration already exists. Review it; do not overwrite blindly.')
requirements = Path('/srv/eventtab/Final-EventTab-System-WEB/requirements.txt')
data = {
    'machine_id': Path('/etc/machine-id').read_text().strip(),
    'approved_requirements_sha256': hashlib.sha256(requirements.read_bytes()).hexdigest(),
}
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
with os.fdopen(fd, 'w') as stream:
    json.dump(data, stream, indent=2)
PY
```

Use `sudo visudo -f /etc/sudoers.d/eventtab-deploy` and add **only**:

```sudoers
eventtab ALL=(root) NOPASSWD: /usr/bin/systemctl stop eventtab, /usr/bin/systemctl restart eventtab
```

Validate the syntax on Ubuntu:

```bash
sudo chmod 0440 /etc/sudoers.d/eventtab-deploy
sudo visudo -cf /etc/sudoers.d/eventtab-deploy
sudo -n -l
```

Review existing broad sudo grants separately; this rule does not revoke them.
Do not grant passwordless Python, Bash, pip, arbitrary `systemctl`, `chown`, or
project-provided commands. The existing systemd unit is the restricted service:
only its exact stop/restart commands run as root. All release validation,
application checks, static collection and file operations run as `eventtab`.
No new root-running deployment service or Nginx changes are needed.

Test the installed helper without deploying, on Ubuntu:

```bash
python3 /usr/local/lib/eventtab-deploy/deploy_server.py status
```

Then Windows:

```powershell
.\deploy.ps1 -Status
.\deploy.ps1 -DryRun
```

`-Status` must show health `ok`. Resolve any local test failures before a normal
deployment. Installation does not automatically upload a release or restart.

## Everyday workflow

1. Edit and test locally. Review the diff and intended test coverage.
2. Commit approved application changes and the deployment tooling. The existing
   faculty fix remains a separate working-tree change until you review/commit it.
3. Run `.\deploy.ps1 -DryRun` and resolve failures.
4. Run `.\deploy.ps1`, review the summary, and type `DEPLOY`.
5. Verify the website, Judge app API/login, and faculty publication flow manually.

The workflow uses SCP with your existing key and strict host-key checking. It
compares SHA256 on Windows and Ubuntu and validates every manifest member/hash
before extraction. Archives cannot contain links, duplicate members, absolute
paths, traversal, secrets, uploads or disallowed file types. Expanded size and
file counts are limited. Only tracked allowlisted application files are shipped.
Deployable archives use exact committed Git bytes, avoiding Windows CRLF
conversion changing requirements/migration hashes on Ubuntu. Dirty dry runs
inspect working-tree files and cannot produce a deployable archive.
Only eight explicitly named design images under `assets` are eligible; team,
candidate, report and other uploaded files are excluded. Vendor license files
are retained. Maintenance/reset tools and test evidence are excluded.

The server checks root-owned helper/config integrity, machine identity, existing
deployment layout, service identity, disk space, dependencies, Django checks,
model/migration drift, pending migrations, and baseline health. It refuses
unmanaged edits to previously deployed source. If a previously managed source
file disappears from the new package, deployment blocks for separate removal
review rather than silently deleting it.

A nonblocking Linux lock prevents simultaneous operations. A private staging
directory contains the validated source; `.env` is referenced only by a server
symlink, never transferred. The existing venv is used throughout. A checkpoint
backs up each changed application file and the collected static tree when needed.
The service stops only after all preflight gates pass. Source replacements use
atomic per-file renames while the service is stopped; `collectstatic --noinput`
runs only for frontend/design-asset/settings changes. Then the service restarts
and both systemd and the existing database-backed `/api/health/` must succeed.
This health endpoint does not prove every UI, scoring rule or mobile flow works.

Windows transcripts: `.tmp/deploy/logs/`. Release archives/metadata:
`.tmp/deploy/`. Server logs: `/srv/eventtab-deploy/logs/*.jsonl` (mode 0600).
Logs include release, timestamp, changed files, command/check outcomes, restart
and rollback outcomes. Server staging/checkpoints/logs are private to `eventtab`.
Diagnostics may contain sensitive operational information; do not publish logs.
Nothing automatically prunes old releases, checkpoints or logs; monitor space.
Only delete old artifacts after an explicit retention/rollback review.

## Dependency changes: separate reviewed maintenance

Normal deployment does **not** run pip. A requirements hash different from the
root-approved hash blocks with `Dependency review required`. This is intentional:
mutating the shared venv during a code rollout would make automatic rollback
unreliable. The existing requirements include version ranges, so hash approval
alone is not a reproducible lock of every transitive package.

For a reviewed dependency change, an operator must separately:

1. Review the requirements diff, package origins and dependency resolution on
   matching Python/Ubuntu. Do not approve arbitrary remote requirement URLs.
2. Schedule maintenance and stop `eventtab`; take a verified private copy of the
   **existing** venv and record installed packages with `venv/bin/python -m pip
   freeze`. Preserve its original path so a restore has correct embedded paths.
3. Install the reviewed requirements with the existing venv as `eventtab`, never
   root: `venv/bin/python -m pip install -r /absolute/path/to/reviewed-requirements.txt`.
   Run `venv/bin/python -m pip check` and the relevant checks. If it fails, keep
   the service stopped and restore the backed-up venv through a reviewed recovery
   procedure; do not blindly restart a partially updated environment.
4. Copy the approved requirements text into the existing application's
   `requirements.txt`. Do not alter `.env`, media or the database.
5. After explicit dependency approval, update only
   `approved_requirements_sha256` in `/etc/eventtab-deploy.json` using a trusted
   administrator tool. The exact hash is printed by the following read-only
   Ubuntu command. Keep the configuration root-owned, mode 0644, and preserve its
   `machine_id`:

```bash
sha256sum /srv/eventtab/Final-EventTab-System-WEB/requirements.txt
sudoedit /etc/eventtab-deploy.json
```

6. Restart the existing service and verify health manually. Future code releases
   with these identical approved requirements can use `.\deploy.ps1` again.

Dependency installation/approval is a separate operator procedure, not a hidden
flag that bypasses normal deployment checks. Existing rollback checkpoints from
before that maintenance may no longer be safe; the helper blocks them.

## Models and migrations: separate review, backup and confirmation

The helper never runs `migrate` and never reverses migrations. It blocks new or
changed migration files, pending migrations and model drift with
`Deployment blocked: migration review required`. This includes data migrations.

Review the exact migration files and SQL, locking/downtime effects, dependencies,
data loss risks and backward compatibility against a separate local PostgreSQL
test database first. Take a PostgreSQL 18-compatible production backup, store it
privately, and verify a restore in an isolated database before authorizing a
production schema change. Use your existing approved backup process and database
role; do not place credentials in command arguments or this repository.

Then obtain explicit operator confirmation for a separate maintenance runbook
covering installation of the reviewed migration files (and any required code),
application with the existing venv as `eventtab`, checks and recovery. This
workflow deliberately provides no one-command schema bypass. After separately
reviewed maintenance has installed the matching migration files and applied
any required schema changes, ordinary code deployment is allowed again. The
reviewed replacement for an original history completed through `events.0044`
needs only explicit file-installation approval and read-only compatibility
checks, as detailed in the first-deployment checklist: do not run `migrate`
merely to register its alias. Changing applied migration
history invalidates older code rollback checkpoints. Automatic rollback is not
a substitute for database backup or a schema recovery plan.

## Rollback, interruption and failures

Failed code deployment or health checks trigger automatic restoration when the
checkpoint hashes, requirements and database migration history make it safe.
New files created by that deployment are removed; previous file contents and
collected static files are restored. Uploads, `.env`, venv, logs, Nginx and SQL
data are never restored or deleted by rollback. The old service restarts and
must pass health checks. A nonzero exit still reports the failed deployment.

Manual rollback undoes the latest managed successful deployment or recovers a
pending interrupted one. Repeated commands can step backward through available
checkpoints. The first deployment's checkpoint preserves changed managed source
contents and affected collected static files from the unmanaged baseline. It
is not a full baseline/database/configuration backup and follows any separately
approved migration-file installation. Obtain and restore-test an independent
baseline backup first. There is no arbitrary path/release selection or force-overwrite flag.

The revised helper performs complete read-only rollback preflight **before**
writing the recovery journal or stopping the service. Format-2 checkpoints are
bound to a SHA256 reference in the current/pending state. Validation includes
release identity, exact release/archive manifest, previous deployment receipt,
complete baseline/backup inventories, source/static/settings hashes, safe paths
and Linux artifact permissions, writable targets, requirements, installed
packages and migration history. Invalid preflight leaves application files,
service and journals unchanged. It cannot make an already unhealthy service
healthy; it preserves the running application's state on rejection.

Source/static inputs are validated again after service stop and before restoring
any files. If an external edit occurs after preflight, recovery blocks and keeps
the journal rather than overwriting it. Continue to freeze concurrent maintenance.
Legacy checkpoints without the new integrity reference are rejected for operator
review. Never generate an approval hash merely to accept an old checkpoint.
Keep incoming release archives, checkpoint files, baseline settings and backups
together throughout the rollback retention period, including the previous
managed release's archive/checkpoint. Missing artifacts now fail preflight.
Hashes detect corruption within the existing deployment-account trust boundary;
they do not protect against compromise of that account. No signing credentials,
new root privileges, automatic SQL or force/bypass flags were introduced.
Unexpected edits to affected server files, corrupt backups, changed dependencies
or schema history block restoration for operator review. The installed package
name/version inventory is also recorded and must match before rollback.

An interrupted operation leaves `/srv/eventtab-deploy/pending.json`. New deploys
block while it exists. Run `-Rollback` to recover interactively. If restoration
or its health check fails, the journal remains and the service may be stopped
or unhealthy. Preserve all evidence, inspect the private JSONL audit log via SSH,
and use console access if needed. Do not remove the journal just to bypass a
failure. The migration-history guard detects Django-recorded schema changes;
it cannot detect arbitrary manual SQL. Do not run concurrent schema/dependency
maintenance while a deployment or rollback is active.

If upload/hash verification fails, application source is unchanged. If staging
validation fails, source is unchanged and the stage/audit remain for inspection.
Package a new timestamped release to retry after resolving the cause. Reapplying
an already successful identical release is a health-checked no-op.

## Investigation of the original failing application run

The original SQLite run had 5 failures and 66 errors. These were not 71
independent application defects:

| Original errors | Cause | Correction |
| --- | --- | --- |
| 33 | PostgreSQL sequence functions used in fixture setup do not exist in SQLite | Use a disposable real PostgreSQL cluster |
| 14 | Fixture setup reads PostgreSQL sequence relations absent from SQLite | Use a disposable real PostgreSQL cluster |
| 1 | Live database identity test correctly requires PostgreSQL | Run against the new local test cluster |
| 17 | A mocked email thread was executed inline; its real `close_old_connections()` closed the TestCase's transaction and broke later tests | Mock thread connection cleanup during the synchronous worker test and assert both cleanup calls |
| 1 | Pageant fixture submitted a staff Faculty account in the legacy Tabulator assignment field | Supply a genuine non-staff Tabulator fixture; keep authorization validation intact |

The five original failed assertions were stale: assigned sports faculty can
publish with explicit confirmation, match completion alone does not satisfy
department/placement publication checks, the criteria wizard requires its
current category/date/classification fields, the results page displays a
publication-status notice instead of the retired verification heading, and a
hidden legacy bracket dialog is not a visible create action. Tests now check
those existing contracts and persisted state rather than changing application
permissions or scoring to match obsolete expectations. The bracket listing's
empty-state link is also not expected when events already exist.

Once the closed-connection cascade was removed, additional stale assertions
became visible: event-specific entry IDs were confused with registry-person IDs,
and an empty-participant rejection expected obsolete wording. Updated tests
verify entry-to-person mappings, qualifier entry IDs, the actual rejection, and
that rejected creation saves no event.

Real migrations exposed two additional application defects that the old
SQLite/model-only setup could not detect. Migration `0019` unconditionally
renamed a legacy `abbreviation` column although fresh `0018` already creates
`code`. Migration `0020` assumed the legacy `events_match` table always exists.
The initial fix made those repairs conditional. A follow-up compatibility
review found that the conditional reverse could remove data even when forward
was a no-op, and that constraint-name checks did not prove table ownership.

The finalized solution restores original `0019` and `0020` byte-for-byte and
adds `0018_squashed_0020_fresh_schema.py`. Fresh databases use the canonical Team
creation; completed histories through `0044` have an empty migration plan with
no history writes during the deployment checks. Three real PostgreSQL regression
tests cover fresh forward/reverse/replay, original legacy behavior, and read-only
compatibility with the completed original history.

See [MIGRATION_COMPATIBILITY_REVIEW.md](MIGRATION_COMPATIBILITY_REVIEW.md) for the
exact SQL effects, data-loss findings, constraint ownership evidence, partial-
history limits and deployment decision. The new replacement file still needs
separate operator review under the deployment helper's migration-file gate.
Do not run production `migrate` just to register the replacement: Django can
write an alias record even when there is no schema work. Automated deployment
continues to run no migrations or migration reversals.

## Verification performed during implementation

- Original deployment unit/integration simulations: 39 tests, 38 passed and one Linux
  `flock` integration test skipped on Windows. Tests cover archive exclusions,
  hashes, extraction, traversal, links, duplicates, size limits, real Git clean
  builds/dry runs, service failure auditing, partial-copy/health rollback,
  checkpoint corruption, source removal/drift, pending recovery, confirmation,
  and refusal of foreign/exited/mismatched local PostgreSQL processes.
- PowerShell 5.1 syntax parsing passed. An actual PowerShell dry run with the
  legacy migration module passed 3 PostgreSQL application tests and deployment safety tests,
  and completed without SSH/SCP calls or server changes.
- Offline SSH/SCP stubs also verified dry-run isolation, dirty-tree refusal before
  transfer, and interactive rollback dispatch even when status reports unhealthy.
- Focused faculty/publication selection: 113 application tests passed; Django
  checks and model/migration drift check passed.
- Full application suite after investigation: **470 tests, 469 passed, no
  failures/errors, one existing Windows skip** (`O_NOFOLLOW` is unavailable).
  Actual PostgreSQL 18.3 migrations, Django checks and migration drift checks
  passed. Latest compatibility-review evidence: `.tmp/deploy/migration-review/full-suite.log`. The isolated cluster
  was stopped after completion.
- Linux runtime ownership, systemd/sudo integration, production database identity
  checks, Nginx health and actual production rollback were **not** verified here.
  Subsequent local Linux HTTP-process tests are recorded below, but do not
  cover production-equivalent systemd/Nginx integration. Validate those in an
  isolated Ubuntu staging environment before trusting production use.

To rerun the offline deployment tests on Windows:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s execution -p "test_deploy*.py"
```

Latest rollback-fix results: **62 tests on Windows: 59 passed, three Linux-only
skips**. A network-disabled disposable Linux container, running unprivileged
with read-only source/root and a private 1 GiB tmpfs, passed **51 server-flow and
database-isolation safety tests**, including actual flock, artifact permissions,
and loopback HTTP-process survival on corrupt-backup rejection, successful
rollback and simulated failed-deployment recovery. Systemd, dependency and
migration probes remain mocked. The cached image lacks Git, so the full Git
packaging tests ran on Windows; production Linux systemd/Nginx integration
remains a release blocker. Evidence is under `.tmp/deploy/rollback-safety/`.

To run the same deployment tests in an isolated Linux checkout (no production
secrets, network target or real service required):

```bash
python3 -m unittest discover -s execution -p 'test_deploy*.py'
```

Passing local tests or successfully deploying does not establish production
readiness. Review security settings, backup restoration, monitoring and real
application behavior separately. Flutter-only UI changes under
`C:\Users\Admin\Desktop\eventtab` use a separate APK build/distribution process;
they do not require Django deployment unless backend API code also changed.
