"""Access control: setup code, bearer auth key, rate limiting, Host allowlist, what is exposed before login."""
import hashlib
import json

import pytest
from conftest import AUTH_KEY, IV, SETUP, b64key, bearer, read_board, vault_body, write_board

from sealboard.app import create_app

WRONG = bearer(b64key(b'x'))


def test_fresh_instance_exposes_only_existence(client):
    assert client.get('/api/vault').get_json() == {'exists': False}
    assert client.get('/api/board').status_code == 401


def test_setup_requires_the_one_time_code(client):
    for code in ('wrong', None, 'tést-ünicode'):
        assert client.post('/api/vault', json=dict(vault_body(), setupCode=code)).status_code == 403
    assert client.post('/api/vault', json=vault_body()).status_code == 200
    assert client.post('/api/vault', json=vault_body()).status_code == 409   # exists: refused before the code


def test_setup_code_guessing_is_rate_limited(client):
    for _ in range(10):
        client.post('/api/vault', json=dict(vault_body(), setupCode='guess'))
    assert client.post('/api/vault', json=vault_body()).status_code == 429


def test_setup_code_is_generated_when_not_given(tmp_path):
    app = create_app(str(tmp_path / 'd'))
    code = app.config['SEALBOARD_SETUP_CODE']
    assert code and len(code) >= 16
    assert app.test_client().post('/api/vault', json=dict(vault_body(), setupCode=code)).status_code == 200
    assert app.config['SEALBOARD_SETUP_CODE'] is None                        # single use
    assert create_app(str(tmp_path / 'd')).config['SEALBOARD_SETUP_CODE'] is None


def test_vault_info_never_leaks_wrapped_key_or_hash(owner):
    assert set(owner.get('/api/vault').get_json()) == {'exists', 'kdf', 'iter', 'salt'}


def test_board_needs_the_right_bearer_key(owner, store):
    assert owner.get('/api/board').status_code == 401
    assert owner.get('/api/board', headers=WRONG).status_code == 401
    assert owner.get('/api/board', headers=bearer('not-base64!')).status_code == 401
    board = owner.get('/api/board', headers=bearer()).get_json()
    assert board['vault']['wrapped']['ct'] and 'authHash' not in board['vault'] and 'orphans' not in board
    raw = read_board(store)
    assert AUTH_KEY not in raw and '"authHash"' in raw


def test_key_rotation_replaces_the_auth_key_and_the_old_endpoint_is_gone(owner):
    new_key = b64key(b'n')
    body = {'vault': vault_body(ct='bmV3d3JhcA=='), 'authKey': new_key, 'tabs': [], 'notes': []}
    assert owner.put('/api/vault', json=body, headers=bearer()).status_code == 405   # rewrap-only path removed
    assert owner.post('/api/vault/rotate', json=body).status_code == 401
    assert owner.post('/api/vault/rotate', json=body, headers=bearer()).status_code == 200
    assert owner.get('/api/board', headers=bearer()).status_code == 401              # old key is dead
    assert owner.get('/api/board', headers=bearer(new_key)).get_json()['vault']['wrapped']['ct'] == 'bmV3d3JhcA=='


def test_wrong_keys_are_rate_limited_but_missing_keys_are_not(owner):
    for _ in range(15):
        owner.get('/api/board')                                            # no key: not a guess
    assert owner.get('/api/board', headers=bearer()).status_code == 200
    for _ in range(10):
        owner.get('/api/board', headers=WRONG)
    r = owner.get('/api/board', headers=bearer())
    assert r.status_code == 429 and int(r.headers['Retry-After']) > 0


def test_restored_board_file_takes_effect_without_restart(owner, store):
    board = json.loads(read_board(store))
    new_key = b64key(b'r')
    board['vault'] = dict(board['vault'], authHash=hashlib.sha256(b'r' * 32).hexdigest())
    write_board(store, board)
    assert owner.get('/api/board', headers=bearer(new_key)).status_code == 200
    assert owner.get('/api/board', headers=bearer()).status_code == 401


@pytest.mark.parametrize('host, status', [('127.0.0.1:8080', 200), ('localhost', 200), ('[::1]:8080', 200),
                                          ('evil.example', 400), ('notes.example.org', 400)])
def test_unknown_host_headers_are_refused(client, host, status):
    assert client.get('/api/vault', headers={'Host': host}).status_code == status


def test_extra_allowed_hosts(tmp_path):
    app = create_app(str(tmp_path / 'd'), setup_code=SETUP, allowed_hosts=['Notes.Example.org'])
    assert app.test_client().get('/', headers={'Host': 'notes.example.org'}).status_code == 200


def test_security_headers_on_page_and_api(client):
    page = client.get('/')
    assert page.status_code == 200
    csp = page.headers['Content-Security-Policy']
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and 'unsafe' not in csp
    assert client.get('/api/vault').headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('patch', [{'iter': 1000}, {'kdf': 'MD5'}, {'salt': 'not base64!'},
                                   {'salt': 'c2hvcnQ='}, {'wrapped': {'iv': 'x', 'ct': IV}}, {'authKey': 'short'}])
def test_vault_rejects_weak_or_malformed(client, patch):
    assert client.post('/api/vault', json=dict(vault_body(), **patch)).status_code == 400


def test_api_responses_are_sandboxed_data(owner):
    r = owner.get('/api/board', headers=bearer())
    assert r.headers['Content-Security-Policy'] == "default-src 'none'; sandbox"
    assert r.headers['Content-Disposition'] == 'attachment'


@pytest.mark.parametrize('headers', [{'Sec-Fetch-Mode': 'navigate'}, {'Sec-Fetch-Dest': 'script'},
                                     {'Sec-Fetch-Dest': 'document'}])
def test_browsers_may_not_load_the_api_as_a_page_or_script(client, headers):
    assert client.get('/api/vault', headers=headers).status_code == 400
    assert client.get('/api/vault', headers={'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Dest': 'empty'}).status_code == 200

