import base64
import json

import pytest

from sealboard.app import create_app

SETUP = 'test-setup-code-0123'
IV = base64.b64encode(b'\x01' * 12).decode()
CT = base64.b64encode(b'ciphertext-bytes').decode()
SALT = base64.b64encode(b'saltsaltsaltsalt').decode()


def b64key(seed: bytes) -> str:
    return base64.b64encode((seed * 32)[:32]).decode()


AUTH_KEY = b64key(b'k')


def bearer(key: str = AUTH_KEY) -> dict:
    return {'Authorization': 'Bearer ' + key}


def vault_body(salt: str = SALT, auth_key: str = AUTH_KEY, ct: str = CT) -> dict:
    return {'kdf': 'PBKDF2-SHA256', 'iter': 600000, 'salt': salt,
            'wrapped': {'iv': IV, 'ct': ct}, 'authKey': auth_key, 'setupCode': SETUP}


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / 'data'), setup_code=SETUP)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def owner(client):
    """A client whose vault already exists."""
    assert client.post('/api/vault', json=vault_body()).status_code == 200
    return client


@pytest.fixture
def store(app):
    return app.extensions['sealboard']['store']


def read_board(store) -> str:
    with open(store.board_path, encoding='utf-8') as f:
        return f.read()


def write_board(store, board: dict) -> None:
    with open(store.board_path, 'w', encoding='utf-8') as f:
        json.dump(board, f)
