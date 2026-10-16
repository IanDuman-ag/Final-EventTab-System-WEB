#!/usr/bin/python3 -I
"""Root-only, no-argument, read-only host export. No Django, DB or signing key."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import stat
import sys
from uuid import UUID


INSTALLED = Path('/usr/local/libexec/eventtab-export-host-identity.py')
CHALLENGE = Path('/etc/eventtab/development-reset/host-challenge.json')
DMI = Path('/sys/devices/virtual/dmi/id/product_uuid')
MACHINE = Path('/etc/machine-id')
BOOT = Path('/proc/sys/kernel/random/boot_id')


def trusted(path):
    for item in (path, *path.parents):
        info = item.lstat()
        if (info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode) or
                (item == path and not stat.S_ISREG(info.st_mode)) or
                (item != path and not stat.S_ISDIR(info.st_mode))):
            raise ValueError('Untrusted fixed identity/helper path')


def uuid_value(value):
    parsed = UUID(value.strip().lower())
    if parsed.int in (0, (1 << 128) - 1):
        raise ValueError('Invalid host identity')
    return str(parsed)


def export_identity():
    if sys.platform != 'linux' or os.geteuid() != 0 or len(sys.argv) != 1 or Path(__file__) != INSTALLED:
        raise ValueError('Install at the fixed path and run the helper alone as root; no arguments are accepted')
    for path in (INSTALLED, CHALLENGE, DMI, MACHINE, BOOT):
        trusted(path)
    challenge = json.loads(CHALLENGE.read_text())
    now = datetime.now(timezone.utc)
    created = datetime.fromisoformat(challenge['created_at'])
    if (created.utcoffset() != timedelta(0) or not created <= now < created + timedelta(hours=24) or
            challenge.get('purpose') != 'eventtab-host-identity-challenge-v1' or
            not re.fullmatch(r'[0-9a-f]{64}', challenge.get('nonce', ''))):
        raise ValueError('Fresh root-controlled challenge required')
    machine = MACHINE.read_text().strip().lower()
    if not re.fullmatch(r'[0-9a-f]{32}', machine) or machine == '0' * 32:
        raise ValueError('Invalid local machine ID')
    return {'purpose': 'eventtab-root-host-identity-v1', 'collector': 'root-fixed-path-host-export-v1',
        'host_uuid': uuid_value(DMI.read_text()), 'machine_id': machine, 'boot_id': uuid_value(BOOT.read_text()),
        'challenge': challenge['nonce'], 'issued_at': now.isoformat(),
        'expires_at': min(now + timedelta(hours=2), created + timedelta(hours=24)).isoformat()}


if __name__ == '__main__':
    try:
        print(json.dumps(export_identity(), indent=2))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print('Host export failed: check fixed root-owned paths, fresh challenge, Linux root invocation and no arguments. No database was accessed.', file=sys.stderr)
        sys.exit(1)
