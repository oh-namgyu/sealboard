# Contributing

Thanks for taking the time to help!

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'
python -m playwright install chromium     # for the browser tests
```

## Tests

```bash
python -m pytest -m "not e2e"   # API tests (fast)
python -m pytest -m e2e         # browser end-to-end tests (headless Chromium)
```

## Ground rules

- **The server must never see plaintext.** Anything a user types or pastes is encrypted in
  `sealboard/static/js/crypto.js` before it leaves the page. New fields that carry user content
  go inside the encrypted payload, not next to it.
- **No inline styles or scripts.** The page runs under `style-src 'self'; script-src 'self'`.
  Dynamic geometry goes through the constructed stylesheet in `board.js`.
- Keep files under ~300 lines and functions under ~50 lines; split rather than grow.
- Python 3.9+ and no new runtime dependencies without a good reason.
- Add or update tests for behaviour changes (`tests/` for the API, `tests/e2e/` for the UI).

## Security issues

Please do not open public issues for vulnerabilities — see [SECURITY.md](SECURITY.md).
