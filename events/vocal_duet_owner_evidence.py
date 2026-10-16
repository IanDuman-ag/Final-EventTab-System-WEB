"""Standard-library-only evidence rules shared with the installed root helper."""
import re
import unicodedata


DEVELOPMENT_CONFIRMATION = 'ALL CURRENT EVENTTAB SCORES AND PUBLISHED RESULTS ARE DEVELOPMENT TEST DATA; NO ACTUAL SCHOOL COMPETITION DATA'
CONFIRMATIONS = (
    'RESET ONLY VOCAL DUET TEST SCORES 175-190',
    'DELETE ONLY VOCAL DUET TEST SCORES 175-190 AND REPAIR 64/79',
    'DEVELOPMENT ONLY: RESET VOCAL DUET TEST SCORES 175-190 ON VERIFIED TARGET',
    DEVELOPMENT_CONFIRMATION,
)


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


def evidence_error(reference):
    if not isinstance(reference, str) or not 20 <= len(reference.strip()) <= 2000:
        return 'Provide an actual ownership/development-use record reference (20–2000 characters).'
    if any(unicodedata.category(c).startswith('C') for c in reference):
        return 'Evidence references cannot contain control/format characters.'
    value = normalized(reference)
    if any(normalized(phrase) in value for phrase in CONFIRMATIONS):
        return 'A reset/usage confirmation phrase is not an evidence reference.'
    if not re.search(r'kamatera|console|https?://|\brecord\b|\bticket\b|\bdocument\b|\breceipt\b', reference, re.I):
        return 'Reference the actual console page, record, ticket, document or URL reviewed; do not enter an approval phrase.'
    return None


def approval_evidence_error(approval):
    if not isinstance(approval, dict):
        return 'Approval must be an object.'
    error = evidence_error(approval.get('owner_evidence_reference'))
    if error:
        return error
    if approval.get('evidence_version') != 2 or approval.get('development_use_confirmed') is not True:
        return 'Explicit current development-only use confirmation is required; reject the old approval and request reapproval.'
    return None
