# Security

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting**: open the repository's
**Security** tab and choose **Report a vulnerability**. That keeps the details private
until a fix is out. For anything that is not sensitive, a normal issue is fine.

## What sealboard protects — and what it does not

sealboard encrypts every tab name, note (title, body, format) and image **in the browser**
with a key derived from your passphrase. The server, its disk and its backups hold only
ciphertext, layout, and a SHA-256 hash of an authentication key.

| Threat | Protected? |
|---|---|
| Someone copies the data directory or a backup | **Yes** — they get ciphertext; breaking it means brute-forcing your passphrase through PBKDF2-SHA256 (600,000 iterations) |
| Someone on the network without your passphrase | **Yes** — every data endpoint needs the auth key; failed attempts are rate-limited |
| The person running the server reads your notes from the files | **Yes** — same as the first row |
| A compromised or malicious server | **No.** It can serve modified JavaScript that captures your passphrase the next time you unlock. This is a limit of every web app that does encryption in the page; run sealboard on a machine you control |
| A malicious browser extension or a compromised device | **No** — anything that can read the page can read decrypted notes |
| Metadata | **Visible to the server:** number of tabs/notes, note positions, sizes, colours, which images belong to which note, timestamps and sizes |
| Rollback and reshuffling | A malicious server can serve an older copy of a note, bring back a deleted note, or move a note to another tab (the tab is layout, not encrypted). It cannot forge or read content |
| Forgotten passphrase | **Unrecoverable by design.** Nobody, including the server operator, can decrypt the notes |
| Someone who once knew your passphrase | Changing the passphrase re-wraps the same data key; notes are **not** re-encrypted. A person who knew the old passphrase **and** kept a copy of the data directory can still read it. If a passphrase leaked, start a new vault in a new data directory and copy your notes over (data-key rotation is on the roadmap) |
| Someone who obtains your auth key (e.g. from a proxy log) | They cannot read notes, but they can modify or delete data until you change the passphrase. Keep backups of the data directory |

## Hardening notes

- Browsers allow WebCrypto only on `https://` or `localhost`. Expose sealboard to other
  devices only through a TLS reverse proxy (see the README).
- The first vault can only be created with the one-time setup code printed at start-up
  (at least 16 characters if you set your own via `SEALBOARD_SETUP_CODE`); guesses are rate-limited.
- Failed logins are limited per client address (10 per 5 minutes). Behind a reverse proxy every request
  comes from the proxy's address, so set `SEALBOARD_TRUSTED_PROXY` — otherwise someone sending wrong keys
  could keep the owner locked out for a few minutes at a time.
- Only `localhost`, `127.0.0.1` and `[::1]` are accepted as `Host` by default (protects against DNS
  rebinding); add your own domain with `SEALBOARD_ALLOWED_HOSTS`.
- The browser refuses key-derivation settings weaker than PBKDF2-SHA256 with 600,000 iterations and a
  16-byte salt, so a tampered server cannot downgrade them.
- Responses carry a strict Content-Security-Policy (no inline scripts or styles, no framing).
- Rich text is filtered through a DOMPurify allowlist; external image URLs are dropped.
