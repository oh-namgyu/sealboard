"""Board API: only ciphertext and layout reach disk, revisions prevent silent overwrites, images are cleaned up."""
import json
import os

import pytest
from conftest import CT, IV, bearer, read_board

from sealboard import store as st

TAB, NOTE, BLOB = 't_abcdefghij', 'n_abcdefghij', 'b_abcdefghij'


@pytest.fixture
def api(owner):
    def call(method, url, body=None, data=None):
        kwargs = {'headers': bearer()}
        if data is not None:
            kwargs['data'] = data
        elif body is not None:
            kwargs['json'] = body
        return getattr(owner, method)(url, **kwargs)
    return call


def _tab(api, tid=TAB):
    assert api('put', f'/api/tabs/{tid}', {'iv': IV, 'ct': CT, 'baseRev': 0}).status_code == 200


def _note(api, base=0, **extra):
    return api('put', f'/api/notes/{NOTE}', dict({'tab': TAB, 'iv': IV, 'ct': CT, 'baseRev': base}, **extra))


def test_note_needs_existing_tab_and_valid_cipher(api):
    assert _note(api).status_code == 400
    _tab(api)
    assert _note(api, ct='plain text').status_code == 400
    assert api('put', '/api/notes/x', {'tab': TAB}).status_code == 400
    assert api('put', '/api/notes/n_abcdefghij%0A', {'tab': TAB}).status_code == 400   # no trailing-newline ids
    assert _note(api).get_json() == {'ok': True, 'rev': 1}


def test_stale_write_is_a_conflict_not_an_overwrite(api):
    _tab(api)
    _note(api)
    r = _note(api, base=0)
    assert r.status_code == 409 and r.get_json()['current']['rev'] == 1
    assert _note(api, base=1).get_json()['rev'] == 2


def test_only_ciphertext_and_layout_reach_disk(api, store):
    _tab(api)
    _note(api, title='secret title', body='secret body', x=10)
    raw = read_board(store)
    assert 'secret' not in raw
    note = json.loads(raw)['notes'][0]
    assert set(note) == {'id', 'tab', 'x', 'y', 'w', 'h', 'z', 'collapsed', 'color', 'iv', 'ct', 'blobs', 'rev', 'updatedAt'}


def test_layout_patch_clamps_and_validates(api):
    _tab(api)
    _note(api)
    assert api('patch', f'/api/notes/{NOTE}', {'x': -50, 'w': 99999, 'collapsed': 1}).status_code == 200
    note = api('get', '/api/board').get_json()['notes'][0]
    assert (note['x'], note['w'], note['collapsed'], note['rev']) == (0, 4000, True, 1)   # layout does not bump rev
    assert api('patch', f'/api/notes/{NOTE}', {'color': 'red'}).status_code == 400
    assert api('patch', f'/api/notes/{NOTE}', {'tab': 't_zzzzzzzzzz'}).status_code == 400


def test_blob_roundtrip_limits_and_immutability(api, monkeypatch):
    data = os.urandom(64)
    assert api('put', f'/api/blobs/{BLOB}', data=data).status_code == 200
    assert api('put', f'/api/blobs/{BLOB}', data=data).status_code == 409
    got = api('get', f'/api/blobs/{BLOB}')
    assert got.data == data and 'immutable' in got.headers['Cache-Control']
    assert api('get', '/api/blobs/b_missingmiss').status_code == 404
    assert api('put', '/api/blobs/b_tinytinyti', data=b'x' * 10).status_code == 400
    monkeypatch.setattr(st, 'MAX_BLOB', 100)
    assert api('put', '/api/blobs/b_hugehugehu', data=b'x' * 101).status_code == 413


def test_orphan_blob_survives_grace_then_goes(api, store, monkeypatch):
    _tab(api)
    keep, new = 'b_keepkeepke', 'b_newnewnewn'
    for bid in (BLOB, keep, new):
        api('put', f'/api/blobs/{bid}', data=os.urandom(64))
    _note(api, blobs=[keep])                       # image list changed → sweep: BLOB only marked
    assert os.path.exists(store.blob_path(BLOB))
    monkeypatch.setattr(st, 'ORPHAN_GRACE_SEC', -1)
    _note(api, base=1, blobs=[keep])               # same list (typing autosave) → no sweep
    assert os.path.exists(store.blob_path(BLOB))
    _note(api, base=2, blobs=[keep, new])          # list changed + grace passed → deleted, referenced ones kept
    assert not os.path.exists(store.blob_path(BLOB))
    assert all(os.path.exists(store.blob_path(b)) for b in (keep, new))


def test_tab_order_and_delete_cascades_notes(api):
    _tab(api)
    t2 = 't_secondtab1'
    _tab(api, t2)
    _note(api)
    assert api('post', '/api/tabs/order', {'ids': [TAB]}).status_code == 409
    assert api('post', '/api/tabs/order', {'ids': [t2, TAB]}).status_code == 200
    assert [t['id'] for t in api('get', '/api/board').get_json()['tabs']] == [t2, TAB]
    assert api('delete', f'/api/tabs/{TAB}').get_json() == {'ok': True, 'deletedNotes': 1}
    assert api('get', '/api/board').get_json()['notes'] == []


def test_failed_mutation_leaves_no_half_applied_state(api, store):
    _tab(api)
    _note(api)
    assert api('post', '/api/tabs/order', {'ids': ['t_nonexisten']}).status_code == 409
    assert _note(api, base=1).get_json()['rev'] == 2   # cache dropped and re-read cleanly after the refusal
