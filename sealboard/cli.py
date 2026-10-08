"""Command-line launcher: ``sealboard [--host H] [--port P] [--data DIR]`` or ``sealboard --remote URL``."""
from __future__ import annotations

import argparse
import ipaddress
import os
import sys
from typing import List, Optional

from sealboard import __version__
from sealboard import store as st
from sealboard.app import create_app
from sealboard.remote import create_client_app

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
    p.add_argument('--remote', default=env('SEALBOARD_REMOTE', ''),
                   help='local client mode: serve the app from this machine, keep data on this remote sealboard')
    p.add_argument('--version', action='version', version='sealboard ' + __version__)
    return p


def _serve_options(args: argparse.Namespace) -> dict:
    opts = {'host': args.host, 'port': args.port, 'threads': 8,
            'max_request_body_size': st.MAX_REQUEST}    # refuse huge bodies before buffering them
    if args.trusted_proxy:
        opts.update(trusted_proxy=args.trusted_proxy, clear_untrusted_proxy_headers=True,
                    trusted_proxy_headers='x-forwarded-for x-forwarded-proto x-forwarded-host')
    return opts


def _is_loopback(host: str) -> bool:
    try:
        return host == 'localhost' or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _serve(app: object, args: argparse.Namespace) -> int:
    from waitress import serve
    serve(app, **_serve_options(args))
    return 0


def _run_client(args: argparse.Namespace) -> int:
    """--remote: code from this machine, ciphertext on the remote. A personal client, so localhost only."""
    if not _is_loopback(args.host):
        print('--remote runs a personal client and binds to localhost only', file=sys.stderr)
        return 2
    try:
        app = create_client_app(args.remote, allowed_hosts=args.allowed_hosts.split(','))
    except ValueError as e:
        print(f'--remote: {e}', file=sys.stderr)
        return 2
    print(f'sealboard {__version__} — local client for {app.config["SEALBOARD_REMOTE"]}')
    print(f'Open http://{args.host}:{args.port}/  (page code comes from this machine, notes stay encrypted remotely)')
    return _serve(app, args)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.remote:
        return _run_client(args)
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
    return _serve(app, args)
