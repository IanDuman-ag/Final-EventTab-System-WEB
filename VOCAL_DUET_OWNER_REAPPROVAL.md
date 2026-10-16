# Correct Vocal Duet owner evidence — approval only

**Do not run `apply`. No score records or PostgreSQL data are changed by this
procedure. Do not manually edit, remove, backdate or forge approval/audit files.**

The old helper accepted any nonempty evidence text. This release rejects reset
and usage confirmation phrases, including case/spacing/punctuation variants,
both in the root helper and Django's owner-authorization consumer. The shared
rules require a record/console/document/URL reference, but cannot establish its
truth automatically: the legitimate owner must actually review the records.
Legacy approvals require explicit reapproval under evidence version2.

`reject` preserves the original approval bytes in a new, exclusively created,
root-owned `owner-rejected-<approval-sha256>.json` audit. It records reason,
checkpoint hash and rejection time, without editing the old approval. Django
denies that exact old approval. Rejection works even if the checkpoint expired.
`approve` refuses silent replacement. After audited rejection, fresh owner
approval links the exact old approval and rejection hashes. The checkpoint is
never modified. Root helpers serialize mutating operations with a trusted flock.

## A. Windows PowerShell

```powershell
Set-Location C:\Users\Admin\Desktop\eventtabs
$bundle = Join-Path $env:TEMP 'eventtab-owner-evidence.tgz'
tar -czf $bundle execution/vocal_duet_simple_owner.py events/vocal_duet_simple.py events/vocal_duet_owner_evidence.py events/test_vocal_duet_simple.py events/test_vocal_duet_owner_evidence.py execution/test_vocal_duet_owner_linux.py execution/vocal_duet_owner_linux.Dockerfile VOCAL_DUET_OWNER_REAPPROVAL.md
Get-FileHash -Algorithm SHA256 $bundle
$sshUser = Read-Host 'Your legitimate sudo-capable SSH account'
scp $bundle "${sshUser}@103.125.217.125:/tmp/eventtab-owner-evidence.tgz"
ssh -t "${sshUser}@103.125.217.125"
```

## B. Ubuntu SSH — deploy together, then reject/inspect/reapprove

Keep all reset operations stopped during deployment. Existing public results,
Pageants, Modern Dance, scores, users, rosters and official configuration remain
untouched. Install helper and shared rules from a verified root-private release;
never execute application-writable Python as root. No migrations/APK rebuild or
web service restart is needed for these management-command-only changes.

```bash
sudo -i
umask 077
release=$(mktemp -d /root/eventtab-owner-evidence.XXXXXX)
cp /tmp/eventtab-owner-evidence.tgz "$release/release.tgz"
read -r -p 'Paste the Windows SHA256: ' expected
actual=$(sha256sum "$release/release.tgz" | cut -d ' ' -f 1)
if [ "$actual" != "${expected,,}" ]; then echo 'Checksum mismatch; STOP'; exit 1; fi
tar -xzf "$release/release.tgz" -C "$release" --no-same-owner
backend=/srv/eventtab/Final-EventTab-System-WEB
cp -p /usr/local/libexec/eventtab-vocal-duet-owner.py "$release/previous-owner.py"
cp -p "$backend/events/vocal_duet_simple.py" "$release/previous-simple.py"
install -o root -g root -m 0755 "$release/execution/vocal_duet_simple_owner.py" /usr/local/libexec/eventtab-vocal-duet-owner.py
install -o root -g root -m 0644 "$release/events/vocal_duet_owner_evidence.py" /usr/local/libexec/eventtab-vocal-duet-evidence.py
for file in execution/vocal_duet_simple_owner.py events/vocal_duet_simple.py events/vocal_duet_owner_evidence.py events/test_vocal_duet_simple.py events/test_vocal_duet_owner_evidence.py execution/test_vocal_duet_owner_linux.py execution/vocal_duet_owner_linux.Dockerfile VOCAL_DUET_OWNER_REAPPROVAL.md; do
  install -o root -g root -m 0644 "$release/$file" "$backend/$file"
done
exit
sudo /usr/local/libexec/eventtab-vocal-duet-owner.py reject
sudo /usr/local/libexec/eventtab-vocal-duet-owner.py inspect
```

For `reject`, state the actual mistake: the confirmation phrase was entered in
the evidence field. Type the exact rejection phrase plus approval SHA256 printed
by the helper. Keep the original approval and audit; repeated rejection is
idempotent. Never use shell commands to remove or edit these files.

Inspect must show the intended session, checkpoint/backup/target hashes,
`can_approve: true`, `failed_checks: []` and current approval state `rejected`.
`can_approve` refers to checkpoint validity; an unrejected existing approval still
cannot be overwritten. If checkpoint/host evidence is stale, obtain a new
`preview` and `verify` as eventtab. Refresh root `observe` only when required,
**before** new preview/verification. Never change old timestamps or hashes.

Only when inspection passes:

```bash
sudo /usr/local/libexec/eventtab-vocal-duet-owner.py approve
sudo /usr/local/libexec/eventtab-vocal-duet-owner.py inspect
```

Three distinct inputs are required:

1. **Evidence reference:** after actually reviewing the supplied supporting
   records, reference their actual locations, for example:
   `Kamatera Console > My Cloud > Servers > eventtab-server > Overview (103.125.217.125); Servers > eventtab-server > History (server created 2026-10-14 UTC)`.
   Do not enter credentials, session tokens or a reset confirmation phrase.
2. **Current-use confirmation:** only if true, type the exact usage statement:
   `ALL CURRENT EVENTTAB SCORES AND PUBLISHED RESULTS ARE DEVELOPMENT TEST DATA; NO ACTUAL SCHOOL COMPETITION DATA`.
   The console establishes infrastructure ownership/creation, not the school's
   data classification; the actual owner must separately verify that statement.
3. **Scope confirmation:** type `RESET ONLY VOCAL DUET TEST SCORES 175-190` in
   its separate confirmation prompt.

Final inspection should show `current_approval.state: recorded`, no evidence
error and the new approval hash. Review the new root-owned approval's
`supersedes_approval_sha256` and `rejection_record_sha256` through the helper's
status output. This records authorization only: **stop here, do not run apply**.
Production two-person authorization, host/cluster targeting, backup/restore
verification, retirement seal, round permissions and atomic rollback remain.

## Tests — isolate, never use Kamatera's database

Approval-only Django tests use `SimpleTestCase` and forbid database access:

```bash
python manage.py test events.test_vocal_duet_simple.RootOwnerHelperTests events.test_vocal_duet_simple.OwnerApprovalDiagnosticTests events.test_vocal_duet_owner_evidence.EvidenceRulesTests events.test_vocal_duet_owner_evidence.OwnerEvidenceWorkflowTests --noinput
python manage.py check
```

Run real Linux ownership/symlink/flock tests **locally in disposable Docker**, not
as root on the deployed server. The test image installs Python only for testing;
it starts a new empty PostgreSQL18 cluster on private port55449, creates no score
tables and checks read-only database snapshots before/after. No network, host
mounts, deployment credentials, source database or production volume is attached.
From the backend repository on the developer's machine:

```powershell
docker build -f execution/vocal_duet_owner_linux.Dockerfile -t eventtab-owner-evidence-tests:local .
docker run --rm --network none eventtab-owner-evidence-tests:local
```

The Dockerfile copies only the three explicit test/helper files. Do not pass
database credentials or mount deployment folders. A failure leaves the original
approval rejected; fix the cause and re-request genuine approval. Do not roll
back to a version that accepts invalid evidence or manually remove denial audit
files. No database rollback is needed because this procedure changes no scores.
