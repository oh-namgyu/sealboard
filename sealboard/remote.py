"""Local client mode: ``sealboard --remote https://notes.example.org``.

This machine serves the browser app from the installed package and forwards ``/api/*`` to a remote
sealboard. The remote then only ever answers API calls with ciphertext — it has no channel to ship code
to your browser, which closes the "a compromised server serves tampered JavaScript" gap for remote
deployments. (Opening the remote directly, e.g. from a phone, still trusts the remote's code.)
"""
from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any, Iterable
from urllib.parse import urlsplit

from flask import Flask, Response, current_app, jsonify, request

from sealboard.app import base_app

FORWARD_REQUEST = ('Authorization', 'Content-Type')
FORWARD_RESPONSE = ('Content-Type', 'Cache-Control', 'Retry-After')
LOCAL_NAMES = ('localhost', '127.0.0.1', '::1')
TIMEOUT_SEC = 60


def check_remote(url: str) -> str:
    """Normalize the remote to ``scheme://host[:port]``. Only https (plain http just for a local test server)."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ('https', 'http') or not parts.hostname:
        raise ValueError('the remote must look like https://notes.example.org')
    if parts.scheme == 'http' and parts.hostname not in LOCAL_NAMES:
        raise ValueError('the remote must use https:// (plain http only for localhost)')
    if parts.path not in ('', '/') or parts.query or parts.fragment:
        raise ValueError('give the server root, e.g. https://notes.example.org')
    return f'{parts.scheme}://{parts.netloc}'


def _send(req: urllib.request.Request) -> Any:
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:              # 4xx/5xx from sealboard itself: pass them through
        return e.code, e.headers, e.read()


def forward(sub: str) -> Any:
    if '..' in sub.split('/'):
        return jsonify({'error': 'bad path'}), 400
    url = current_app.config['SEALBOARD_REMOTE'] + '/api/' + sub
    if request.query_string:
        url += '?' + request.query_string.decode('ascii', 'replace')
    body = request.get_data() if request.method in ('POST', 'PUT', 'PATCH') else None
    headers = {k: request.headers[k] for k in FORWARD_REQUEST if k in request.headers}
    try:
        status, got, data = _send(urllib.request.Request(url, data=body, method=request.method, headers=headers))
    except (urllib.error.URLError, OSError) as e:
        return jsonify({'error': 'remote unreachable: ' + str(getattr(e, 'reason', e))}), 502
    resp = Response(data, status=status)
    for key in FORWARD_RESPONSE:
        if got.get(key):
            resp.headers[key] = got[key]
    return resp


def index() -> Any:
    return current_app.send_static_file('index.html')


def create_client_app(remote: str, allowed_hosts: Iterable[str] = ()) -> Flask:
    app = base_app(allowed_hosts)
    app.config['SEALBOARD_REMOTE'] = check_remote(remote)
    app.add_url_rule('/', 'index', index)
    app.add_url_rule('/api/<path:sub>', 'forward', forward, methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
    return app
