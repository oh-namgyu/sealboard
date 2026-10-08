"""Board storage: input validation, locked access to board.json, and orphan image cleanup.

The server never sees plaintext. A note or tab is stored as ``{iv, ct}`` (AES-GCM ciphertext made in
the browser) plus layout fields (position, size, colour, collapsed, tab). Images are opaque encrypted
blobs on disk. The only secret-adjacent value is ``authHash`` = SHA-256 of a key the browser derives
from the passphrase; it lets the server tell the owner apart without being able to decrypt anything.

board.json is the single source of truth. It is parsed once and re-read only when its mtime/size
change (so restoring a backup while the server runs takes effect immediately).
"""
from __future__ import annotations

import base64
import binascii
import contextlib
import json
import os
import re
import tempfile
import threading
import time
from typing import Any, Callable, Optional, Tuple

ID_RE = re.compile(r'[tnb]_[A-Za-z0-9_-]{10,40}')
COLORS = ('yellow', 'pink', 'green', 'blue', 'purple', 'gray')
GEOM = {'x': (0, 50000), 'y': (0, 50000), 'w': (160, 4000), 'h': (44, 4000), 'z': (0, 10 ** 7)}
MAX_CT = 1_000_000            # one note's ciphertext (base64); images travel separately as blobs
MAX_BLOB = 8 * 1024 * 1024
MAX_BLOBS_PER_NOTE = 100
MIN_KDF_ITER = 600_000       # the browser always uses 600k and refuses anything lower
ORPHAN_GRACE_SEC = 3600       # an unreferenced blob is deleted only after this long (upload→save gap, cut/paste)


class Reject(Exception):
    """A validation or conflict error; the app turns it into ``(json, status)``."""

    def __init__(self, status: int, error: str, **extra: Any) -> None:
        super().__init__(error)
        self.status, self.body = status, {'error': error, **extra}


def empty() -> dict:
    return {'version': 1, 'vault': None, 'tabs': [], 'notes': [], 'orphans': {}}


class Store:
    """board.json + blobs/ under one data directory. One process, many threads: one lock guards the board."""

    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir
        self.blob_dir = os.path.join(data_dir, 'blobs')
        self.board_path = os.path.join(data_dir, 'board.json')
        self._lock = threading.Lock()
        self._cache: Optional[Tuple[Tuple[int, int], dict]] = None

    def blob_path(self, bid: str) -> str:
        return os.path.join(self.blob_dir, bid + '.bin')

    def _board(self) -> dict:
        """The parsed board (caller holds the lock and must not keep it past the lock)."""
        try:
            stat = os.stat(self.board_path)
        except FileNotFoundError:
            self._cache = None
            return empty()
        sig = (stat.st_mtime_ns, stat.st_size)
        if self._cache is None or self._cache[0] != sig:
            with open(self.board_path, encoding='utf-8') as f:
                self._cache = (sig, json.load(f))
        return self._cache[1]

    def read(self, view: Callable[[dict], Any]) -> Any:
        """Run ``view(board)`` under the lock. ``view`` must not mutate the board or let it escape."""
        with self._lock:
            return view(self._board())

    def update(self, mutate: Callable[[dict], Any]) -> Any:
        """Mutate the board and save it atomically. If ``mutate`` raises, nothing is written."""
        with self._lock:
            board = self._board()
            try:
                out = mutate(board)
            except BaseException:
                self._cache = None          # drop any half-applied change; the file is untouched
                raise
            os.makedirs(self.data_dir, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.data_dir, prefix='.board-', suffix='.tmp')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(json.dumps(board, ensure_ascii=False, separators=(',', ':')))
            os.replace(tmp, self.board_path)
            stat = os.stat(self.board_path)
            self._cache = ((stat.st_mtime_ns, stat.st_size), board)
            return out

    def sweep_blobs(self, board: dict) -> None:
        """Remember when each unreferenced blob was first seen; delete it once ORPHAN_GRACE_SEC has passed."""
        refs = {b for n in board['notes'] for b in n.get('blobs', [])}
        try:
            files = {f[:-4] for f in os.listdir(self.blob_dir) if f.endswith('.bin')}
        except FileNotFoundError:
            files = set()
        now, seen, keep = time.time(), board.get('orphans', {}), {}
        for bid in files - refs:
            first = seen.get(bid, now)
            if now - first > ORPHAN_GRACE_SEC:
                with contextlib.suppress(FileNotFoundError):
                    os.remove(self.blob_path(bid))
            else:
                keep[bid] = first
        board['orphans'] = keep


# ── validation ─────────────────────────────────────────────────────────────

def check_id(value: Any, kind: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value) or value[0] != kind:
        raise Reject(400, 'bad id')
    return value


def decode_b64(value: Any, limit: int, field: str) -> bytes:
    """Strict base64 (no whitespace, correct padding) up to ``limit`` characters → bytes."""
    if not isinstance(value, str) or not value or len(value) > limit or len(value) % 4:
        raise Reject(400, 'bad ' + field)
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise Reject(400, 'bad ' + field)


def sealed(body: dict) -> dict:
    """``{iv, ct}`` — the iv is a 96-bit AES-GCM nonce."""
    if len(decode_b64(body.get('iv'), 16, 'iv')) != 12:
        raise Reject(400, 'bad iv')
    decode_b64(body.get('ct'), MAX_CT, 'ct')
    return {'iv': body['iv'], 'ct': body['ct']}


def layout(body: dict) -> dict:
    out: dict = {}
    for key, (lo, hi) in GEOM.items():
        if key in body:
            v = body[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise Reject(400, 'bad ' + key)
            out[key] = int(min(max(v, lo), hi))
    if 'collapsed' in body:
        out['collapsed'] = bool(body['collapsed'])
    if 'color' in body:
        if body['color'] not in COLORS:
            raise Reject(400, 'bad color')
        out['color'] = body['color']
    if 'tab' in body:
        out['tab'] = check_id(body['tab'], 't')
    return out


def blob_ids(body: dict) -> list:
    ids = body.get('blobs', [])
    if not isinstance(ids, list) or len(ids) > MAX_BLOBS_PER_NOTE:
        raise Reject(400, 'bad blobs')
    return sorted({check_id(b, 'b') for b in ids})


def vault_params(body: Any) -> dict:
    """KDF parameters + the wrapped data key. The auth key is validated separately (see auth.py)."""
    if not isinstance(body, dict) or body.get('kdf') != 'PBKDF2-SHA256':
        raise Reject(400, 'bad vault')
    it = body.get('iter')
    if isinstance(it, bool) or not isinstance(it, int) or not MIN_KDF_ITER <= it <= 10_000_000:
        raise Reject(400, 'bad iter')
    if len(decode_b64(body.get('salt'), 64, 'salt')) < 16:
        raise Reject(400, 'bad salt')
    wrapped = body.get('wrapped')
    if not isinstance(wrapped, dict):
        raise Reject(400, 'bad wrapped')
    return {'kdf': 'PBKDF2-SHA256', 'iter': it, 'salt': body['salt'], 'wrapped': sealed(wrapped)}


def find(items: list, item_id: str) -> Optional[dict]:
    return next((x for x in items if x['id'] == item_id), None)


def check_rev(current: Optional[dict], body: dict) -> int:
    """Optimistic concurrency: if another window saved first, 409 with the current copy (no silent overwrite)."""
    base = body.get('baseRev')
    have = current['rev'] if current else 0
    if isinstance(base, bool) or not isinstance(base, int) or base != have:
        raise Reject(409, 'conflict', current=current)
    return have + 1
