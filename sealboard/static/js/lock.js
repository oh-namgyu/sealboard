// Lock screen, first-time setup, passphrase change and auto-lock. The passphrase never leaves this page.
import { api, setToken } from './api.js';
import * as sc from './crypto.js';
import { $ } from './dom.js';

const MIN_PASS = 10;
const DEFAULT_IDLE_MIN = 15;
export const PREF = { tab: 'sealboard.tab', idleMin: 'sealboard.idleMin' };
const TEXT = {
  setup: {
    title: 'Create your vault', submit: 'Create', busy: 'Creating…', autocomplete: 'new-password',
    hint: 'Your notes are encrypted in this browser with this passphrase. The server never sees it, so if you forget it nobody can recover your notes.',
  },
  unlock: {
    title: '🔒 Unlock', submit: 'Unlock', busy: 'Unlocking…', autocomplete: 'current-password',
    hint: 'Enter your passphrase. Notes are decrypted only in this browser.',
  },
};

export function readPref(key, fallback) {
  try { const v = localStorage.getItem(key); return v === null ? fallback : v; } catch (e) { return fallback; }
}

export function writePref(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* private mode etc. — defaults still work */ }
}

const MESSAGES = {
  401: 'Wrong passphrase',
  403: 'Wrong setup code — use the link the server printed at start-up',
  409: 'A vault was created in another window — reload this page',
  429: 'Too many failed attempts — wait a few minutes',
};

// A wrong passphrase makes unwrapKey throw OperationError (GCM authentication failure).
const failMessage = (e, wrongPass) =>
  MESSAGES[e.status] || (e.name === 'OperationError' ? wrongPass : 'Failed: ' + e.message);

// New passphrase check → user-facing problem, or ''
const newPassError = (pass, again) =>
  (pass.length < MIN_PASS ? `Use at least ${MIN_PASS} characters` : pass !== again ? 'The two passphrases differ' : '');

const setupCodeFromUrl = () => new URLSearchParams(location.hash.slice(1)).get('setup') || '';

async function create(pass, code) {
  const { vault, authKey } = await sc.createVault(pass);
  await api.createVault(vault, authKey, code);
  setToken(authKey);
  history.replaceState(null, '', location.pathname);   // drop the one-time code from the address bar
  return api.board();
}

async function unlock(info, pass) {
  const { kek, authKey } = await sc.derive(pass, info);
  setToken(authKey);
  const board = await api.board();                      // 401 here = wrong passphrase
  await sc.openVault(board.vault.wrapped, kek);
  return board;
}

function render(setup, text) {
  $('lockTitle').textContent = text.title;
  $('lockHint').textContent = text.hint;
  ['pass2', 'setupCode'].forEach(id => { $(id).classList.toggle('hidden', !setup); $(id).required = setup; });
  $('pass').autocomplete = text.autocomplete;
  $('setupCode').value = setup ? setupCodeFromUrl() : '';
  $('pass').value = $('pass2').value = $('lockErr').textContent = '';
  $('lockSubmit').textContent = text.submit;
  $('workspace').classList.add('hidden');
  $('lock').classList.remove('hidden');
  $('pass').focus();
}

// info = GET /api/vault. On success onOpen(board) with the (still encrypted) board.
export function showLock(info, onOpen) {
  const setup = !info.exists, text = setup ? TEXT.setup : TEXT.unlock;
  const pass = $('pass'), err = $('lockErr'), submit = $('lockSubmit');
  render(setup, text);
  // the setup link may be opened in a tab already showing this page: only the #hash changes, no reload
  window.onhashchange = setup ? () => { $('setupCode').value = setupCodeFromUrl() || $('setupCode').value; } : null;
  $('lockForm').onsubmit = async e => {
    e.preventDefault();
    err.textContent = setup ? newPassError(pass.value, $('pass2').value) : '';
    if (err.textContent) return;
    submit.disabled = true;
    submit.textContent = text.busy;
    try {
      const board = setup ? await create(pass.value, $('setupCode').value.trim()) : await unlock(info, pass.value);
      pass.value = $('pass2').value = '';
      $('lock').classList.add('hidden');
      $('workspace').classList.remove('hidden');
      onOpen(board);
    } catch (ex) {
      setToken(null);
      sc.lock();
      err.textContent = failMessage(ex, 'Wrong passphrase');
    } finally {
      submit.disabled = false;
      submit.textContent = text.submit;
    }
  };
}

export function showLockError(msg) {
  $('lockTitle').textContent = 'Cannot open sealboard';
  $('lockHint').textContent = msg;
  $('lockForm').classList.add('is-disabled');
}

// Calls onIdle() N minutes after the last input. Returns { reset, pause(on) } — paused during a passphrase change.
export function idleLock(onIdle) {
  let timer = null, paused = false;
  const reset = () => {
    clearTimeout(timer);
    const minutes = Number(readPref(PREF.idleMin, DEFAULT_IDLE_MIN));
    if (!paused && minutes > 0 && sc.isUnlocked()) timer = setTimeout(onIdle, minutes * 60000);
  };
  ['pointerdown', 'keydown', 'wheel'].forEach(ev => document.addEventListener(ev, reset, { passive: true }));
  return { reset, pause: on => { paused = on; reset(); } };
}

// 🔑 dialog: auto-lock delay and (optionally) a new passphrase. A new passphrase also gets a new data key:
// changePassphrase(old, next, progress) re-encrypts everything (see rotate.js) and must not be interrupted.
export function initKeyDialog({ changePassphrase, toast, idle }) {
  const dlg = $('keyDlg'), err = $('keyErr'), status = $('keyStatus');
  const fields = ['oldPass', 'newPass', 'newPass2'].map($);
  const busy = on => {
    ['keySave', 'keyCancel'].forEach(id => { $(id).disabled = on; });
    dlg.dataset.busy = on ? '1' : '';
    idle.pause(on);
  };
  dlg.addEventListener('cancel', e => { if (dlg.dataset.busy) e.preventDefault(); });   // no Escape mid-rotation
  $('keyBtn').addEventListener('click', () => {
    fields.forEach(f => { f.value = ''; });
    err.textContent = status.textContent = '';
    $('idleMin').value = readPref(PREF.idleMin, String(DEFAULT_IDLE_MIN));
    dlg.showModal();
  });
  $('keyCancel').addEventListener('click', () => dlg.close());
  $('keySave').addEventListener('click', async () => {
    writePref(PREF.idleMin, $('idleMin').value);
    idle.reset();
    const [old, next, next2] = fields.map(f => f.value);
    if (!old && !next) { dlg.close(); toast('Auto-lock setting saved'); return; }
    err.textContent = newPassError(next, next2);
    if (err.textContent) return;
    busy(true);
    try {
      const r = await changePassphrase(old, next, msg => { status.textContent = msg; });
      dlg.close();
      const extra = [r.lost && `${r.lost} unreadable image(s) dropped`, r.dropped && `${r.dropped} unreadable note(s) removed`];
      const note = extra.filter(Boolean).join(', ');
      toast('Passphrase changed — everything was re-encrypted with a new key' + (note ? ` (${note})` : ''));
    } catch (ex) {
      err.textContent = ex.wrongPassphrase ? 'Current passphrase is wrong'
        : ex.status === 409 ? 'Notes changed in another window — try again' : 'Failed: ' + ex.message;
    } finally {
      busy(false);
      status.textContent = '';
    }
  });
}
