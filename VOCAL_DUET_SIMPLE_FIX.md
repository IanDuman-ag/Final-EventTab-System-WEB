# Vocal Duet: three-stage development repair

This release has **not** been deployed. It changes no Flutter code, migrations,
authentication models, official weights, roster, event status or Faculty round
controls. Use a short maintenance window: keep judges and configuration editors
out until verification finishes.

The command is fixed to Faculty/Admin **69**, JudgingEvent **64** / Event **79**,
round **s9bu97fk**, scores **175–190**, criteria **93→1197, 96→1198, 97→1199,
98→1200**, and pairs **14→372 [1,9] BFPT**, **15→373 [4,10] BTLEd**.
Names alone never establish pair identity. Check these IDs, members and departments
against the actual roster before confirming.

## Approval and trust

The original production command and its two-person orphan-owner approval remain
unchanged. Existing valid signed host/permit/review evidence is reused first.
This **separate development policy** also permits the actual server owner to
approve one verified checkpoint through a fixed root-owned helper. This is an
explicit infrastructure-owner attestation, not a second Faculty identity or an
off-server signature. It requires real ownership/development-use evidence and an
interactive sudo session; DEBUG, environment variables and database names cannot
authorize it. If exclusive development use cannot actually be established, stop
and use the original independent-review workflow.

The helper never imports Django, connects to PostgreSQL or holds a private key.
It reads the real root-only DMI UUID without changing its permissions. Root-owned
short-lived files bind host UUID, machine/boot IDs, actual PostgreSQL cluster ID,
database/table OIDs, role, address/port, checkpoint and backup hashes. Untrusted
paths, changed evidence, expired/reused approval and a production retirement seal
block the operation. Do not grant the web process sudo access to this helper.

## A. Windows VS Code PowerShell — package and upload once

Files are in **C:\Users\Admin\Desktop\eventtabs**, not Flutter's `backend/`.
Run only after reviewing the changes and local test results. The SCP/SSH commands
below are operator actions; the coding agent has not run them.

```powershell
Set-Location C:\Users\Admin\Desktop\eventtabs
$bundle = Join-Path $env:TEMP 'eventtab-vocal-duet-simple.tgz'
tar -czf $bundle events/vocal_duet_development.py events/vocal_duet_simple.py events/management/commands/vocal_duet_simple.py events/test_vocal_duet_simple.py execution/vocal_duet_simple_owner.py VOCAL_DUET_SIMPLE_FIX.md
Get-FileHash -Algorithm SHA256 $bundle
$sshUser = Read-Host 'Your legitimate sudo-capable SSH account'
scp $bundle "${sshUser}@103.125.217.125:/tmp/eventtab-vocal-duet-simple.tgz"
ssh -t "${sshUser}@103.125.217.125"
```

No APK rebuild is needed. The existing reset, development, host-identity and
scoring modules must already be deployed. This is an incremental backend release.

## B. Kamatera Ubuntu SSH — install once, then three stages

Install from a **root-private copy**, not by executing a helper from the web
process's writable source directory. In this block, **stop after `sha256sum` and
compare it with the Windows SHA256 before executing the remaining lines**.
Use a fresh release directory if this one already exists; preserve previous code
through your normal release/version backup. Do not replace original evidence.

```bash
sudo -i
umask 077
mkdir /root/eventtab-vocal-duet-simple-release
cp /tmp/eventtab-vocal-duet-simple.tgz /root/eventtab-vocal-duet-simple-release/release.tgz
cd /root/eventtab-vocal-duet-simple-release
sha256sum release.tgz
# STOP and compare with the Windows SHA256; continue only on an exact match.
tar -xzf release.tgz --no-same-owner
install -d -o root -g root -m 0755 /usr/local/libexec /etc/eventtab /etc/eventtab/development-reset
install -o root -g root -m 0755 execution/vocal_duet_simple_owner.py /usr/local/libexec/eventtab-vocal-duet-owner.py
for file in events/vocal_duet_development.py events/vocal_duet_simple.py events/management/commands/vocal_duet_simple.py events/test_vocal_duet_simple.py execution/vocal_duet_simple_owner.py VOCAL_DUET_SIMPLE_FIX.md; do
  install -o root -g root -m 0644 "$file" "/srv/eventtab/Final-EventTab-System-WEB/$file"
done
exit
```

Before starting, the owner must independently verify current development-only
use using actual server ownership and school-use records. Faculty must have the
actual pair roster and test-run evidence available. Do not invent references.

### Stage 1 — read-only preflight and preview

```bash
sudo /usr/local/libexec/eventtab-vocal-duet-owner.py observe
sudo -iu eventtab
cd /srv/eventtab/Final-EventTab-System-WEB
source venv/bin/activate
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py showmigrations --plan
python manage.py vocal_duet_simple preview
```

`observe` writes only a trusted host observation; never run Django as root.
The preview uses a read-only database transaction and creates a private session
inside `/home/eventtab/vocal-duet-development.mufqRL`. It prints the exact test
rows, owners/timestamps/locks, dependencies, full criterion changes including
detailed children, verified pair membership, official round and target identity.
Review `blocked_reasons`. No scores are deleted. Database/schema check failures
must be resolved separately; **do not run migrations merely to silence errors**.

### Stage 2 — verify existing backup, roster and explicit approval

Still in the same `eventtab` shell:

```bash
python manage.py vocal_duet_simple verify
exit
# Run only if verify reports that existing signed authorization is unavailable:
sudo /usr/local/libexec/eventtab-vocal-duet-owner.py approve
```

The command preserves `review.json` (or the existing literal `review\.json`),
`database.dump` and **eventtab_vd_dev_restore**. It hashes the full custom-format
archive and compares complete relevant live/restored rows, not just counts:
scores including extra database columns, mobile/official criteria and children,
categories, pair entries/members/departments, submissions and assignments.
The isolated connection must resolve to a different database OID. Differences
block; do not edit the old dump/restore to make the check pass. Obtain a separately
reviewed fresh full backup and isolated restore if it is stale.

When prompted, provide a real roster/test-run evidence reference if missing.
After backup comparison, inspect the displayed mappings and type exactly:
`RESET ONLY VOCAL DUET TEST SCORES 175-190`.
The source review is not edited; the complete confirmed record is session-local.

If needed, the actual owner then runs `approve`, independently checks displayed
host/cluster identity and exclusive development use, verifies that this exact
archive really produced the isolated restore, supplies the actual verification
reference and types the same phrase. A row comparison does not alone prove a
dump's provenance: this restore provenance attestation must be truthful.
Approval is bound to the complete checkpoint, lasts at most two hours and cannot
authorize a different scope. No JSON editing, signing keys, fake accounts or
permit-copying steps are needed.

### Stage 3 — atomic repair and final verification

```bash
sudo -iu eventtab
cd /srv/eventtab/Final-EventTab-System-WEB
source venv/bin/activate
python manage.py vocal_duet_simple apply
python manage.py inspect_judge_portal --judge-id 57
python manage.py check
exit
```

Apply locks/rechecks scope, ownership, dependencies, authorization, publication,
round, entire backup/restore comparison and frozen protected state. It saves an
exclusive private preimage, deletes only IDs175–190, retains criterion/candidate
IDs, copies approved official criteria/subcriteria and canonical pair snapshots,
saves explicit mappings and records a mandatory audit in one transaction.
Detailed failure rolls everything back; changed or new rows invalidate approval.
Other scores, events, rosters, assignments and official settings must be unchanged.
Retries after a committed audit report `already_applied` without touching new
scores. Existing scores are never reassigned to fabricated accounts.

Success must report `mobile_configuration_ready: true` and
`protected_data_unchanged: true`. Judge57's inspection should show Vocal Duet's
configuration valid; `can_score` still depends on that judge's assignment and
Faculty-controlled open round. Compare Pageants and Modern Dance with stage1.
In the APK, refresh, score one eligible pair with detailed criteria, submit,
confirm the locked receipt and History, and retry sync to verify no duplicates.
If a Faculty lock or genuine roster/configuration issue remains, resolve that
through the authorized Web Admin workflow. Nothing force-enables scoring.

## Recovery and later official school use

Before commit, any failure rolls back automatically. Keep the original archive,
restore database, original review, session `approval-request.json`, private
`preimage-*.json`, commit intent/completion report, root approval and database
audit. Files left by a rolled-back attempt are evidence, not proof of commit;
the database audit ID establishes success. A stale checkpoint requires a fresh
stage1; never alter a frozen checkpoint to bypass a mismatch.

After commit, do **not** restore the full archive over an active database. A DBA
should restore it to a **new isolated recovery database**, compare it with the
private preimage and audit hashes, and prepare an event-scoped compensating
transaction for review. That transaction may restore only the exact removed16
rows and prior mobile definitions/children/candidate snapshots/mapping keys,
after proving no new submissions, collisions or other dependencies exist. Retain
the audit and all new data; missing-owner FK constraints require separate review,
never account recreation. A full database rollback requires a separate approved
maintenance decision because it would also roll back unrelated work.

**Before any official school records are entered**, retire both development
authorization paths using the actual owner account:

```bash
sudo /usr/local/libexec/eventtab-vocal-duet-owner.py retire
sudo -u eventtab test -r /etc/eventtab/official-production.json
```

Retirement creates a root-controlled seal checked by both development workflows;
even unexpired old permits/root approvals cannot authorize a reset. There is no
unretire flag. Preserve the seal in future releases, remove the development
helper/commands and credentials through a reviewed production release, retain
audit/backups, and establish real production independent reviewers, restricted
database credentials and tested recovery. Clear test data only through separately
reviewed scoped procedures; this command handles Vocal Duet alone. The original
production two-person process remains intact.

## Local validation

Run the focused tests only with an isolated PostgreSQL test configuration, never
with credentials pointing at Kamatera. They intentionally create disposable test
data and exercise orphan rows. No migration files are included in this release.

```powershell
# In a local shell configured for your isolated PostgreSQL test database:
python manage.py test events.test_vocal_duet_simple events.test_vocal_duet_development events.test_vocal_duet_reset events.test_vocal_duet_host_identity --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
```

Linux `openat`/`O_NOFOLLOW` integration checks are skipped on Windows; rerun the
focused suite on an isolated Linux test database before adopting this helper.
No Django migrations should be applied as part of this fix.
