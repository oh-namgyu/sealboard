# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.1] - 2026-10-08

### Fixed
- Start-up messages, including the one-time setup link, were held back when output is not a terminal
  (systemd, `nohup`, redirected logs), so a first-time setup could not be completed there.

## [0.1.0] - 2026-10-08

### Added
- Sticky-note board with tabs: create, move, resize, collapse, colour and delete notes;
  rename, reorder and delete tabs.
- Plain-text and rich-text notes (DOMPurify allowlist), search across all tabs.
- Images by paste, drag-and-drop, file picker or screen capture — encrypted before upload.
- Browser-only end-to-end encryption (PBKDF2-SHA256 → HKDF → AES-GCM-256) with zero-knowledge
  authentication, one-time setup code and auto-lock.
- Passphrase change rotates the data key: everything is re-encrypted and swapped atomically.
- Local client mode (`--remote`): the app is served from your machine, data stays on a remote server.
- Optimistic concurrency: edits from two windows never overwrite each other silently.
- Strict Content-Security-Policy, rate-limited logins, Docker image running as non-root.

[0.1.1]: https://github.com/oh-namgyu/sealboard/releases/tag/v0.1.1
[0.1.0]: https://github.com/oh-namgyu/sealboard/releases/tag/v0.1.0
