"""Browser end-to-end in local client mode: page from the local client, ciphertext on the remote."""
import json
import os
import threading

import pytest

playwright_api = pytest.importorskip('playwright.sync_api')
from werkzeug.serving import make_server  # noqa: E402

from sealboard.app import create_app  # noqa: E402
from sealboard.remote import create_client_app  # noqa: E402

pytestmark = pytest.mark.e2e
expect = playwright_api.expect
SETUP = 'client-mode-setup-0001'


def serve(app):
    srv = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f'http://127.0.0.1:{srv.server_port}'


def test_notes_round_trip_through_the_local_client(tmp_path):
    remote_srv, remote_url = serve(create_app(str(tmp_path / 'remote'), setup_code=SETUP))
    client_srv, client_url = serve(create_client_app(remote_url))
    try:
        with playwright_api.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(client_url + '/#setup=' + SETUP)
            page.fill('#pass', 'client mode passphrase')
            page.fill('#pass2', 'client mode passphrase')
            page.click('#lockSubmit')
            page.click('#newNote')
            page.fill('.sb-note-title', 'kept remotely')
            page.fill('.sb-text', 'secret through the proxy')
            expect(page.locator('#saveState')).to_have_text('Saved')
            browser.close()
        with open(os.path.join(tmp_path, 'remote', 'board.json'), encoding='utf-8') as f:
            raw = f.read()
        assert len(json.loads(raw)['notes']) == 1 and 'secret' not in raw and 'remotely' not in raw
        assert not errors, errors
    finally:
        client_srv.shutdown()
        remote_srv.shutdown()
