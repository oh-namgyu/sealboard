"""Local client mode: the page comes from this process, /api/* is forwarded to a real remote sealboard."""
import os
import threading

import pytest
from conftest import SETUP, b64key, bearer, read_board, vault_body
from werkzeug.serving import make_server

from sealboard import cli
from sealboard.app import create_app
from sealboard.remote import check_remote, create_client_app


@pytest.fixture
def remote(tmp_path):
    app = create_app(str(tmp_path / 'remote'), setup_code=SETUP)
    srv = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_port}', app.extensions['sealboard']['store']
    srv.shutdown()


@pytest.mark.parametrize('url', ['http://notes.example.org', 'ftp://notes.example.org', 'https://notes.example.org/sub',
                                 'https://notes.example.org/?x=1', 'notes.example.org'])
def test_remote_must_be_an_https_root(url):
    with pytest.raises(ValueError):
        check_remote(url)


def test_remote_is_normalized():
    assert check_remote('https://Notes.example.org/') == 'https://Notes.example.org'
    assert check_remote('http://127.0.0.1:8080') == 'http://127.0.0.1:8080'


def test_round_trip_through_the_local_client(remote):
    url, store = remote
    client = create_client_app(url).test_client()
    assert client.get('/api/vault').get_json() == {'exists': False}
    assert client.post('/api/vault', json=vault_body()).status_code == 200
    assert client.get('/api/board').status_code == 401                       # status codes pass through
    board = client.get('/api/board', headers=bearer())
    assert board.status_code == 200 and board.headers['Cache-Control'] == 'no-store'
    data = os.urandom(64)
    assert client.put('/api/blobs/b_abcdefghij', data=data, headers=bearer()).status_code == 200
    got = client.get('/api/blobs/b_abcdefghij', headers=bearer())
    assert got.data == data and 'immutable' in got.headers['Cache-Control']
    assert '"authHash"' in read_board(store)                                  # the data lives on the remote


def test_rate_limit_headers_pass_through(remote):
    url, _ = remote
    client = create_client_app(url).test_client()
    client.post('/api/vault', json=vault_body())
    for _ in range(10):
        client.get('/api/board', headers=bearer(b64key(b'x')))
    r = client.get('/api/board', headers=bearer())
    assert r.status_code == 429 and r.headers['Retry-After']


def test_page_is_local_and_api_reports_an_unreachable_remote():
    client = create_client_app('http://127.0.0.1:9').test_client()               # nothing listens on port 9
    page = client.get('/')
    assert page.status_code == 200 and "script-src 'self'" in page.headers['Content-Security-Policy']
    r = client.get('/api/vault')
    assert r.status_code == 502 and 'unreachable' in r.get_json()['error']
    assert client.get('/api/../x').status_code in (400, 404)
    assert client.get('/api/vault', headers={'Host': 'evil.example'}).status_code == 400


def test_cli_refuses_a_public_bind_or_bad_remote(capsys):
    assert cli.main(['--remote', 'https://notes.example.org', '--host', '0.0.0.0']) == 2
    assert 'localhost only' in capsys.readouterr().err
    assert cli.main(['--remote', 'http://notes.example.org']) == 2
    assert 'https' in capsys.readouterr().err
