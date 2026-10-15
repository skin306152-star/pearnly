"""Credential values and token parsing, without framework or IO dependencies."""
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import re

MAX_TOKEN_LENGTH = 1024
MAX_RECEIPT_BYTES = 1024 * 1024
REVOCATION_RESERVE_BYTES = 1024
_CONNECTION = re.compile(r'^[A-Za-z0-9.-]{1,140}$')
_SECRET = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
_DIGEST = re.compile(r'^[0-9a-f]{64}$')
_DEVICE_KEY = re.compile(r'^[A-Za-z0-9_-]{32,128}$')


class CredentialError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def text(value, *, maximum=140, optional=False):
    if optional and value in (None, ''):
        return ''
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\x00' in value:
        raise CredentialError('companion.request_invalid')
    return value.strip()


def generation(value):
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) is not int or not 1 <= value <= 2147483647:
        raise CredentialError('companion.request_invalid')
    return value


def encode(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as error:
        raise CredentialError('companion.request_invalid') from error


def fingerprint(value):
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


def operation_identity(site, actor, connection, request_key):
    return fingerprint([text(site), text(actor), text(connection), text(request_key, maximum=200)])


def wire_flag(value):
    """HTTP form 把布尔值送成字符串：只认这四种确切写法，别的原样交给合同去拒绝。"""
    return {'1': True, 'true': True, '0': False, 'false': False}.get(value, value) if isinstance(value, str) else value


def issue_intent(reset, expected_generation):
    if type(reset) is not bool or (not reset and expected_generation is not None):
        raise CredentialError('companion.request_invalid')
    expected = generation(expected_generation) if reset else None
    return {'operation': 'issue', 'reset': reset, 'expected_generation': expected}


def revoke_intent(expected_generation, reason):
    return {'operation': 'revoke', 'expected_generation': generation(expected_generation),
            'reason': text(reason, maximum=240, optional=True)}


def active_identity(site, connection, device=None):
    """The unique "this key is current" value.

    Without a device it is the original per-connection slot: keys issued before CC Desktop knew about computers keep
    it forever, even after their computer is recorded (ADR-021), so a server rollback still recognises them. Keys
    issued by register v2 are per computer: at most one current key per (connection, device), several per connection.
    """
    if device in (None, ''):
        return fingerprint([text(site), text(connection)])
    return fingerprint([text(site), text(connection), text(device)])


def active_matches(stored, site, connection, device=None):
    """Recompute ``stored`` with the row's own formula: the original slot, or this row's device slot."""
    if not isinstance(stored, str) or not _DIGEST.fullmatch(stored):
        return False
    candidates = [active_identity(site, connection)]
    if device:
        candidates.append(active_identity(site, connection, device))
    return any(hmac.compare_digest(stored, value) for value in candidates)


def device_key(value):
    """The computer's own random key (CC Desktop keeps it under Windows DPAPI). Only its salted digest is stored."""
    if not isinstance(value, str) or not _DEVICE_KEY.fullmatch(value):
        raise CredentialError('companion.request_invalid')
    return value


def device_digest(site, value):
    return fingerprint(['cc-desktop-device', text(site), device_key(value)])


def device_issue_intent(device, rekey):
    if type(rekey) is not bool:
        raise CredentialError('companion.request_invalid')
    return {'operation': 'issue', 'device': text(device), 'rekey': rekey}


def generation_identity(site, connection, value):
    return fingerprint([text(site), text(connection), generation(value)])


def token_digest(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class ParsedToken:
    connection: str
    digest: str = field(repr=False)


def parse_token(token):
    """Native legacy names may be CWEC-UUID; no managed UUID reinterpretation."""
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH or not token.startswith('exp_'):
        return None
    parts = token.split('_', 2)
    if len(parts) != 3 or not _CONNECTION.fullmatch(parts[1]) or not _SECRET.fullmatch(parts[2]):
        return None
    return ParsedToken(parts[1], token_digest(token))


def token_matches(stored, supplied):
    return (isinstance(stored, str) and isinstance(supplied, str)
            and bool(_DIGEST.fullmatch(stored)) and bool(_DIGEST.fullmatch(supplied))
            and hmac.compare_digest(stored, supplied))


def append_receipt(raw, operation, intent, result):
    try:
        receipts = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    except (ValueError, TypeError) as error:
        raise CredentialError('companion.request_invalid') from error
    if not isinstance(receipts, dict) or not _DIGEST.fullmatch(operation):
        raise CredentialError('companion.request_invalid')
    value = {'intent_hash': fingerprint(intent), 'result': result}
    if operation in receipts and receipts[operation] != value:
        raise CredentialError('companion.request_conflict')
    receipts[operation] = value
    encoded = encode(receipts)
    # Every fresh/exists alias leaves room for a first permanent revoke receipt.
    limit = MAX_RECEIPT_BYTES if result == 'revoked' else MAX_RECEIPT_BYTES - REVOCATION_RESERVE_BYTES
    if len(encoded.encode('utf-8')) > limit:
        raise CredentialError('companion.receipt_limit')
    return encoded


@dataclass(frozen=True)
class AgentContext:
    site: str
    connection: str
    workspace: str
    client: str
    issuer: str
    credential_id: str
    token_generation: int
    branch: str = 'legacy'
    binding_generation: int = 0
    _capability: object = field(default=None, repr=False, compare=False)
