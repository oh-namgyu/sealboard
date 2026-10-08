// Tab bar: switch, add, rename (double-click or F2), delete, drag to reorder, note-count / search-hit badges.
// hooks: onSelect(id), onAdd(), onRename(id, name), onDelete(id), onReorder(ids)
import { button } from './dom.js';

export class Tabs {
  constructor(bar, hooks) {
    this.bar = bar;
    this.hooks = hooks;
    this.dragId = null;
  }

  // tabs: [{id, name}] in order, counts: Map id→note count, hits: Map id→search hits | null (no search)
  render(tabs, activeId, counts, hits) {
    const nodes = tabs.map(t => this.tab(t, t.id === activeId, counts.get(t.id) || 0, hits ? hits.get(t.id) || 0 : null));
    const add = button('+ Tab', 'New tab', { className: 'sb-tab-add' });
    add.addEventListener('click', () => this.hooks.onAdd());
    this.bar.replaceChildren(...nodes, add);
  }

  tab(t, active, count, hit) {
    const el = document.createElement('div');
    el.className = 'sb-tab' + (active ? ' is-active' : '') + (hit === 0 ? ' is-dim' : '');
    el.dataset.id = t.id;
    el.draggable = true;
    el.tabIndex = 0;
    el.setAttribute('role', 'tab');
    el.setAttribute('aria-selected', String(active));
    el.title = 'Click to open · double-click to rename · drag to reorder';
    const name = document.createElement('span');
    name.className = 'sb-tab-name';
    name.textContent = t.name;
    const badge = document.createElement('span');
    badge.className = 'sb-tab-count' + (hit ? ' is-hit' : '');
    badge.textContent = hit === null ? String(count) : `${hit}/${count}`;
    el.append(name, badge);
    if (active) el.appendChild(this.deleteButton(t));
    this.wire(el, t, name);
    return el;
  }

  deleteButton(t) {
    const del = button('✕', `Delete tab "${t.name}"`, { className: 'sb-tab-del' });
    del.addEventListener('click', e => { e.stopPropagation(); this.hooks.onDelete(t.id); });
    return del;
  }

  wire(el, t, name) {
    el.addEventListener('click', () => this.hooks.onSelect(t.id));
    el.addEventListener('keydown', e => { if (e.key === 'Enter') this.hooks.onSelect(t.id); if (e.key === 'F2') this.rename(t, name); });
    el.addEventListener('dblclick', () => this.rename(t, name));
    el.addEventListener('dragstart', e => { this.dragId = t.id; e.dataTransfer.effectAllowed = 'move'; });
    el.addEventListener('dragover', e => { if (this.dragId && this.dragId !== t.id) { e.preventDefault(); el.classList.add('is-drop'); } });
    el.addEventListener('dragleave', () => el.classList.remove('is-drop'));
    el.addEventListener('drop', e => { e.preventDefault(); el.classList.remove('is-drop'); this.drop(t.id); });
    el.addEventListener('dragend', () => { this.dragId = null; });
  }

  drop(targetId) {
    const ids = [...this.bar.querySelectorAll('.sb-tab')].map(n => n.dataset.id);
    const from = ids.indexOf(this.dragId);
    if (from < 0) return;
    ids.splice(from, 1);
    ids.splice(ids.indexOf(targetId) + (from <= ids.indexOf(targetId) ? 1 : 0), 0, this.dragId);
    this.dragId = null;
    this.hooks.onReorder(ids);
  }

  rename(t, name) {
    const input = document.createElement('input');
    Object.assign(input, { className: 'sb-tab-input', value: t.name, maxLength: 60 });
    input.setAttribute('aria-label', 'Tab name');
    let done = false;
    const finish = save => {
      if (done) return;
      done = true;
      const v = input.value.trim();
      input.replaceWith(name);
      if (save && v && v !== t.name) this.hooks.onRename(t.id, v);
    };
    input.addEventListener('keydown', e => { if (e.key === 'Enter') finish(true); if (e.key === 'Escape') finish(false); e.stopPropagation(); });
    input.addEventListener('blur', () => finish(true));
    input.addEventListener('click', e => e.stopPropagation());
    name.replaceWith(input);
    input.focus();
    input.select();
  }
}
