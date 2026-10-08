// The board: note DOM, move, resize, collapse, bring-to-front, colour. Positions and sizes are written as rules
// in a constructed stylesheet (adoptedStyleSheets) instead of style attributes, so the page needs no inline styles
// (and works under a strict Content-Security-Policy).
import { button } from './dom.js';
import { Editor } from './editor.js';

const COLORS = ['yellow', 'pink', 'green', 'blue', 'purple', 'gray'];
const colorName = c => c[0].toUpperCase() + c.slice(1);
const MIN_W = 160;
const MIN_H = 120;
const DRAG_SLOP = 4;
const sheet = new CSSStyleSheet();
document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
const rules = new Map();

function rule(selector) {
  if (!rules.has(selector)) {
    sheet.insertRule(selector + '{}', sheet.cssRules.length);
    rules.set(selector, sheet.cssRules[sheet.cssRules.length - 1]);
  }
  return rules.get(selector).style;
}

function dropRule(selector) {
  const r = rules.get(selector);
  if (!r) return;
  const i = Array.prototype.indexOf.call(sheet.cssRules, r);
  if (i >= 0) sheet.deleteRule(i);
  rules.delete(selector);
}

function place(note) {
  const s = rule(`.sb-note[data-id="${note.id}"]`);   // ids are [A-Za-z0-9_-] only (validated server-side)
  s.setProperty('left', note.x + 'px');
  s.setProperty('top', note.y + 'px');
  s.setProperty('width', note.w + 'px');
  s.setProperty('height', note.collapsed ? 'auto' : note.h + 'px');
  s.setProperty('z-index', String(note.z));
}

function trackPointer(e, onMove, onEnd) {
  const target = e.currentTarget;
  target.setPointerCapture(e.pointerId);
  const up = () => {
    target.removeEventListener('pointermove', onMove);
    target.removeEventListener('pointerup', up);
    target.removeEventListener('pointercancel', up);
    onEnd();
  };
  target.addEventListener('pointermove', onMove);
  target.addEventListener('pointerup', up);
  target.addEventListener('pointercancel', up);
}

// Fixed markup only — user content (title, body) goes in as values/nodes.
const HEAD = `
<header class="sb-note-head">
  <span class="sb-note-drag" aria-hidden="true">⠿</span>
  <input class="sb-note-title" placeholder="Untitled" maxlength="120" aria-label="Note title">
  <button type="button" class="sb-note-btn" data-act="kind" title="Rich text on/off" aria-label="Rich text on/off">Aa</button>
  <button type="button" class="sb-note-btn" data-act="color" title="Colour" aria-label="Colour">●</button>
  <button type="button" class="sb-note-btn" data-act="collapse"></button>
  <button type="button" class="sb-note-btn" data-act="delete" title="Delete note" aria-label="Delete note">✕</button>
</header>
<div class="sb-swatches hidden"></div>`;

// hooks: onLayout(note), onContent(note), onDelete(note), onNew(x, y), onError(msg), openImage(src)
export class Board {
  constructor(canvas, hooks) {
    this.canvas = canvas;
    this.hooks = hooks;
    this.entries = new Map();     // id → { note, el, ed }
    this.maxZ = 0;
    canvas.addEventListener('dblclick', e => {
      if (e.target !== canvas) return;
      const r = canvas.getBoundingClientRect();
      hooks.onNew(Math.max(0, Math.round(e.clientX - r.left)), Math.max(0, Math.round(e.clientY - r.top)));
    });
  }

  fit() {
    let w = 0, h = 0;
    this.entries.forEach(({ note }) => {
      w = Math.max(w, note.x + note.w);
      h = Math.max(h, note.y + (note.collapsed ? 40 : note.h));
    });
    const s = rule('.sb-canvas');
    s.setProperty('width', (w + 400) + 'px');
    s.setProperty('height', (h + 300) + 'px');
  }

  decorate({ note, el }) {
    el.classList.remove(...COLORS.map(c => 'sb-c-' + c));
    el.classList.add('sb-c-' + note.color);
    el.classList.toggle('is-collapsed', note.collapsed);
    el.classList.toggle('is-rich', note.kind === 'rich');
    const btn = el.querySelector('[data-act="collapse"]');
    btn.textContent = note.collapsed ? '▸' : '▾';
    btn.title = note.collapsed ? 'Expand' : 'Collapse';
    btn.setAttribute('aria-label', btn.title);
    el.querySelector('[data-act="kind"]').classList.toggle('is-on', note.kind === 'rich');
    place(note);
  }

  raise(note) {
    if (note.z >= this.maxZ) return false;
    note.z = ++this.maxZ;
    place(note);
    return true;
  }

  // Dragging the header moves the note. Starting on the title still moves once past DRAG_SLOP (a click edits the title).
  dragMove({ note, el }, e) {
    const sx = e.clientX, sy = e.clientY, ox = note.x, oy = note.y;
    let moving = false;
    trackPointer(e, ev => {
      const dx = ev.clientX - sx, dy = ev.clientY - sy;
      if (!moving && Math.hypot(dx, dy) < DRAG_SLOP) return;
      if (!moving) {
        moving = true;
        el.classList.add('is-dragging');
        if (document.activeElement) document.activeElement.blur();
        getSelection().removeAllRanges();
      }
      note.x = Math.max(0, Math.round(ox + dx));
      note.y = Math.max(0, Math.round(oy + dy));
      place(note);
    }, () => {
      el.classList.remove('is-dragging');
      if (moving) { this.fit(); this.hooks.onLayout(note); }
    });
  }

  dragResize({ note }, e) {
    const sx = e.clientX, sy = e.clientY, ow = note.w, oh = note.h;
    e.preventDefault();
    trackPointer(e, ev => {
      note.w = Math.max(MIN_W, Math.round(ow + ev.clientX - sx));
      note.h = Math.max(MIN_H, Math.round(oh + ev.clientY - sy));
      place(note);
    }, () => { this.fit(); this.hooks.onLayout(note); });
  }

  act(entry, action) {
    const { note, el, ed } = entry;
    if (action === 'collapse') {
      note.collapsed = !note.collapsed;
      this.decorate(entry);
      this.fit();
      this.hooks.onLayout(note);
    } else if (action === 'color') {
      el.querySelector('.sb-swatches').classList.toggle('hidden');
    } else if (action === 'kind') {
      ed.setKind(note.kind === 'rich' ? 'text' : 'rich');
    } else if (action === 'delete' && confirm('Delete this note? This cannot be undone.')) {
      this.hooks.onDelete(note);
    }
  }

  buildSwatches(entry) {
    const box = entry.el.querySelector('.sb-swatches');
    COLORS.forEach(c => {
      const b = button('', colorName(c), { className: 'sb-swatch sb-c-' + c });
      b.addEventListener('click', () => {
        entry.note.color = c;
        box.classList.add('hidden');
        this.decorate(entry);
        this.hooks.onLayout(entry.note);
      });
      box.appendChild(b);
    });
  }

  wire(entry) {
    const { note, el } = entry;
    const head = el.querySelector('.sb-note-head');
    const title = el.querySelector('.sb-note-title');
    const grip = el.querySelector('.sb-note-grip');
    title.value = note.title || '';
    title.addEventListener('input', () => { note.title = title.value; this.hooks.onContent(note); });
    el.addEventListener('pointerdown', () => { if (this.raise(note)) this.hooks.onLayout(note); });
    head.addEventListener('pointerdown', e => { if (e.button === 0 && !e.target.closest('button')) this.dragMove(entry, e); });
    head.addEventListener('click', e => { const b = e.target.closest('button[data-act]'); if (b) this.act(entry, b.dataset.act); });
    grip.addEventListener('pointerdown', e => { if (e.button === 0) this.dragResize(entry, e); });
  }

  build(note) {
    const el = document.createElement('article');
    el.className = 'sb-note';
    el.dataset.id = note.id;
    el.innerHTML = HEAD;
    const entry = { note, el, ed: null };
    entry.ed = new Editor(note, {
      onChange: () => this.hooks.onContent(note),
      onKind: () => this.decorate(entry),
      onError: this.hooks.onError,
      openImage: this.hooks.openImage,
    });
    const grip = document.createElement('div');
    grip.className = 'sb-note-grip';
    grip.title = 'Drag to resize';
    el.append(entry.ed.toolbar, entry.ed.body, entry.ed.picker, grip);
    this.buildSwatches(entry);
    this.wire(entry);
    this.entries.set(note.id, entry);
    this.maxZ = Math.max(this.maxZ, note.z);
    this.decorate(entry);
    return entry;
  }

  render(notes) {
    this.entries.clear();
    sheet.replaceSync('');            // drop placement rules of the previous tab's notes
    rules.clear();
    this.canvas.replaceChildren(...notes.map(n => this.build(n).el));
    this.fit();
  }

  add(note) {
    const entry = this.build(note);
    this.canvas.appendChild(entry.el);
    this.fit();
    entry.ed.focus();
  }

  remove(id) {
    const entry = this.entries.get(id);
    if (!entry) return;
    entry.el.remove();
    this.entries.delete(id);
    dropRule(`.sb-note[data-id="${id}"]`);
    this.fit();
  }

  has(id) { return this.entries.has(id); }

  // Lower-cased text for search, read from the live editor (no save-time serialization)
  text(id) {
    const { note, ed } = this.entries.get(id);
    return ((note.title || '') + '\n' + ed.text()).toLowerCase();
  }

  nextZ() { return ++this.maxZ; }

  content(id) {
    const { note, ed } = this.entries.get(id);
    return { title: note.title || '', kind: note.kind, body: ed.getBody(), blobs: ed.getBlobs() };
  }

  dim(matchIds) {
    this.entries.forEach(({ el }, id) => el.classList.toggle('is-dim', !!matchIds && !matchIds.has(id)));
  }

  reveal(id) {
    const entry = this.entries.get(id);
    if (!entry) return;
    this.raise(entry.note);
    entry.el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
    entry.el.classList.remove('is-flash');
    void entry.el.offsetWidth;                            // restart the animation
    entry.el.classList.add('is-flash');
  }

  clear() {
    this.entries.clear();
    this.canvas.replaceChildren();
    sheet.replaceSync('');
    rules.clear();
    this.maxZ = 0;
  }
}
