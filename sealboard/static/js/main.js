// sealboard page: unlock → decrypt tabs and notes → per-tab board. Server contract: sealboard/app.py
import { api, setToken, whenUnauthorized } from './api.js';
import { Board } from './board.js';
import * as sc from './crypto.js';
import { $ } from './dom.js';
import { htmlToText } from './editor.js';
import { initLightbox, revokeAll } from './images.js';
import { PREF, idleLock, initKeyDialog, readPref, showLock, showLockError, writePref } from './lock.js';
import { rotate } from './rotate.js';
import { Sync } from './sync.js';
import { Tabs } from './tabs.js';

const WORK_UI = ['q', 'newNote', 'keyBtn', 'lockBtn', 'saveState'];
const state = { vault: null, tabs: [], notes: new Map(), active: null, cursor: -1 };
const textCache = new Map();   // off-board note id → { title, body, text }: avoid re-parsing per search. Cleared on lock
let toastTimer = null;
let searchTimer = null;

function toast(msg, bad) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.toggle('is-bad', !!bad);
  t.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add('hidden'), 4500);
}

const board = new Board($('canvas'), {
  onLayout: note => sync.layout(note),
  onContent: note => { sync.content(note); if (query()) { clearTimeout(searchTimer); searchTimer = setTimeout(applySearch, 150); } },
  onDelete: note => removeNote(note),
  onNew: (x, y) => newNote(x, y),
  onError: msg => toast(msg, true),
  openImage: initLightbox($('lightbox')),
});
const sync = new Sync({
  snapshot: n => (board.has(n.id) ? board.content(n.id) : { title: n.title, kind: n.kind, body: n.body, blobs: n.blobs }),
  onStatus: (text, bad) => { $('saveState').textContent = text; $('saveState').classList.toggle('is-bad', bad); },
  onConflict,
  onError: msg => toast(msg, true),
});
const tabs = new Tabs($('tabs'), { onSelect: selectTab, onAdd: () => addTab(), onRename: renameTab, onDelete: deleteTab, onReorder: reorderTabs });
const resetIdle = idleLock(() => lockNow('Locked after inactivity'));

const query = () => $('q').value.trim().toLowerCase();
const notesOf = tabId => [...state.notes.values()].filter(n => n.tab === tabId);
const tabOf = id => state.tabs.find(t => t.id === id);

// ── unlock / lock ───────────────────────────────────────────────────────────

async function decryptAll(data) {
  let bad = 0;
  const open = async item => {
    const { iv, ct, ...meta } = item;
    try { return { ...meta, ...(await sc.openJSON(item.id, { iv, ct })) }; } catch (e) { bad++; return null; }
  };
  state.tabs = (await Promise.all(data.tabs.map(open))).filter(Boolean);
  const notes = (await Promise.all(data.notes.map(open))).filter(Boolean);
  state.notes = new Map(notes.map(n => [n.id, n]));
  if (bad) toast(`${bad} item(s) could not be decrypted (damaged, or from another vault)`, true);
}

async function enter(data) {
  state.vault = data.vault;
  await decryptAll(data);
  if (!state.tabs.length) await addTab('Notes');
  const saved = readPref(PREF.tab, '');
  state.active = tabOf(saved) ? saved : state.tabs[0].id;
  WORK_UI.forEach(id => $(id).classList.remove('hidden'));
  renderAll();
  resetIdle();
}

let locking = false;   // idle timer, the button and a revoked session may all fire at once

// revoked = the server no longer accepts our key (passphrase changed elsewhere): pending saves cannot succeed.
async function lockNow(reason, revoked = false) {
  if (locking || !sc.isUnlocked()) return;
  locking = true;
  if (!revoked) {
    try { await sync.flush(); } catch (e) { /* failures were already reported in the status line */ }
  }
  sync.reset();
  sc.lock();
  setToken(null);
  revokeAll();
  textCache.clear();
  board.clear();
  Object.assign(state, { tabs: [], notes: new Map(), active: null, cursor: -1 });
  $('tabs').replaceChildren();
  $('q').value = '';
  WORK_UI.forEach(id => $(id).classList.add('hidden'));
  locking = false;
  showLock(await api.vaultInfo(), enter);
  if (reason) toast(reason);
}

// ── render / search ─────────────────────────────────────────────────────────

function countByTab(list) {
  const out = new Map();
  list.forEach(n => out.set(n.tab, (out.get(n.tab) || 0) + 1));
  return out;
}

function renderTabs(found = matches()) {
  tabs.render(state.tabs, state.active, countByTab(state.notes), found && countByTab(found));
}

function showEmpty() {
  $('empty').classList.toggle('hidden', board.entries.size > 0);
}

function renderAll() {
  board.render(notesOf(state.active));
  showEmpty();
  applySearch();
}

// Lower-cased search text: live editor text for notes on the board, cached text for the rest.
function searchText(n) {
  if (board.has(n.id)) return board.text(n.id);
  const hit = textCache.get(n.id);
  if (hit && hit.title === n.title && hit.body === n.body) return hit.text;
  const text = (n.title + '\n' + (n.kind === 'rich' ? htmlToText(n.body || '') : n.body || '')).toLowerCase();
  textCache.set(n.id, { title: n.title, body: n.body, text });
  return text;
}

function matches() {
  const q = query();
  return q ? [...state.notes.values()].filter(n => searchText(n).includes(q)) : null;
}

function applySearch() {
  const m = matches();
  board.dim(m && new Set(m.map(n => n.id)));
  renderTabs(m);
}

// Enter = next match (tab order, then top→bottom, left→right)
function jumpToNext() {
  const m = matches();
  if (!m || !m.length) return;
  const order = new Map(state.tabs.map((t, i) => [t.id, i]));
  m.sort((a, b) => order.get(a.tab) - order.get(b.tab) || a.y - b.y || a.x - b.x);
  state.cursor = (state.cursor + 1) % m.length;
  const n = m[state.cursor];
  if (n.tab !== state.active) selectTab(n.tab);
  board.reveal(n.id);
}

// ── tabs ────────────────────────────────────────────────────────────────────

function selectTab(id) {
  if (id === state.active) return;
  sync.flush();                           // content is captured synchronously; no need to wait for the server
  state.active = id;
  writePref(PREF.tab, id);
  renderAll();
}

async function addTab(name) {
  const id = sc.newId('t');
  const label = name || `Tab ${state.tabs.length + 1}`;
  try {
    const r = await api.putTab(id, { ...(await sc.sealJSON(id, { name: label })), baseRev: 0 });
    state.tabs.push({ id, name: label, rev: r.rev, order: state.tabs.length });
  } catch (e) { toast('Could not create tab: ' + e.message, true); return; }
  if (state.active) selectTab(id); else state.active = id;
}

async function renameTab(id, name) {
  const t = tabOf(id);
  try {
    const r = await api.putTab(id, { ...(await sc.sealJSON(id, { name })), baseRev: t.rev });
    Object.assign(t, { name, rev: r.rev });
  } catch (e) { toast(e.status === 409 ? 'This tab changed in another window — reload' : 'Rename failed: ' + e.message, true); }
  renderTabs();
}

async function deleteTab(id) {
  const t = tabOf(id), count = notesOf(id).length;
  if (state.tabs.length === 1) { toast('The last tab cannot be deleted', true); return; }
  if (!confirm(`Delete tab "${t.name}" and its ${count} note(s)? This cannot be undone.`)) return;
  await sync.flush();
  try { await api.deleteTab(id); } catch (e) { toast('Could not delete tab: ' + e.message, true); return; }
  state.tabs = state.tabs.filter(x => x.id !== id);
  notesOf(id).forEach(n => state.notes.delete(n.id));
  state.active = state.tabs[0].id;
  writePref(PREF.tab, state.active);
  renderAll();
}

async function reorderTabs(ids) {
  try { await api.orderTabs(ids); } catch (e) { toast('Could not save tab order: ' + e.message, true); return; }
  state.tabs.sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id));
  renderTabs();
}

// ── notes ───────────────────────────────────────────────────────────────────

function newNote(x, y) {
  const view = $('board'), step = (notesOf(state.active).length % 8) * 24;
  const note = {
    id: sc.newId('n'), tab: state.active, w: 280, h: 240, z: board.nextZ(), collapsed: false, color: 'yellow',
    x: x ?? view.scrollLeft + 32 + step, y: y ?? view.scrollTop + 24 + step,
    rev: 0, blobs: [], title: '', kind: 'text', body: '',
  };
  state.notes.set(note.id, note);
  board.add(note);
  showEmpty();
  renderTabs();
  sync.create(note);
}

function removeNote(note) {
  board.remove(note.id);
  state.notes.delete(note.id);
  textCache.delete(note.id);
  showEmpty();
  renderTabs();
  sync.remove(note).catch(e => toast('Delete failed: ' + e.message, true));
}

// Another window saved first: show the server copy, keep this window's edit as a sibling "conflict copy".
async function onConflict(note, current, local) {
  const { iv, ct, ...meta } = current;
  let remote = { title: '', kind: 'text', body: '' };
  try { remote = await sc.openJSON(note.id, { iv, ct }); } catch (e) { /* unreadable: the copy keeps the text */ }
  Object.assign(note, meta, remote);
  if (note.tab === state.active) renderAll();
  if (remote.title === local.title && remote.body === local.body) return;
  const copy = { ...note, ...local, id: sc.newId('n'), x: note.x + 24, y: note.y + 24, z: board.nextZ(), rev: 0,
    title: '(conflict copy) ' + local.title };
  state.notes.set(copy.id, copy);
  if (copy.tab === state.active) board.add(copy);
  sync.create(copy);
  toast('This note changed in another window — your edit was kept as a copy next to it');
}

// New passphrase → new data key: everything is re-encrypted and swapped on the server, then this page switches
// to the new token and key. The modal dialog keeps the board untouched while this runs.
async function changePassphrase(oldPass, newPass, progress) {
  await sync.flush();
  const { next, notes } = await rotate({ vault: state.vault, tabs: state.tabs, notes: [...state.notes.values()],
    oldPass, newPass, progress });
  setToken(next.authKey);
  sc.adopt(next.key);
  state.vault = next.vault;
  state.tabs.forEach(t => { t.rev += 1; });
  notes.forEach(({ note, body, blobs }) => Object.assign(note, { body, blobs, rev: note.rev + 1 }));
  revokeAll();                            // cached image URLs belong to the old blob ids
  textCache.clear();
  renderAll();
}

// ── start ───────────────────────────────────────────────────────────────────

$('newNote').addEventListener('click', () => newNote());
$('lockBtn').addEventListener('click', () => lockNow());
$('q').addEventListener('input', () => { state.cursor = -1; applySearch(); });
$('q').addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); jumpToNext(); }
  if (e.key === 'Escape') { $('q').value = ''; applySearch(); }
});
window.addEventListener('beforeunload', e => { if (sync.pending()) { e.preventDefault(); e.returnValue = ''; } });
whenUnauthorized(() => lockNow('Signed out — the passphrase was changed in another window', true));
initKeyDialog({ changePassphrase, toast, resetIdle });

(async () => {
  if (!sc.supported) { showLockError('Browsers allow WebCrypto only on https:// or localhost. Open sealboard through HTTPS (see README).'); return; }
  try { showLock(await api.vaultInfo(), enter); } catch (e) { showLockError('Could not reach the server: ' + e.message); }
})();
