// Saving: one serial queue per note (keeps revisions in order) plus debouncing. Content is captured synchronously
// when a timer fires and written onto the note object, so switching tabs (which re-renders the board) never loses
// edits and never has to wait for the server. Conflicts (409) go to hooks.onConflict.
import { api, ApiError } from './api.js';
import { sealJSON } from './crypto.js';

const CONTENT_DELAY = 700;
const LAYOUT_DELAY = 400;
const RETRY_DELAY = 10000;

const layoutOf = n => ({ tab: n.tab, x: n.x, y: n.y, w: n.w, h: n.h, z: n.z, collapsed: n.collapsed, color: n.color });

// hooks: snapshot(note) → {title, kind, body, blobs}, onStatus(text, bad), onConflict(note, current, local), onError(msg)
export class Sync {
  constructor(hooks) {
    this.hooks = hooks;
    this.timers = new Map();     // 'c:<id>' (content) | 'l:<id>' (layout) → { timer, fire }
    this.chains = new Map();     // note id → its last queued job
    this.inflight = 0;
    this.failed = false;
  }

  busy() {
    return this.timers.size + this.inflight > 0;
  }

  status() {
    this.hooks.onStatus(this.failed ? '⚠ Save failed — retrying' : this.busy() ? 'Saving…' : 'Saved', this.failed);
  }

  queue(id, job) {
    this.inflight++;
    this.status();
    const next = (this.chains.get(id) || Promise.resolve()).then(job).finally(() => {
      this.inflight--;
      if (this.chains.get(id) === next) this.chains.delete(id);
      this.status();
    });
    this.chains.set(id, next.catch(() => {}));
    return next;
  }

  cancel(key) {
    const t = this.timers.get(key);
    if (t) clearTimeout(t.timer);
    this.timers.delete(key);
  }

  later(key, delay, prepare) {
    this.cancel(key);
    const fire = () => { this.timers.delete(key); return prepare(); };
    this.timers.set(key, { timer: setTimeout(fire, delay), fire });
    this.status();
  }

  capture(note) {
    const snap = this.hooks.snapshot(note);
    Object.assign(note, snap);
    return snap;
  }

  content(note, delay = CONTENT_DELAY) {
    this.later('c:' + note.id, delay, () => this.create(note));
  }

  layout(note) {
    this.later('l:' + note.id, LAYOUT_DELAY, () =>
      this.queue(note.id, () => api.patchNote(note.id, layoutOf(note))).catch(e => this.hooks.onError('Could not save layout: ' + e.message)));
  }

  // Save content (including a new note's first PUT). Same queue, so a later PATCH never races ahead of the create.
  create(note) {
    const snap = this.capture(note);
    return this.queue(note.id, () => this.saveContent(note, snap)).catch(e => this.retry(note, e));
  }

  async saveContent(note, snap) {
    const sealed = await sealJSON(note.id, { title: snap.title, kind: snap.kind, body: snap.body });
    try {
      const r = await api.putNote(note.id, { ...sealed, ...layoutOf(note), blobs: snap.blobs, baseRev: note.rev });
      note.rev = r.rev;
      this.failed = false;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.body.current) return this.hooks.onConflict(note, e.body.current, snap);
      throw e;
    }
  }

  retry(note, e) {
    this.failed = true;
    this.hooks.onError('Save failed: ' + e.message);
    this.content(note, RETRY_DELAY);
  }

  remove(note) {
    this.cancel('c:' + note.id);
    this.cancel('l:' + note.id);
    return this.queue(note.id, () => (note.rev ? api.deleteNote(note.id) : null));
  }

  // Send pending saves now (content is captured synchronously). The promise resolves when all are done —
  // await it only when the server must have finished (locking, deleting a tab).
  flush() {
    [...this.timers.values()].forEach(t => { clearTimeout(t.timer); t.fire(); });
    this.timers.clear();
    return Promise.all([...this.chains.values()]);
  }

  pending() {
    return this.failed || this.busy();
  }

  // After locking there is no key, so drop leftover retry timers (call after flush).
  reset() {
    this.timers.forEach(t => clearTimeout(t.timer));
    this.timers.clear();
    this.failed = false;
    this.status();
  }
}
