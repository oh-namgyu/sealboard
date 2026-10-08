"""sealboard HTTP app — serves the browser app and a small JSON API over ciphertext.

Public endpoints: the page, static assets, ``GET /api/vault`` (KDF parameters only) and
``POST /api/vault`` (first-time setup, needs the one-time setup code). Everything else needs
``Authorization: Bearer <auth key>`` — see auth.py for how that key is derived.
"""
from __future__ import annotations

import secrets
from typing import Any, Iterable, Optional

from flask import Flask, Response, current_app, jsonify, request

from sealboard import store as st
from sealboard.auth import FailLimiter
from sealboard.routes import bp

DEFAULT_HOSTS = ('localhost', '127.0.0.1', '[::1]')
SECURITY_HEADERS = {
    'Content-Security-Policy': ("default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; "
                                "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
                                "form-action 'self'"),
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'X-Frame-Options': 'DENY',
}


def _check_host() -> Any:
    """Refuse unknown Host headers (DNS rebinding). Add your domain via SEALBOARD_ALLOWED_HOSTS."""
    host = (request.host or '').lower()
    name = host.rsplit(':', 1)[0] if not host.endswith(']') else host
    if name not in current_app.config['SEALBOARD_ALLOWED_HOSTS']:
        return jsonify({'error': 'host not allowed'}), 400
    return None


def _security_headers(resp: Response) -> Response:
    for key, value in SECURITY_HEADERS.items():
        resp.headers.setdefault(key, value)
    if request.path.startswith('/api/'):
        resp.headers.setdefault('Cache-Control', 'no-store')
    return resp


def create_app(data_dir: str, setup_code: Optional[str] = None, allowed_hosts: Iterable[str] = ()) -> Flask:
    """Build the app for one data directory. If no vault exists yet, ``setup_code`` (or a random one) gates
    its creation and is exposed as ``app.config['SEALBOARD_SETUP_CODE']`` for the launcher to print.
    ``allowed_hosts`` adds Host names (e.g. your domain behind a reverse proxy) to localhost/127.0.0.1/[::1]."""
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = st.MAX_BLOB + 1024 * 1024
    store = st.Store(data_dir)
    has_vault = store.read(lambda b: bool(b.get('vault')))
    app.config['SEALBOARD_SETUP_CODE'] = None if has_vault else (setup_code or secrets.token_urlsafe(16))
    app.config['SEALBOARD_ALLOWED_HOSTS'] = {h.strip().lower() for h in (*DEFAULT_HOSTS, *allowed_hosts) if h.strip()}
    app.extensions['sealboard'] = {'store': store, 'limiter': FailLimiter()}
    app.before_request(_check_host)
    app.register_blueprint(bp)
    app.after_request(_security_headers)
    return app
