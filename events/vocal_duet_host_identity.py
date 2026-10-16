"""Unprivileged verification of an off-server-signed root host observation."""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
from uuid import UUID

from django.utils import timezone

from .vocal_duet_reset import ResetError


MACHINE_ID = Path('/etc/machine-id')
BOOT_ID = Path('/proc/sys/kernel/random/boot_id')


def _uuid(value):
    try:
        parsed = UUID(value)
        if value != str(parsed) or parsed.int in (0, (1 << 128) - 1):
            raise ValueError
        return str(parsed)
    except (ValueError, TypeError, AttributeError):
        raise ResetError('Host attestation contains an invalid UUID.') from None


def _utc(value):
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() != timedelta(0):
        raise ValueError
    return parsed


def verified_host_identity():
    # Resolve the fixed trusted directory at call time; no CLI/env override exists.
    from .vocal_duet_development import DIRECTORY, _root_controlled, _verify_signature
    paths = {name: DIRECTORY / name for name in (
        'host-attestation.json', 'host-attestation.sig', 'host-challenge.json', 'authority-public.pem')}
    try:
        for path in (*paths.values(), MACHINE_ID, BOOT_ID):
            _root_controlled(path)
        payload = paths['host-attestation.json'].read_bytes()
        _verify_signature(payload, paths['host-attestation.sig'], paths['authority-public.pem'], 'Host identity attestation')
        observation = json.loads(payload)
        challenge = json.loads(paths['host-challenge.json'].read_text())
        if not isinstance(observation, dict) or not isinstance(challenge, dict):
            raise ValueError
        now = timezone.now()
        created = _utc(challenge['created_at'])
        issued, expires = _utc(observation['issued_at']), _utc(observation['expires_at'])
        nonce = challenge.get('nonce')
        if (challenge.get('purpose') != 'eventtab-host-identity-challenge-v1' or
                not isinstance(nonce, str) or not re.fullmatch(r'[0-9a-f]{64}', nonce) or
                observation.get('purpose') != 'eventtab-root-host-identity-v1' or
                observation.get('collector') != 'root-fixed-path-host-export-v1' or
                observation.get('challenge') != nonce or
                not (created <= issued <= now < expires <= created + timedelta(hours=24)) or
                not timedelta(0) < expires - issued <= timedelta(hours=24)):
            raise ResetError('Host attestation is expired, stale or does not match the current root challenge. Request a fresh root export and off-server signature.')
        machine = MACHINE_ID.read_text().strip().lower()
        boot = _uuid(BOOT_ID.read_text().strip().lower())
        if not re.fullmatch(r'[0-9a-f]{32}', machine) or machine == '0' * 32:
            raise ResetError('The local machine ID cannot be verified.')
        if observation.get('machine_id') != machine or observation.get('boot_id') != boot:
            raise ResetError('Host attestation belongs to a different machine or boot. Request a fresh root export and off-server signature.')
        return {'host_uuid': _uuid(observation.get('host_uuid')), 'host_machine_id': machine,
            'host_boot_id': boot, 'host_attestation_sha256': hashlib.sha256(payload).hexdigest()}
    except ResetError:
        raise
    except OSError:
        raise ResetError('Host attestation or its trusted local identity files are missing/unreadable. Ask the infrastructure owner to install a signed root host attestation; do not run Django as root or chmod DMI.') from None
    except (ValueError, TypeError, KeyError):
        raise ResetError('Host attestation/challenge is malformed. Obtain a fresh verified root export and off-server signature.') from None
