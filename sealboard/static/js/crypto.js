// Browser-only cryptography. The server never receives the passphrase, the data key or any plaintext.
//
//   passphrase ──PBKDF2-SHA256(salt, 600k)──► 256-bit master ──HKDF-SHA256──┬─ "sealboard kek v1"  → KEK (stays here)
//                                                                         └─ "sealboard auth v1" → auth key (bearer token;
//                                                                                                   server keeps SHA-256 only)
//   DEK = random AES-GCM-256 key, wrapped by the KEK. A passphrase change makes a new DEK and re-encrypts everything.
//   Every tab name, note and image is sealed with the DEK, a fresh 96-bit IV and the item id as additional
//   authenticated data (so ciphertexts cannot be swapped between items).
const ITER = 600000;              // OWASP 2023 recommendation for PBKDF2-SHA256 — also the minimum we accept
const MIN_SALT = 16;
const enc = new TextEncoder();
const dec = new TextDecoder();
const AES = { name: 'AES-GCM', length: 256 };
let dek = null;                   // non-extractable; exists only while unlocked

export const supported = !!(window.isSecureContext && window.crypto && crypto.subtle);
export const isUnlocked = () => dek !== null;

function b64(buf) {
  const u = new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000));
  return btoa(s);
}
const unb64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
const rand = n => crypto.getRandomValues(new Uint8Array(n));
const aad = id => enc.encode(id);
const hkdf = info => ({ name: 'HKDF', hash: 'SHA-256', salt: new Uint8Array(0), info: enc.encode(info) });

export function newId(kind) {
  return kind + '_' + b64(rand(12)).replace(/\+/g, '-').replace(/\//g, '_');
}

// passphrase + KDF params → { kek, authKey }. Refuses weakened parameters (a tampered server could otherwise
// downgrade the KDF to make the auth key cheap to brute-force).
export async function derive(pass, params) {
  const salt = unb64(params.salt);
  if (params.kdf !== 'PBKDF2-SHA256' || !(params.iter >= ITER) || salt.length < MIN_SALT) {
    throw new Error('the server sent unsafe key-derivation settings');
  }
  const base = await crypto.subtle.importKey('raw', enc.encode(pass), 'PBKDF2', false, ['deriveBits']);
  const master = await crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt, iterations: params.iter }, base, 256);
  const hk = await crypto.subtle.importKey('raw', master, 'HKDF', false, ['deriveKey', 'deriveBits']);
  const kek = await crypto.subtle.deriveKey(hkdf('sealboard kek v1'), hk, AES, false, ['wrapKey', 'unwrapKey']);
  const auth = await crypto.subtle.deriveBits(hkdf('sealboard auth v1'), hk, 256);
  return { kek, authKey: b64(auth) };
}

// Fresh salt for a (new) passphrase → { params, kek, authKey }
async function freshKeys(pass) {
  const params = { kdf: 'PBKDF2-SHA256', iter: ITER, salt: b64(rand(MIN_SALT)) };
  return { params, ...(await derive(pass, params)) };
}

async function wrap(key, kek) {
  const iv = rand(12);
  const ct = await crypto.subtle.wrapKey('raw', key, kek, { name: 'AES-GCM', iv });
  return { iv: b64(iv), ct: b64(ct) };
}

// A wrong key fails GCM authentication and throws — the unwrap itself is the check.
function unwrap(wrapped, kek, extractable) {
  return crypto.subtle.unwrapKey('raw', unb64(wrapped.ct), kek, { name: 'AES-GCM', iv: unb64(wrapped.iv) },
    AES, extractable, ['encrypt', 'decrypt']);
}

// New vault for a passphrase → { vault: {kdf, iter, salt, wrapped}, authKey }. Unlocks this tab.
export async function createVault(pass) {
  const key = await crypto.subtle.generateKey(AES, true, ['encrypt', 'decrypt']);
  const { params, kek, authKey } = await freshKeys(pass);
  const vault = { ...params, wrapped: await wrap(key, kek) };
  dek = await unwrap(vault.wrapped, kek, false);            // keep only a non-extractable copy
  return { vault, authKey };
}

export async function openVault(wrapped, kek) {
  dek = await unwrap(wrapped, kek, false);
}

// Passphrase change step 1: prove the current passphrase, then make a brand-new DEK under the new one.
// The caller re-encrypts everything with `key` (rotate.js) and calls adopt(key) once the server has swapped.
export async function prepareRotation(vault, oldPass, newPass) {
  const [old, fresh] = await Promise.all([derive(oldPass, vault), freshKeys(newPass)]);
  await unwrap(vault.wrapped, old.kek, false);                  // throws if the current passphrase is wrong
  const key = await crypto.subtle.generateKey(AES, true, ['encrypt', 'decrypt']);
  const next = { ...fresh.params, wrapped: await wrap(key, fresh.kek) };
  return { vault: next, authKey: fresh.authKey, key: await unwrap(next.wrapped, fresh.kek, false) };
}

export function adopt(key) {
  dek = key;
}

export function lock() {
  dek = null;
}

async function encrypt(id, bytes, key) {
  const iv = rand(12);
  const ct = await crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData: aad(id) }, key, bytes);
  return { iv, ct: new Uint8Array(ct) };
}

async function decrypt(id, iv, ct) {
  return new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv, additionalData: aad(id) }, dek, ct));
}

// `key` defaults to the unlocked DEK; a key rotation passes the new one.
export async function sealJSON(id, obj, key = dek) {
  const s = await encrypt(id, enc.encode(JSON.stringify(obj)), key);
  return { iv: b64(s.iv), ct: b64(s.ct) };
}

export async function openJSON(id, item) {
  return JSON.parse(dec.decode(await decrypt(id, unb64(item.iv), unb64(item.ct))));
}

// Image blob on the wire: [12-byte iv][ciphertext]. Plaintext = [1-byte mime length][mime][bytes],
// so even the image type stays private.
export async function sealBlob(id, mime, bytes, key = dek) {
  const m = enc.encode(mime);
  const pt = new Uint8Array(1 + m.length + bytes.length);
  pt[0] = m.length;
  pt.set(m, 1);
  pt.set(bytes, 1 + m.length);
  const s = await encrypt(id, pt, key);
  const out = new Uint8Array(12 + s.ct.length);
  out.set(s.iv);
  out.set(s.ct, 12);
  return out;
}

export async function openBlob(id, buf) {
  const u = new Uint8Array(buf);
  const pt = await decrypt(id, u.slice(0, 12), u.slice(12));
  const n = pt[0];
  return new Blob([pt.slice(1 + n)], { type: dec.decode(pt.slice(1, 1 + n)) });
}
