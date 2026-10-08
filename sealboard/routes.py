"""HTTP endpoints. All are owner-only except the page and the two vault endpoints listed in web.PUBLIC_ENDPOINTS."""
from __future__ import annotations

import json
import os
from typing import Any

from flask import Response, current_app, jsonify, request, send_from_directory

from sealboard import store as st
from sealboard import web
from sealboard.auth import setup_code_matches


@web.bp.route('/')
def index() -> Any:
    return current_app.send_static_file('index.html')


@web.bp.route('/api/vault')
def vault_info() -> Any:
    """Enough for the browser to derive keys — never the wrapped key (that needs auth, see /api/board)."""
    def view(board: dict) -> dict:
        v = board.get('vault')
        return {'exists': True, 'kdf': v['kdf'], 'iter': v['iter'], 'salt': v['salt']} if v else {'exists': False}
    return jsonify(web.store().read(view))


@web.bp.route('/api/vault', methods=['POST'])
def create_vault() -> Any:
    """First-time setup: KDF params + wrapped data key + auth key, gated by the one-time setup code."""
    config = current_app.config
    if web.store().read(lambda b: bool(b.get('vault'))):
        raise st.Reject(409, 'vault exists')
    if web.limiter().blocked(web.client()):
        return web.too_many()
    body = web.body()
    if not setup_code_matches(body.get('setupCode'), config['SEALBOARD_SETUP_CODE']):
        web.limiter().fail(web.client())
        raise st.Reject(403, 'bad setup code')
    record = web.vault_record(body, body.get('authKey'))

    def mutate(board: dict) -> None:
        if board.get('vault'):
            raise st.Reject(409, 'vault exists')
        board['vault'] = record
    web.store().update(mutate)
    config['SEALBOARD_SETUP_CODE'] = None
    return jsonify({'ok': True})


def _rotated(items: Any, kind: str) -> dict:
    """Validate the re-encrypted copies sent with a key rotation → id → {sealed, baseRev, blobs}."""
    if not isinstance(items, list):
        raise st.Reject(400, 'bad ' + kind)
    out = {}
    for item in items:
        if not isinstance(item, dict):
            raise st.Reject(400, 'bad ' + kind)
        iid = st.check_id(item.get('id'), kind[0])
        out[iid] = {'sealed': st.sealed(item), 'baseRev': item.get('baseRev'),
                    'blobs': st.blob_ids(item) if kind == 'notes' else None}
    return out


def _swap(items: list, incoming: dict, drop: frozenset = frozenset()) -> list:
    """Replace every item's ciphertext — all or nothing: the sets must match exactly and no item may have
    moved on. ``drop`` = ids the owner's browser could not decrypt (unreadable anyway); they are removed."""
    kept = [c for c in items if c['id'] not in drop]
    have = {c['id'] for c in kept}
    if have != set(incoming) or not drop <= {c['id'] for c in items}:
        raise st.Reject(409, 'board changed', missing=sorted(have - set(incoming)), extra=sorted(set(incoming) - have))
    for cur in kept:
        new = incoming[cur['id']]
        if new['baseRev'] != cur['rev']:
            raise st.Reject(409, 'conflict', current=cur)
        cur.update(new['sealed'], rev=cur['rev'] + 1, updatedAt=web.now_iso())
        if new['blobs'] is not None:
            cur['blobs'] = new['blobs']
    return kept


def _drop_ids(value: Any) -> frozenset:
    if value is None:
        return frozenset()
    if not isinstance(value, list):
        raise st.Reject(400, 'bad drop')
    return frozenset(st.check_id(v, 'n') for v in value)


@web.bp.route('/api/vault/rotate', methods=['POST'])
def rotate_vault() -> Any:
    """Passphrase change with a brand-new data key. Every tab and note arrives re-encrypted (images were uploaded
    under new ids beforehand) and is swapped in one atomic save; images under the old key are deleted at once,
    after the new board is on disk. Notes the browser could not decrypt are listed in ``drop`` and removed."""
    body = web.body()
    record = web.vault_record(body.get('vault'), body.get('authKey'))
    tabs, notes = _rotated(body.get('tabs'), 'tabs'), _rotated(body.get('notes'), 'notes')
    drop = _drop_ids(body.get('drop'))
    store = web.store()

    def mutate(board: dict) -> None:
        board['tabs'] = _swap(board['tabs'], tabs)
        board['notes'] = _swap(board['notes'], notes, drop)
        board['vault'] = record
        store.sweep_blobs(board, immediate=True)
    web.update(mutate)
    return jsonify({'ok': True, 'dropped': len(drop)})


@web.bp.route('/api/board')
def get_board() -> Any:
    def view(board: dict) -> str:   # serialize under the lock: the board may change right after
        vault = {k: v for k, v in board['vault'].items() if k != 'authHash'}
        return json.dumps({'version': board['version'], 'vault': vault, 'tabs': board['tabs'], 'notes': board['notes']},
                          ensure_ascii=False, separators=(',', ':'))
    return Response(web.store().read(view), mimetype='application/json')


@web.bp.route('/api/tabs/order', methods=['POST'])
def order_tabs() -> Any:
    ids = web.body().get('ids')
    if not isinstance(ids, list):
        raise st.Reject(400, 'ids required')
    ids = [st.check_id(i, 't') for i in ids]

    def mutate(board: dict) -> None:
        if sorted(ids) != sorted(t['id'] for t in board['tabs']):
            raise st.Reject(409, 'tab set changed')
        for i, tid in enumerate(ids):
            st.find(board['tabs'], tid)['order'] = i
        board['tabs'].sort(key=lambda t: t['order'])
    web.update(mutate)
    return jsonify({'ok': True})


@web.bp.route('/api/tabs/<tid>', methods=['PUT'])
def put_tab(tid: str) -> Any:
    """Create or rename a tab — the name is ciphertext (iv, ct)."""
    st.check_id(tid, 't')
    body = web.body()
    sealed = st.sealed(body)

    def mutate(board: dict) -> int:
        cur = st.find(board['tabs'], tid)
        rev = st.check_rev(cur, body)
        if cur is None:
            cur = {'id': tid, 'order': len(board['tabs'])}
            board['tabs'].append(cur)
        cur.update(sealed, rev=rev, updatedAt=web.now_iso())
        return rev
    return jsonify({'ok': True, 'rev': web.update(mutate)})


@web.bp.route('/api/tabs/<tid>', methods=['DELETE'])
def delete_tab(tid: str) -> Any:
    """Delete a tab and every note in it (their images are cleaned up after the grace period)."""
    st.check_id(tid, 't')
    store = web.store()

    def mutate(board: dict) -> int:
        if not st.find(board['tabs'], tid):
            raise st.Reject(404, 'no tab')
        board['tabs'] = [t for t in board['tabs'] if t['id'] != tid]
        before = len(board['notes'])
        board['notes'] = [n for n in board['notes'] if n['tab'] != tid]
        store.sweep_blobs(board)
        return before - len(board['notes'])
    return jsonify({'ok': True, 'deletedNotes': web.update(mutate)})


@web.bp.route('/api/notes/<nid>', methods=['PUT'])
def put_note(nid: str) -> Any:
    """Save content (creating the note if new): ciphertext + image list + optional layout. Revision-checked."""
    st.check_id(nid, 'n')
    body = web.body()
    sealed, layout, blobs = st.sealed(body), st.layout(body), st.blob_ids(body)
    store = web.store()

    def mutate(board: dict) -> int:
        cur = st.find(board['notes'], nid)
        rev = st.check_rev(cur, body)
        tab = layout.get('tab') or (cur or {}).get('tab')
        if not tab or not st.find(board['tabs'], tab):
            raise st.Reject(400, 'unknown tab')
        if cur is None:
            cur = {'id': nid, 'x': 40, 'y': 40, 'w': 280, 'h': 240, 'z': 1, 'collapsed': False, 'color': 'yellow'}
            board['notes'].append(cur)
        changed = cur.get('blobs') != blobs
        cur.update(layout, **sealed, blobs=blobs, rev=rev, updatedAt=web.now_iso())
        if changed:
            store.sweep_blobs(board)
        return rev
    return jsonify({'ok': True, 'rev': web.update(mutate)})


@web.bp.route('/api/notes/<nid>', methods=['PATCH'])
def patch_note(nid: str) -> Any:
    """Layout only (move, resize, collapse, colour, change tab) — not revision-checked; last write wins."""
    st.check_id(nid, 'n')
    layout = st.layout(web.body())

    def mutate(board: dict) -> None:
        cur = st.find(board['notes'], nid)
        if cur is None:
            raise st.Reject(404, 'no note')
        if 'tab' in layout and not st.find(board['tabs'], layout['tab']):
            raise st.Reject(400, 'unknown tab')
        cur.update(layout)
    web.update(mutate)
    return jsonify({'ok': True})


@web.bp.route('/api/notes/<nid>', methods=['DELETE'])
def delete_note(nid: str) -> Any:
    st.check_id(nid, 'n')
    store = web.store()

    def mutate(board: dict) -> None:
        if not st.find(board['notes'], nid):
            raise st.Reject(404, 'no note')
        board['notes'] = [n for n in board['notes'] if n['id'] != nid]
        store.sweep_blobs(board)
    web.update(mutate)
    return jsonify({'ok': True})


@web.bp.route('/api/blobs/<bid>', methods=['PUT'])
def put_blob(bid: str) -> Any:
    """Encrypted image bytes ([12-byte iv][ciphertext+tag]). Immutable: re-uploading an id is a 409."""
    st.check_id(bid, 'b')
    data = request.stream.read(st.MAX_BLOB + 1)
    if len(data) > st.MAX_BLOB:
        raise st.Reject(413, 'blob too large')
    if len(data) < 12 + 16:
        raise st.Reject(400, 'blob too small')
    web.still_owner()
    path = web.store().blob_path(bid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, 'xb') as f:
            f.write(data)
    except FileExistsError:
        raise st.Reject(409, 'blob exists')
    return jsonify({'ok': True, 'size': len(data)})


@web.bp.route('/api/blobs/<bid>')
def get_blob(bid: str) -> Any:
    """Stream an encrypted image. Blobs never change, so the browser may cache them (they are ciphertext)."""
    st.check_id(bid, 'b')
    store = web.store()
    if not os.path.isfile(store.blob_path(bid)):
        raise st.Reject(404, 'no blob')
    resp = send_from_directory(store.blob_dir, bid + '.bin', mimetype='application/octet-stream')
    resp.headers['Cache-Control'] = 'private, max-age=31536000, immutable'
    return resp


bp = web.bp   # the blueprint, now with every endpoint above registered (create_app imports it from here)
