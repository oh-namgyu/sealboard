// Passphrase change = new data key. Every image, note and tab name is re-encrypted in this browser and the
// server swaps them in one atomic step (POST /api/vault/rotate), deleting images that were under the old key.
// Anyone who knew the old passphrase and kept a copy of the data cannot read anything written from now on.
import { api } from './api.js';
import * as sc from './crypto.js';

// Images are re-uploaded under new ids, one at a time (bounded memory: each can be up to 8 MB).
async function reencryptImages(notes, key, progress) {
  const ids = [...new Set(notes.flatMap(n => n.blobs || []))];
  const renamed = new Map();
  for (const [i, id] of ids.entries()) {
    progress(`Re-encrypting images ${i + 1}/${ids.length}…`);
    const image = await sc.openBlob(id, await api.getBlob(id));
    const fresh = sc.newId('b');
    await api.putBlob(fresh, await sc.sealBlob(fresh, image.type, new Uint8Array(await image.arrayBuffer()), key));
    renamed.set(id, fresh);
  }
  return renamed;
}

// Rich bodies point at images by data-blob id; plain bodies have none.
const relink = (body, renamed) =>
  body.replace(/data-blob="(b_[A-Za-z0-9_-]+)"/g, (m, id) => `data-blob="${renamed.get(id) || id}"`);

async function reencryptNote(n, key, renamed) {
  const body = n.kind === 'rich' ? relink(n.body || '', renamed) : n.body;
  const blobs = (n.blobs || []).map(b => renamed.get(b) || b);
  const sealed = await sc.sealJSON(n.id, { title: n.title, kind: n.kind, body }, key);
  return { note: n, body, blobs, wire: { id: n.id, baseRev: n.rev, blobs, ...sealed } };
}

// → { next: {vault, authKey, key}, notes: [{note, body, blobs}] } once the server has swapped everything.
// Throws (and changes nothing that matters) if the current passphrase is wrong or another window edited meanwhile.
export async function rotate({ vault, tabs, notes, oldPass, newPass, progress }) {
  progress('Checking passphrase…');
  const next = await sc.prepareRotation(vault, oldPass, newPass);
  const renamed = await reencryptImages(notes, next.key, progress);
  progress('Re-encrypting notes…');
  const done = await Promise.all(notes.map(n => reencryptNote(n, next.key, renamed)));
  const sealedTabs = await Promise.all(tabs.map(async t =>
    ({ id: t.id, baseRev: t.rev, ...(await sc.sealJSON(t.id, { name: t.name }, next.key)) })));
  progress('Saving…');
  await api.rotateVault({ vault: next.vault, authKey: next.authKey, tabs: sealedTabs, notes: done.map(d => d.wire) });
  return { next, notes: done };
}
