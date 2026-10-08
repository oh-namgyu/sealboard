// Note images: shrink pasted/captured images, encrypt and upload them; fetch + decrypt into object URLs to show.
// Rich-text bodies store only <img data-blob="b_…">; the src is filled in at render time, so no URL is ever saved.
import { api } from './api.js';
import { newId, openBlob, sealBlob } from './crypto.js';

const MAX_DIM = 2000;             // long-edge cap: keeps screenshot text readable without storing 4K originals
const urls = new Map();           // blob id → Promise<object URL>; shared by concurrent lookups, revoked on lock

async function shrink(file) {
  const bmp = await createImageBitmap(file);
  const scale = Math.min(1, MAX_DIM / Math.max(bmp.width, bmp.height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(bmp.width * scale);
  canvas.height = Math.round(bmp.height * scale);
  canvas.getContext('2d').drawImage(bmp, 0, 0, canvas.width, canvas.height);
  bmp.close();
  const out = await new Promise(res => canvas.toBlob(res, 'image/webp', 0.9));
  return out && out.size < file.size ? out : file;   // no webp encoder (PNG fallback) or bigger → keep original
}

// image file → encrypted upload → { id, url }
export async function upload(file) {
  const img = await shrink(file);
  const id = newId('b');
  const sealed = await sealBlob(id, img.type || 'image/png', new Uint8Array(await img.arrayBuffer()));
  await api.putBlob(id, sealed);
  const url = URL.createObjectURL(img);
  urls.set(id, Promise.resolve(url));
  return { id, url };
}

function urlFor(id) {
  if (!urls.has(id)) {
    urls.set(id, api.getBlob(id).then(buf => openBlob(id, buf)).then(b => URL.createObjectURL(b))
      .catch(e => { urls.delete(id); throw e; }));
  }
  return urls.get(id);
}

// Fill every img[data-blob] under root with its decrypted image. Failures are marked, the text stays.
export async function hydrate(root) {
  await Promise.all([...root.querySelectorAll('img[data-blob]')].map(async img => {
    try {
      img.src = await urlFor(img.dataset.blob);
    } catch (e) {
      img.alt = 'Image unavailable';
      img.classList.add('sb-img-broken');
    }
  }));
}

export function revokeAll() {
  urls.forEach(p => p.then(u => URL.revokeObjectURL(u), () => {}));
  urls.clear();
}

// Screen capture: the browser asks which screen/window to share; we grab one frame and stop sharing.
export async function captureScreen() {
  const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
  try {
    const video = document.createElement('video');
    video.srcObject = stream;
    video.muted = true;
    await video.play();
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));   // wait for a painted frame
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);
    return await new Promise(res => canvas.toBlob(res, 'image/png'));
  } finally {
    stream.getTracks().forEach(t => t.stop());
  }
}

export function imageFiles(dataTransfer) {
  return [...(dataTransfer && dataTransfer.files || [])].filter(f => f.type.startsWith('image/'));
}

// Full-size view (double-click an image in a note). A <dialog> brings Escape, backdrop and focus handling.
export function initLightbox(dlg) {
  const img = dlg.querySelector('img');
  dlg.addEventListener('click', () => dlg.close());
  dlg.addEventListener('close', () => img.removeAttribute('src'));
  return src => { img.src = src; dlg.showModal(); };
}
