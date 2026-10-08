"""Command-line launcher: ``sealboard [--host H] [--port P] [--data DIR]``."""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from sealboard import __version__
from sealboard import store as st
from sealboard.app import create_app

MIN_SETUP_CODE = 16


def _parser() -> argparse.ArgumentParser:
    env = os.environ.get
    p = argparse.ArgumentParser(prog='sealboard', description='Self-hosted, end-to-end encrypted sticky-note board.')
    p.add_argument('--host', default=env('SEALBOARD_HOST', '127.0.0.1'), help='bind address (default 127.0.0.1)')
    p.add_argument('--port', type=int, default=int(env('SEALBOARD_PORT', '8080')), help='port (default 8080)')
    p.add_argument('--data', default=env('SEALBOARD_DATA', 'sealboard-data'),
                   help='data directory (default ./sealboard-data)')
    p.add_argument('--allowed-hosts', default=env('SEALBOARD_ALLOWED_HOSTS', ''),
                   help='extra Host names to accept, comma-separated (e.g. notes.example.org behind a proxy)')
    p.add_argument('--trusted-proxy', default=env('SEALBOARD_TRUSTED_PROXY', ''),
                   help='address of your reverse proxy; its X-Forwarded-* headers are then trusted')
    p.add_argument('--version', action='version', version='sealboard ' + __version__)
    return p


def _serve_options(args: argparse.Namespace) -> dict:
    opts = {'host': args.host, 'port': args.port, 'threads': 8,
            'max_request_body_size': st.MAX_BLOB + 1024 * 1024}    # refuse huge bodies before buffering them
    if args.trusted_proxy:
        opts.update(trusted_proxy=args.trusted_proxy, clear_untrusted_proxy_headers=True,
                    trusted_proxy_headers='x-forwarded-for x-forwarded-proto x-forwarded-host')
    return opts


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    setup_code = os.environ.get('SEALBOARD_SETUP_CODE') or None
    if setup_code and len(setup_code) < MIN_SETUP_CODE:
        print(f'SEALBOARD_SETUP_CODE must be at least {MIN_SETUP_CODE} characters', file=sys.stderr)
        return 2
    data_dir = os.path.abspath(args.data)
    app = create_app(data_dir, setup_code=setup_code, allowed_hosts=args.allowed_hosts.split(','))
    shown = '127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host
    url = f'http://{shown}:{args.port}/'
    print(f'sealboard {__version__} — data: {data_dir}')
    print(f'Open {url}')
    code = app.config['SEALBOARD_SETUP_CODE']
    if code:
        print('No vault yet. Create one with this one-time link:')
        print(f'  {url}#setup={code}')
    from waitress import serve
    serve(app, **_serve_options(args))
    return 0
