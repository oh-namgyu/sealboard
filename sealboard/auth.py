"""Zero-knowledge access control.

The browser derives a 256-bit master secret with PBKDF2 over the passphrase and splits it with HKDF
into a key-encryption key (never leaves the browser) and an *auth key* sent as a bearer token. The
server keeps only SHA-256(auth key), so it can recognise the owner but holds nothing that decrypts the
notes. A one-time setup code (printed at start-up) gates creation of the first vault.
"""
from __future__ import annotations

import hashlib
import hmac
import threading
import time
from typing import Dict, List, Optional

from sealboard.store import Reject, decode_b64

AUTH_KEY_BYTES = 32
MAX_FAILS = 10                # failed attempts per client address …
FAIL_WINDOW_SEC = 300         # … within this window before 429
MAX_TRACKED_CLIENTS = 10_000  # bound memory if many addresses fail once (e.g. rotating IPv6)


def hash_auth_key(value: object) -> str:
    """Validate a base64 32-byte auth key and return its SHA-256 hex digest."""
    raw = decode_b64(value, 64, 'authKey')
    if len(raw) != AUTH_KEY_BYTES:
        raise Reject(400, 'bad authKey')
    return hashlib.sha256(raw).hexdigest()


def presented_key(header: Optional[str]) -> Optional[str]:
    """The bearer token from an Authorization header, or None when no key was presented at all."""
    if header and header.startswith('Bearer '):
        return header[len('Bearer '):].strip() or None
    return None


def key_matches(key: str, auth_hash: Optional[str]) -> bool:
    if not auth_hash:
        return False
    try:
        return hmac.compare_digest(hash_auth_key(key), auth_hash)
    except Reject:
        return False


def setup_code_matches(given: object, expected: Optional[str]) -> bool:
    return bool(expected) and isinstance(given, str) and hmac.compare_digest(given.encode(), expected.encode())


class FailLimiter:
    """Failed attempts per client address in a fixed window: address → [count, first failure time]."""

    def __init__(self) -> None:
        self._fails: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def _entry(self, client: str, now: float) -> Optional[List[float]]:
        entry = self._fails.get(client)
        if entry and now - entry[1] >= FAIL_WINDOW_SEC:
            del self._fails[client]
            return None
        return entry

    def blocked(self, client: str) -> bool:
        with self._lock:
            entry = self._entry(client, time.time())
            return bool(entry) and entry[0] >= MAX_FAILS

    def fail(self, client: str) -> None:
        with self._lock:
            now = time.time()
            entry = self._entry(client, now)
            if entry:
                entry[0] += 1
                return
            if len(self._fails) >= MAX_TRACKED_CLIENTS:
                self._fails = {c: e for c, e in self._fails.items() if now - e[1] < FAIL_WINDOW_SEC}
            self._fails[client] = [1, now]
