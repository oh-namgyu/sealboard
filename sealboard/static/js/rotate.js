// Passphrase change = new data key. Every image, note and tab name is re-encrypted in this browser and the
// server swaps them in one atomic step (POST /api/vault/rotate), deleting images that were under the old key.
// Anyone who knew the old passphrase and kept a copy of the data cannot read anything written from now on.
import { api } from './api.js';
import * as sc from './crypto.js';

const BLOB_ID = /^b_[A-Za-z0-9_-]{10,40}$/;
const MAX_BYTES = 30 * 1024 * 1024;     // stay under the server's 32 MB request cap

// Images are re-uploaded under new ids, one at a time (bounded memory: each can be up to 8 MB). An image that is
// missing or does not decrypt is unusable anyway: it is reported and left out instead of blocking the change.
async function reencryptImages(notes, key, progress) {
  const ids = [...new Set(notes.flatMap(n => n.blobs || []))].filter(id => BLOB_ID.test(id));
  const renamed = new Map(), lost = [];
  for (const [i, id] of ids.entries()) {
    progress(`Re-encrypting images ${i + 1}/${ids.length}…`);
    let image;
    try {
      image = await sc.openBlob(id, await api.getBlob(id));
    } catch (e) {
      if (e.status === 404 || e.name === 'OperationError') { lost.push(id); continue; }
      throw e;
    }
    const fresh = sc.newId('b');
    await api.putBlob(fresh, await sc.sealBlob(fresh, image.type, new Uint8Array(await image.arrayBuffer()), key));
    renamed.set(id, fresh);
  }
  return { renamed, lost };
}

// Rich bodies point at images by data-blob id; plain bodies have none.
const relink = (body, renamed) =>
  body.replace(/data-blob="(b_[A-Za-z0-9_-]+)"/g, (m, id) => `data-blob="${renamed.get(id) || id}"`);

async function reencryptNote(n, key, renamed) {
  const body = n.kind === 'rich' ? relink(n.body || '', renamed) : n.body;
  const blobs = (n.blobs || []).filter(b => renamed.has(b)).map(b => renamed.get(b));
  const sealed = await sc.sealJSON(n.id, { title: n.title, kind: n.kind, body }, key);
  return { note: n, body, blobs, wire: { id: n.id, baseRev: n.rev, blobs, ...sealed } };
}

async function prepare(vault, oldPass, newPass) {
  try {
    return await sc.prepareRotation(vault, oldPass, newPass);
  } catch (e) {
    throw e.name === 'OperationError' ? Object.assign(new Error('Current passphrase is wrong'), { wrongPassphrase: true }) : e;
  }
}

function checkSize(payload) {
  if (JSON.stringify(payload).length > MAX_BYTES) {
    throw new Error('the notes are too large to re-encrypt in one request (over 30 MB of text)');
  }
}

// → { next: {vault, authKey, key}, notes: [{note, body, blobs}], lost, dropped } once the server has swapped
// everything. `drop` = ids of notes this browser could not decrypt; they are removed in the same step.
// Throws (and changes nothing that matters) on a wrong passphrase or if another window edited meanwhile.
export async function rotate({ vault, tabs, notes, drop, oldPass, newPass, progress }) {
  progress('Checking passphrase…');
  const next = await prepare(vault, oldPass, newPass);
  const { renamed, lost } = await reencryptImages(notes, next.key, progress);
  progress('Re-encrypting notes…');
  const done = await Promise.all(notes.map(n => reencryptNote(n, next.key, renamed)));
  const sealedTabs = await Promise.all(tabs.map(async t =>
    ({ id: t.id, baseRev: t.rev, ...(await sc.sealJSON(t.id, { name: t.name }, next.key)) })));
  const payload = { vault: next.vault, authKey: next.authKey, tabs: sealedTabs, notes: done.map(d => d.wire), drop };
  checkSize(payload);
  progress('Saving…');
  await api.rotateVault(payload);
  return { next, notes: done, lost: lost.length, dropped: drop.length };
}
