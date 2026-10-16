# Vocal Duet owner approval hotfix — stop after approval

**No reset is part of this hotfix. Do not run `apply`.** No migrations, APK build,
service restart, database writes or edits to existing evidence are required.

## Finding

`database_identity()` stores PostgreSQL `inet_server_addr()::text`, which returns
`127.0.0.1/32`. The old helper requires exactly `127.0.0.1`, causing the generic
refusal. A local PostgreSQL read-only query reproduced this discrepancy. The fix
accepts those two exact representations, preserving the original target and
checkpoint digest. Other hosts, network masks and IPv6 remain rejected.

The two reported observation hashes refer to different evidence. Refreshing
`observe` changes the hash even if the physical host is unchanged. Django already
rejects that target change; the helper now checks the current root-owned
observation hash, actual host/boot and two-hour freshness too. **Do not refresh
observe, rerun preview or edit a verified checkpoint just to fix the address.**
The helper follows `simple-current.json`; it does not select the initial session.
Without the live checkpoint, other failed conditions cannot be excluded. The new
read-only `inspect` names every failed predicate without approving anything.

## A. Windows PowerShell — upload only the hotfix

```powershell
Set-Location C:\Users\Admin\Desktop\eventtabs
$bundle = Join-Path $env:TEMP 'eventtab-vocal-owner.tgz'
tar -czf $bundle execution/vocal_duet_simple_owner.py events/vocal_duet_simple.py events/test_vocal_duet_simple.py VOCAL_DUET_OWNER_APPROVAL_FIX.md
Get-FileHash -Algorithm SHA256 $bundle
$sshUser = Read-Host 'Your legitimate sudo-capable SSH account'
scp $bundle "${sshUser}@103.125.217.125:/tmp/eventtab-vocal-owner.tgz"
ssh -t "${sshUser}@103.125.217.125"
```

## B. Ubuntu SSH — verify, install, inspect, approve

Install from a verified root-private copy. Paste the SHA256 printed on Windows
when prompted. Previous code is retained in the new release directory.

```bash
sudo -i
umask 077
release=$(mktemp -d /root/eventtab-vocal-owner.XXXXXX)
cp /tmp/eventtab-vocal-owner.tgz "$release/hotfix.tgz"
read -r -p 'Paste the Windows SHA256: ' expected
actual=$(sha256sum "$release/hotfix.tgz" | cut -d ' ' -f 1)
if [ "$actual" != "${expected,,}" ]; then echo 'Checksum mismatch; STOP'; exit 1; fi
tar -xzf "$release/hotfix.tgz" -C "$release" --no-same-owner
backend=/srv/eventtab/Final-EventTab-System-WEB
cp -p /usr/local/libexec/eventtab-vocal-duet-owner.py "$release/owner.previous.py"
cp -p "$backend/events/vocal_duet_simple.py" "$release/simple.previous.py"
install -o root -g root -m 0755 "$release/execution/vocal_duet_simple_owner.py" /usr/local/libexec/eventtab-vocal-duet-owner.py
for file in execution/vocal_duet_simple_owner.py events/vocal_duet_simple.py events/test_vocal_duet_simple.py VOCAL_DUET_OWNER_APPROVAL_FIX.md; do
  install -o root -g root -m 0644 "$release/$file" "$backend/$file"
done
exit
sudo -iu eventtab
cd /srv/eventtab/Final-EventTab-System-WEB
source venv/bin/activate
python manage.py check
python manage.py test events.test_vocal_duet_simple.RootOwnerHelperTests events.test_vocal_duet_simple.OwnerApprovalDiagnosticTests --noinput
exit
sudo /usr/local/libexec/eventtab-vocal-duet-owner.py inspect
```

Inspect must show the intended current session (expected latest:
`simple-e12d94844961a3c3`), its checkpoint digest, exact target, backup digest,
`can_approve: true` and `failed_checks: []`. It reads host/evidence files only,
never connects to PostgreSQL and never writes an approval.

Only after checking this output and actual development-use/roster/backup evidence:

```bash
sudo /usr/local/libexec/eventtab-vocal-duet-owner.py approve
```

This records approval only. **Stop here.** Production two-person approval, root
ownership checks, confirmation, target/session/backup digest binding, replay
protection and the production retirement seal remain in place.

If inspection names `checkpoint.verified_at`, it is expired/future/non-UTC.
If it names `target.root_observation_sha256`, observation changed after verification.
If it names `root_observation.freshness`, obtain a fresh root observation first.
These require a new `preview` and `verify` as **eventtab**, followed by a fresh
`inspect`/owner approval; never edit timestamps, hash fields or confirmations.
Keep all older sessions, backup and isolated restore. No stale checkpoint can
legitimately be approved merely because its host UUID is unchanged.

## Checks and recovery

The approval-only test labels are:
`events.test_vocal_duet_simple.RootOwnerHelperTests` and
`events.test_vocal_duet_simple.OwnerApprovalDiagnosticTests`. Both use
`SimpleTestCase`, with database setup/access forbidden. Do not run the older
reset tests when the intended constraint is no score deletion, even locally.
Linux `O_NOFOLLOW` execution must also be tested on an isolated Linux host;
Windows skips that single check.
The helper also rereads the checkpoint, session and observation after interactive
confirmation, refusing changes or expiry before it writes the approval.

If installation fails, stop before approval. Retain the root-private release and
previous code; no data rollback is needed because this hotfix changes no database
records. Do not edit or replace backup/restore/review/checkpoint artifacts.
