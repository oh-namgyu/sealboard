// Note body editor: plain text (textarea) ⇄ rich text (contenteditable). Images live in rich notes.
// Everything rendered or saved goes through a DOMPurify allowlist (drops external image URLs, event
// handler attributes and any tag not listed below).
import { button } from './dom.js';
import { captureScreen, hydrate, imageFiles, upload } from './images.js';

const PURIFY = {
  ALLOWED_TAGS: ['b', 'strong', 'i', 'em', 'u', 's', 'strike', 'del', 'mark', 'br', 'p', 'div', 'span', 'h3', 'h4',
    'ul', 'ol', 'li', 'blockquote', 'pre', 'code', 'a', 'img', 'hr'],
  ALLOWED_ATTR: ['href', 'data-blob', 'alt'],
  ALLOW_DATA_ATTR: false,
  RETURN_DOM_FRAGMENT: true,
};
const BLOB_ID = /^b_[A-Za-z0-9_-]{10,40}$/;   // same shape the server accepts
const BLOCKS = new Set(['P', 'DIV', 'LI', 'H3', 'H4', 'PRE', 'BLOCKQUOTE', 'UL', 'OL']);
const TOOLS = [
  ['bold', 'B', 'Bold (⌘/Ctrl+B)'], ['italic', 'I', 'Italic (⌘/Ctrl+I)'], ['underline', 'U', 'Underline (⌘/Ctrl+U)'],
  ['strikeThrough', 'S', 'Strikethrough'], ['h3', 'H', 'Heading'], ['insertUnorderedList', '•', 'Bulleted list'],
  ['insertOrderedList', '1.', 'Numbered list'], ['blockquote', '❝', 'Quote'], ['pre', '</>', 'Code block'],
  ['link', '🔗', 'Link (⌘/Ctrl+click to open)'], ['mark', '🖍', 'Highlight'], ['clear', '⌫', 'Clear formatting'],
];

// Sanitized fragment. Images survive only as our own encrypted blobs (no external URLs, no forged ids that the
// server would reject on every save).
function clean(html) {
  const frag = window.DOMPurify.sanitize(html, PURIFY);
  frag.querySelectorAll('img').forEach(n => { if (!BLOB_ID.test(n.dataset.blob || '')) n.remove(); });
  return frag;
}

function cleanHTML(html) {
  const d = document.createElement('div');
  d.appendChild(clean(html));
  return d.innerHTML;
}

const serialize = node => cleanHTML(node.innerHTML);   // src (object URL) is not allowlisted; only data-blob survives

function textToHTML(text) {
  const d = document.createElement('div');
  d.textContent = text;
  return d.innerHTML.replace(/\n/g, '<br>');
}

export function htmlToText(html) {
  let out = '';
  const walk = n => {
    if (n.nodeType === Node.TEXT_NODE) out += n.nodeValue;
    else if (n.nodeName === 'BR') out += '\n';
    n.childNodes.forEach(walk);
    if (BLOCKS.has(n.nodeName) && !out.endsWith('\n')) out += '\n';
  };
  walk(clean(html));
  return out.replace(/\n+$/, '');
}

function tool(cmd, label, title, className = '') {
  const b = button(label, title, { className });
  b.dataset.cmd = cmd;
  return b;
}

function buildToolbar() {
  const bar = document.createElement('div');
  bar.className = 'sb-note-toolbar';
  bar.append(...TOOLS.map(([cmd, label, title]) => tool(cmd, label, title, 'sb-tb-rich')),
    tool('image', '🖼', 'Insert image (paste or drop also work · double-click to enlarge)'),
    tool('capture', '📷', 'Capture the screen into this note'));
  return bar;
}

// One editor per note. hooks: onChange(), onKind(kind), onError(msg), openImage(src)
export class Editor {
  constructor(note, hooks) {
    this.note = note;
    this.hooks = hooks;
    this.field = null;
    this.lastRange = null;
    this.toolbar = buildToolbar();
    this.body = document.createElement('div');
    this.body.className = 'sb-note-body';
    this.picker = Object.assign(document.createElement('input'), { type: 'file', accept: 'image/*', multiple: true });
    this.picker.className = 'hidden';
    this.toolbar.addEventListener('mousedown', e => { if (e.target.closest('button')) e.preventDefault(); });   // keep selection
    this.toolbar.addEventListener('click', e => { const b = e.target.closest('button'); if (b) this.run(b.dataset.cmd); });
    this.picker.addEventListener('change', () => { this.insertImages([...this.picker.files]); this.picker.value = ''; });
    this.body.addEventListener('dragover', e => { if (e.dataTransfer.types.includes('Files')) e.preventDefault(); });
    this.body.addEventListener('drop', e => {
      const f = imageFiles(e.dataTransfer);
      if (f.length) { e.preventDefault(); this.insertImages(f); }
    });
    this.mount(note.kind || 'text', note.body);
  }

  remember() {
    const sel = getSelection();
    if (sel.rangeCount && this.field.contains(sel.getRangeAt(0).commonAncestorContainer)) {
      this.lastRange = sel.getRangeAt(0).cloneRange();
    }
  }

  caret() {
    if (this.lastRange && this.field.contains(this.lastRange.commonAncestorContainer)) return this.lastRange;
    const r = document.createRange();
    r.selectNodeContents(this.field);
    r.collapse(false);
    return r;
  }

  mountRich(content) {
    const field = document.createElement('div');
    field.className = 'sb-rich';
    field.contentEditable = 'true';
    field.appendChild(clean(content || ''));
    ['keyup', 'mouseup', 'input'].forEach(ev => field.addEventListener(ev, () => this.remember()));
    field.addEventListener('paste', e => this.onRichPaste(e));
    field.addEventListener('click', e => this.onRichClick(e));
    field.addEventListener('dblclick', e => { if (e.target.tagName === 'IMG' && e.target.src) this.hooks.openImage(e.target.src); });
    hydrate(field);
    return field;
  }

  mount(kind, content) {
    this.note.kind = kind;
    this.lastRange = null;
    if (kind === 'rich') {
      this.field = this.mountRich(content);
    } else {
      this.field = document.createElement('textarea');
      this.field.className = 'sb-text';
      this.field.value = content || '';
      this.field.addEventListener('paste', e => this.onPlainPaste(e));
    }
    this.field.setAttribute('aria-label', 'Note text');
    this.field.addEventListener('input', this.hooks.onChange);
    this.body.replaceChildren(this.field);
  }

  async insertImages(files) {
    if (!files.length) return;
    if (this.note.kind !== 'rich') this.setKind('rich');
    const range = this.caret();
    for (const f of files) {
      try {
        const { id, url } = await upload(f);
        const img = document.createElement('img');
        img.dataset.blob = id;
        img.alt = '';
        img.src = url;
        range.insertNode(img);
        range.setStartAfter(img);
        range.collapse(true);
      } catch (e) {
        this.hooks.onError('Could not save image: ' + e.message);
      }
    }
    this.lastRange = range;
    this.hooks.onChange();
  }

  pasteImages(e) {
    const files = imageFiles(e.clipboardData);
    if (!files.length) return false;
    e.preventDefault();
    this.insertImages(files);
    return true;
  }

  onRichPaste(e) {
    if (this.pasteImages(e)) return;
    const html = e.clipboardData.getData('text/html');
    if (!html) return;                                  // plain text: let the browser paste it
    e.preventDefault();
    document.execCommand('insertHTML', false, cleanHTML(html));
    hydrate(this.field);                                // images copied from another note (data-blob)
  }

  onPlainPaste(e) {
    this.pasteImages(e);
  }

  onRichClick(e) {   // single click edits (caret, selection); enlarging an image is a double-click
    const a = e.target.closest('a');
    if (a && (e.metaKey || e.ctrlKey)) { e.preventDefault(); window.open(a.href, '_blank', 'noopener'); }
  }

  mark(unwrap) {
    const sel = getSelection();
    if (!sel.rangeCount || !this.field.contains(sel.getRangeAt(0).commonAncestorContainer)) return;
    const r = sel.getRangeAt(0);
    if (unwrap) {
      this.field.querySelectorAll('mark').forEach(m => { if (r.intersectsNode(m)) m.replaceWith(...m.childNodes); });
      return;
    }
    if (sel.isCollapsed) return;
    const m = document.createElement('mark');
    m.appendChild(r.extractContents());
    r.insertNode(m);
  }

  async capture() {
    try {
      await this.insertImages([await captureScreen()]);
    } catch (e) {
      if (e.name !== 'NotAllowedError') this.hooks.onError('Capture failed: ' + e.message);
    }
  }

  run(cmd) {
    if (cmd === 'image') { this.picker.click(); return; }
    if (cmd === 'capture') { this.capture(); return; }
    this.field.focus();
    if (cmd === 'link') {
      const url = prompt('Link address (https://…)');
      if (url && /^(https?:|mailto:)/i.test(url.trim())) document.execCommand('createLink', false, url.trim());
    } else if (cmd === 'mark' || cmd === 'clear') {
      this.mark(cmd === 'clear');
      if (cmd === 'clear') document.execCommand('removeFormat');
    } else if (['h3', 'blockquote', 'pre'].includes(cmd)) {
      document.execCommand('formatBlock', false, cmd);
    } else {
      document.execCommand(cmd);
    }
    this.hooks.onChange();
  }

  setKind(kind) {
    if (kind === this.note.kind) return;
    if (kind === 'text' && this.field.querySelector('img') && !confirm('Switching to plain text drops images and formatting. Continue?')) return;
    this.mount(kind, kind === 'rich' ? textToHTML(this.field.value) : htmlToText(serialize(this.field)));
    this.hooks.onKind(kind);
    this.hooks.onChange();
  }

  getBody() { return this.note.kind === 'rich' ? serialize(this.field) : this.field.value; }

  getBlobs() {
    return this.note.kind === 'rich' ? [...this.field.querySelectorAll('img[data-blob]')].map(i => i.dataset.blob) : [];
  }

  text() { return this.note.kind === 'rich' ? this.field.innerText : this.field.value; }   // search: live DOM is clean

  focus() { this.field.focus(); }
}
