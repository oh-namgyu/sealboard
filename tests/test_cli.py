"""Launcher: refuses weak setup codes, passes proxy and body-size settings to waitress."""
from sealboard import cli


def test_short_setup_code_from_env_is_refused(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('SEALBOARD_SETUP_CODE', 'short')
    assert cli.main(['--data', str(tmp_path)]) == 2
    assert 'at least 16' in capsys.readouterr().err


def test_serve_options_cap_body_size_and_trust_only_the_named_proxy():
    args = cli._parser().parse_args(['--port', '9000'])
    opts = cli._serve_options(args)
    assert opts['port'] == 9000 and opts['max_request_body_size'] > 8 * 1024 * 1024 and 'trusted_proxy' not in opts
    proxied = cli._serve_options(cli._parser().parse_args(['--trusted-proxy', '172.17.0.1']))
    assert proxied['trusted_proxy'] == '172.17.0.1' and proxied['clear_untrusted_proxy_headers'] is True
