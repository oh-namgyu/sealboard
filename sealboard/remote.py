"""Local client mode: ``sealboard --remote https://notes.example.org``.

This machine serves the browser app from the installed package and forwards ``/api/*`` to a remote
sealboard. The remote can only ever hand back *data*: the response type is decided here (JSON, or bytes
for images), every API response is sandboxed and marked as a download, and browsers may not navigate to
``/api``. So a compromised remote has no channel to run code on this origin — it only sees and returns
ciphertext. (Opening the remote's own page directly, e.g. from a phone, still trusts the remote's code.)
"""
from __future__ import annotations

import http.client
import re
import urllib.error
import urllib.request
from typing import Any, Iterable, Optional
from urllib.parse import quote, urlsplit

from flask import Flask, Response, current_app, jsonify, request

from sealboard import store as st
from sealboard.app import base_app

LOCAL_NAMES = ('localhost', '127.0.0.1', '::1')
API_PATH = re.compile(r'[A-Za-z0-9_/-]{1,120}')       # everything the app calls; no dots, so no traversal
TIMEOUT_SEC = 30
BLOB_CACHE = 'private, max-age=31536000, immutable'


def check_remote(url: str) -> str:
    """Normalize the remote to ``scheme://host[:port]``. Only https (plain http just for a local test server)."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ('https', 'http') or not parts.hostname:
        raise ValueError('the remote must look like https://notes.example.org')
    if parts.scheme == 'http' and parts.hostname not in LOCAL_NAMES:
        raise ValueError('the remote must use https:// (plain http only for localhost)')
    if parts.username or parts.password:
        raise ValueError('do not put credentials in the remote URL')
    if parts.path not in ('', '/') or parts.query or parts.fragment:
        raise ValueError('give the server root, e.g. https://notes.example.org')
    return f'{parts.scheme}://{parts.netloc}'


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: it would carry the Authorization header to wherever the remote points."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


def _read(stream: Any) -> bytes:
    data = stream.read(st.MAX_REQUEST + 1)
    if len(data) > st.MAX_REQUEST:
        raise http.client.HTTPException('response too large')
    return data


def _local_response(status: int, data: bytes, retry_after: Optional[str]) -> Any:
    """The remote's bytes with headers decided here — never the remote's Content-Type or caching."""
    if 300 <= status < 400:
        return jsonify({'error': 'the remote tried to redirect; refused'}), 502
    is_blob = request.method == 'GET' and request.path.startswith('/api/blobs/') and status == 200
    resp = Response(data, status=status, mimetype='application/octet-stream' if is_blob else 'application/json')
    if is_blob:
        resp.headers['Cache-Control'] = BLOB_CACHE
    if retry_after and retry_after.isdigit():
        resp.headers['Retry-After'] = retry_after
    return resp


def forward(sub: str) -> Any:
    if not API_PATH.fullmatch(sub) or '//' in sub:
        return jsonify({'error': 'bad path'}), 400
    url = current_app.config['SEALBOARD_REMOTE'] + '/api/' + quote(sub, safe='/')
    body = request.get_data() if request.method in ('POST', 'PUT', 'PATCH') else None
    headers = {k: request.headers[k] for k in ('Authorization', 'Content-Type') if k in request.headers}
    retry_after = None
    try:
        req = urllib.request.Request(url, data=body, method=request.method, headers=headers)
        try:
            with _OPENER.open(req, timeout=TIMEOUT_SEC) as r:
                status, data = r.status, _read(r)
        except urllib.error.HTTPError as e:          # 4xx/5xx from sealboard itself, and refused redirects
            status, data, retry_after = e.code, _read(e), e.headers.get('Retry-After')
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
        return jsonify({'error': 'remote unreachable: ' + str(getattr(e, 'reason', e))}), 502
    return _local_response(status, data, retry_after)


def index() -> Any:
    return current_app.send_static_file('index.html')


def create_client_app(remote: str, allowed_hosts: Iterable[str] = ()) -> Flask:
    app = base_app(allowed_hosts)
    app.config['SEALBOARD_REMOTE'] = check_remote(remote)
    app.add_url_rule('/', 'index', index)
    app.add_url_rule('/api/<path:sub>', 'forward', forward, methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
    return app
