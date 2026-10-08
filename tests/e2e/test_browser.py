"""Browser end-to-end: a real server in a thread + headless Chromium.

Run with ``pytest -m e2e`` after ``pip install -e '.[test]' && python -m playwright install chromium``.
"""
import json
import os
import re
import sys
import threading

import pytest

playwright_api = pytest.importorskip('playwright.sync_api')
from werkzeug.serving import make_server  # noqa: E402

from sealboard.app import create_app  # noqa: E402

expect = playwright_api.expect

pytestmark = pytest.mark.e2e
SETUP = 'e2e-setup-code-123'
P1, P2 = 'correct horse battery', 'staple battery horse'
BOLD = 'Meta+b' if sys.platform == 'darwin' else 'Control+b'

PASTE_IMAGE = """async () => {
  const c = document.createElement('canvas'); c.width = 320; c.height = 180;
  const g = c.getContext('2d'); g.fillStyle = '#3a7'; g.fillRect(0, 0, 320, 180);
  g.fillStyle = '#fff'; g.font = '28px sans-serif'; g.fillText('CAPTURE', 60, 100);
  const blob = await new Promise(r => c.toBlob(r, 'image/png'));
  const dt = new DataTransfer(); dt.items.add(new File([blob], 'cap.png', { type: 'image/png' }));
  const field = document.querySelector('.sb-rich'); field.focus();
  field.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
}"""


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    data = str(tmp_path_factory.mktemp('data'))
    srv = make_server('127.0.0.1', 0, create_app(data, setup_code=SETUP), threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_port}/', data
    srv.shutdown()


@pytest.fixture(scope='module')
def page(server):
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        pg = browser.new_context(viewport={'width': 1400, 'height': 860}).new_page()
        pg.errors = []
        pg.on('console', lambda m: pg.errors.append(m.text) if m.type == 'error' else None)
        pg.on('pageerror', lambda e: pg.errors.append(str(e)))
        pg.on('dialog', lambda d: d.accept())
        yield pg
        browser.close()


def board_file(data):
    with open(os.path.join(data, 'board.json'), encoding='utf-8') as f:
        return f.read()


def wait_saved(pg):
    expect(pg.locator('#saveState')).to_have_text('Saved')


def image_ready(pg):
    """The first note image has its decrypted blob: URL and decodes (Playwright waits without eval — CSP-safe)."""
    img = pg.locator('.sb-rich img').first
    expect(img).to_have_attribute('src', re.compile('^blob:'))
    img.evaluate('i => i.decode()')


def unlock(pg, pw):
    pg.fill('#pass', pw)
    pg.click('#lockSubmit')


def test_setup_needs_the_code_then_creates_vault(server, page):
    url, data = server
    page.goto(url)
    page.wait_for_selector('#lockTitle:has-text("Create")')
    page.fill('#setupCode', 'wrong-code')
    page.fill('#pass', P1)
    page.fill('#pass2', P1)
    page.click('#lockSubmit')
    expect(page.locator('#lockErr')).to_contain_text('setup code')
    page.goto(url + '#setup=' + SETUP)
    page.wait_for_selector('#lockTitle:has-text("Create")')
    assert page.input_value('#setupCode') == SETUP
    page.fill('#pass', P1)
    page.fill('#pass2', 'different passphrase')
    page.click('#lockSubmit')
    assert 'differ' in page.text_content('#lockErr')
    page.fill('#pass2', P1)
    page.click('#lockSubmit')
    page.wait_for_selector('#workspace:not(.hidden)')
    assert '#setup' not in page.url
    assert page.text_content('.sb-tab.is-active .sb-tab-name') == 'Notes'


def test_note_text_rich_and_image_are_ciphertext_on_disk(server, page):
    _, data = server
    page.click('#newNote')
    page.fill('.sb-note-title', 'Meeting notes')
    page.fill('.sb-text', 'secret alpha\nsecond line')
    wait_saved(page)
    assert not any(w in board_file(data) for w in ('secret', 'alpha', 'Meeting', 'Notes'))
    page.click('.sb-note [data-act="kind"]')
    page.wait_for_selector('.sb-rich')
    page.click('.sb-rich')
    page.keyboard.press('End')
    page.keyboard.press(BOLD)
    page.keyboard.type(' bold')
    page.evaluate(PASTE_IMAGE)
    image_ready(page)
    wait_saved(page)
    note = json.loads(board_file(data))['notes'][0]
    assert len(note['blobs']) == 1
    with open(os.path.join(data, 'blobs', note['blobs'][0] + '.bin'), 'rb') as f:
        blob = f.read()
    assert b'PNG' not in blob[:64] and b'WEBP' not in blob[:64]


def test_move_resize_colour_collapse_persist(server, page):
    _, data = server
    head = page.locator('.sb-note-head').bounding_box()
    page.mouse.move(head['x'] + 8, head['y'] + 10)
    page.mouse.down()
    page.mouse.move(head['x'] + 160, head['y'] + 110, steps=8)
    page.mouse.up()
    grip = page.locator('.sb-note-grip').bounding_box()
    page.mouse.move(grip['x'] + 8, grip['y'] + 8)
    page.mouse.down()
    page.mouse.move(grip['x'] + 128, grip['y'] + 88, steps=8)
    page.mouse.up()
    page.click('.sb-note [data-act="color"]')
    page.click('.sb-swatch.sb-c-blue')
    page.click('.sb-note [data-act="collapse"]')
    wait_saved(page)
    n = json.loads(board_file(data))['notes'][0]
    assert n['x'] > 150 and n['y'] > 90 and n['w'] >= 390 and n['h'] >= 320
    assert n['color'] == 'blue' and n['collapsed'] is True
    assert page.evaluate("[...document.querySelectorAll('.sb-note, .sb-note *')].every(e => !e.hasAttribute('style'))")


def test_tabs_and_search(server, page):
    _, data = server
    page.click('.sb-tab-add')
    expect(page.locator('.sb-tab')).to_have_count(2)
    page.dblclick('.sb-tab.is-active')
    page.fill('.sb-tab-input', 'To do')
    page.keyboard.press('Enter')
    page.wait_for_selector('.sb-tab.is-active .sb-tab-name:has-text("To do")')
    page.dblclick('#canvas', position={'x': 300, 'y': 200})
    page.fill('.sb-note-title', 'Groceries')
    page.fill('.sb-text', 'milk eggs')
    wait_saved(page)
    assert 'To do' not in board_file(data)
    page.fill('#q', 'alpha')
    expect(page.locator('.sb-tab:not(.is-active) .sb-tab-count')).to_have_text('1/1')
    page.press('#q', 'Enter')
    page.wait_for_selector('.sb-tab.is-active .sb-tab-name:has-text("Notes")')
    page.fill('#q', '')
    page.dispatch_event('#q', 'input')


def test_reload_unlock_lock_and_passphrase_change(server, page):
    url, data = server
    page.goto(url)
    page.wait_for_selector('#lockTitle:has-text("Unlock")')
    unlock(page, 'not the passphrase')
    expect(page.locator('#lockErr')).to_contain_text('Wrong passphrase', timeout=15000)
    unlock(page, P1)
    page.wait_for_selector('#workspace:not(.hidden)')
    page.click('.sb-tab:has-text("Notes")')
    page.wait_for_selector('.sb-note.is-collapsed')
    assert page.input_value('.sb-note-title') == 'Meeting notes'
    page.click('.sb-note [data-act="collapse"]')
    image_ready(page)
    html = page.inner_html('.sb-rich')
    assert 'alpha' in html and '<b>' in html and 'blob:' in html
    page.click('#lockBtn')
    page.wait_for_selector('#lock:not(.hidden)')
    assert 'alpha' not in page.content() and page.locator('.sb-note').count() == 0
    unlock(page, P1)
    page.wait_for_selector('#workspace:not(.hidden)')
    before = json.loads(board_file(data))
    page.click('#keyBtn')
    page.fill('#oldPass', 'not the passphrase')
    page.fill('#newPass', P2)
    page.fill('#newPass2', P2)
    page.click('#keySave')
    expect(page.locator('#keyErr')).to_contain_text('Current passphrase is wrong')
    assert json.loads(board_file(data)) == before                         # a wrong passphrase changes nothing
    page.fill('#oldPass', P1)
    page.click('#keySave')
    page.wait_for_selector('#keyDlg', state='hidden')
    image_ready(page)                                                     # same page keeps working on the new key
    after = json.loads(board_file(data))
    old_note = next(n for n in before['notes'] if n['blobs'])
    new_note = next(n for n in after['notes'] if n['id'] == old_note['id'])
    assert new_note['ct'] != old_note['ct'] and new_note['blobs'] != old_note['blobs']
    assert after['vault']['wrapped'] != before['vault']['wrapped'] and after['vault']['salt'] != before['vault']['salt']
    assert not os.path.exists(os.path.join(data, 'blobs', old_note['blobs'][0] + '.bin'))   # old-key image gone
    page.goto(url)
    page.wait_for_selector('#lockTitle:has-text("Unlock")')
    unlock(page, P1)
    expect(page.locator('#lockErr')).to_contain_text('Wrong passphrase', timeout=15000)
    unlock(page, P2)
    expect(page.locator('.sb-tab')).to_have_count(2)
    page.click('.sb-tab:has-text("Notes")')
    image_ready(page)
    assert 'alpha' in page.inner_html('.sb-rich')


def test_api_refuses_without_key_and_page_logs_no_errors(server, page):
    url, _ = server
    status = page.evaluate("fetch('/api/board').then(r => r.status)")
    assert status == 401
    # 401/403 resource logs come from the deliberate wrong-code / wrong-passphrase steps; anything else
    # (a CSP violation, a script error) is a real failure
    real = [e for e in page.errors if not e.startswith('Failed to load resource')]
    assert not real, real
