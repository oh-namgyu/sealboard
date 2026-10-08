// API client. After unlock every call carries the auth key as a bearer token; it lives only in memory.
let token = null;
let onUnauthorized = () => {};

export class ApiError extends Error {
  constructor(status, body) {
    super((body && body.error) || ('HTTP ' + status));
    this.status = status;
    this.body = body || {};
  }
}

export function setToken(value) {
  token = value;
}

// Called once when the current token is refused (e.g. the passphrase was changed in another window). The token
// is dropped first, so requests that follow fail fast instead of piling up more 401s.
export function whenUnauthorized(fn) {
  onUnauthorized = fn;
}

async function call(method, url, body, raw = false) {
  const sent = token;
  const opt = { method, headers: {} };
  if (sent) opt.headers.Authorization = 'Bearer ' + sent;
  if (body instanceof Uint8Array) {
    opt.headers['Content-Type'] = 'application/octet-stream';
    opt.body = body;
  } else if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(url, opt);
  if (raw && r.ok) return r.arrayBuffer();
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && sent && sent === token) { token = null; onUnauthorized(); }
  if (!r.ok) throw new ApiError(r.status, data);
  return data;
}

export const api = {
  vaultInfo: () => call('GET', '/api/vault'),
  createVault: (vault, authKey, setupCode) => call('POST', '/api/vault', { ...vault, authKey, setupCode }),
  changeVault: (vault, authKey) => call('PUT', '/api/vault', { vault, authKey }),
  board: () => call('GET', '/api/board'),
  putTab: (id, body) => call('PUT', '/api/tabs/' + id, body),
  orderTabs: ids => call('POST', '/api/tabs/order', { ids }),
  deleteTab: id => call('DELETE', '/api/tabs/' + id),
  putNote: (id, body) => call('PUT', '/api/notes/' + id, body),
  patchNote: (id, layout) => call('PATCH', '/api/notes/' + id, layout),
  deleteNote: id => call('DELETE', '/api/notes/' + id),
  putBlob: (id, bytes) => call('PUT', '/api/blobs/' + id, bytes),
  getBlob: id => call('GET', '/api/blobs/' + id, undefined, true),
};
