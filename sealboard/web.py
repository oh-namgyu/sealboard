"""Blueprint, request helpers and the owner check shared by every route (see routes.py)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from sealboard import store as st
from sealboard.auth import FailLimiter, hash_auth_key, key_matches, presented_key

PUBLIC_ENDPOINTS = {'sealboard.index', 'sealboard.vault_info', 'sealboard.create_vault'}

bp = Blueprint('sealboard', __name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def store() -> st.Store:
    return current_app.extensions['sealboard']['store']


def limiter() -> FailLimiter:
    return current_app.extensions['sealboard']['limiter']


def client() -> str:
    return request.remote_addr or '?'


def body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise st.Reject(400, 'json object required')
    return data


def vault_record(params: Any, auth_key: Any) -> dict:
    return dict(st.vault_params(params), authHash=hash_auth_key(auth_key), updatedAt=now_iso())


def too_many() -> Any:
    return jsonify({'error': 'too many failed attempts'}), 429, {'Retry-After': '300'}


@bp.before_request
def _require_owner() -> Any:
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    key = presented_key(request.headers.get('Authorization'))
    if key is None:                                   # no key at all: not a guess, do not count it
        return jsonify({'error': 'unauthorized'}), 401
    if limiter().blocked(client()):
        return too_many()
    auth_hash = store().read(lambda b: (b.get('vault') or {}).get('authHash'))
    if not key_matches(key, auth_hash):
        limiter().fail(client())
        return jsonify({'error': 'unauthorized'}), 401
    return None


@bp.errorhandler(st.Reject)
def _rejected(e: st.Reject) -> Any:
    return jsonify(e.body), e.status
