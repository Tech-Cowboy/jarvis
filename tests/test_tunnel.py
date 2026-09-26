from __future__ import annotations

import plistlib

import pytest

from daxton import tunnel
from daxton.config import Settings


def test_hostname_validation():
    assert tunnel.valid_hostname("daxton.example.com") and tunnel.valid_hostname("ai.my-barn.co.uk")
    for bad in ("", "localhost", "daxton", "-bad.example.com", "http://x.example.com", "x.example.com/", "a b.example.com"):
        assert not tunnel.valid_hostname(bad), bad


def test_config_yaml_shape():
    text = tunnel.config_yaml("6ff42ae2-1111-2222-3333-444444444444", "/Users/t/.cloudflared/6ff42ae2.json",
                              "Daxton.Example.com", port=8765)
    assert "tunnel: 6ff42ae2-1111-2222-3333-444444444444" in text
    assert "credentials-file: /Users/t/.cloudflared/6ff42ae2.json" in text
    assert "  - hostname: daxton.example.com\n    service: http://127.0.0.1:8765" in text
    assert text.rstrip().endswith("- service: http_status:404")
    with pytest.raises(tunnel.TunnelError):
        tunnel.config_yaml("id", "creds", "not a host")


def test_parse_tunnel_outputs():
    listing = ('[{"id": "old", "name": "daxton", "deleted_at": "2026-01-01T00:00:00Z"}, '
               '{"id": "abc", "name": "daxton", "created_at": "x", "deleted_at": "0001-01-01T00:00:00Z"}]')
    tunnels = tunnel.parse_tunnel_list(listing)
    assert tunnel.find_tunnel(tunnels, "daxton")["id"] == "abc"  # Go's zero time means "not deleted"
    assert tunnel.find_tunnel(tunnels, "other") is None
    assert tunnel.parse_tunnel_list("") == [] and tunnel.parse_tunnel_list("not json") == []
    assert tunnel.parse_created_tunnel("Tunnel credentials written to /Users/t/.cloudflared/"
                                       "6ff42ae2-1111-2222-3333-444444444444.json.\n"
                                       "Created tunnel daxton with id 6ff42ae2-1111-2222-3333-444444444444") \
        == "6ff42ae2-1111-2222-3333-444444444444"
    assert tunnel.parse_created_tunnel("nothing here") is None


def test_launchd_plist_roundtrip(tmp_path):
    data = plistlib.loads(tunnel.launchd_plist("ai.daxton.dashboard", ["/v/bin/daxton", "ui", "--no-browser"],
                                               tmp_path, tmp_path / "log.txt"))
    assert data["Label"] == "ai.daxton.dashboard" and data["KeepAlive"] is True and data["RunAtLoad"] is True
    assert data["ProgramArguments"] == ["/v/bin/daxton", "ui", "--no-browser"]
    assert data["WorkingDirectory"] == str(tmp_path) and data["EnvironmentVariables"]["PATH"].startswith("/opt/homebrew/bin")


def test_write_config_backs_up_previous(tmp_path):
    path = tmp_path / "config.yml"
    tunnel.write_config("one\n", path)
    tunnel.write_config("two\n", path)
    assert path.read_text() == "two\n"
    backups = list(tmp_path.glob("config.yml.*.bak"))
    assert len(backups) == 1 and backups[0].read_text() == "one\n"


def test_setup_refuses_without_password_or_hostname(tmp_path):
    s = Settings(data_dir=tmp_path)
    with pytest.raises(tunnel.TunnelError, match="hostname"):
        tunnel.setup(s, "")
    with pytest.raises(tunnel.TunnelError, match="DASHBOARD_PASSWORD"):
        tunnel.setup(s, "daxton.example.com")
    s.dashboard_password = "correct horse battery staple"
    with pytest.raises(tunnel.TunnelError, match="valid hostname"):
        tunnel.setup(s, "nope")


def test_access_instructions_mention_the_hostname():
    text = tunnel.access_instructions("daxton.example.com", "me@example.com")
    assert "daxton.example.com" in text and "me@example.com" in text and "One-time PIN" in text


def test_doctor_reports_portal_state(tmp_path, monkeypatch):
    from daxton.doctor import FAIL, OK, WARN, run_checks

    s = Settings(data_dir=tmp_path, llm_provider="keyword", tts_provider="console", public_hostname="daxton.example.com")
    areas = {c.area: c for c in run_checks(s)}
    assert areas["portal"].status == FAIL  # hostname but no password
    s.dashboard_password = "short"
    assert {c.area: c for c in run_checks(s)}["portal"].status == WARN
    s.dashboard_password = "correct horse battery staple"
    monkeypatch.setattr(tunnel, "find_cloudflared", lambda: None)
    areas = {c.area: c for c in run_checks(s)}
    assert areas["portal"].status == OK and areas["tunnel"].status == WARN and "daxton tunnel setup" in areas["tunnel"].hint


def test_quick_tunnel_url_parsing_and_lookup(tmp_path):
    log = ("2026-09-25T23:40:01Z INF Thank you for trying Cloudflare Tunnel.\n"
           "2026-09-25T23:40:02Z INF +--------------------------------------------------------------------------------------------+\n"
           "2026-09-25T23:40:02Z INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |\n"
           "2026-09-25T23:40:02Z INF |  https://tin-horse-beach-ride.trycloudflare.com                                            |\n"
           "2026-09-25T23:40:02Z INF +--------------------------------------------------------------------------------------------+\n")
    assert tunnel.quick_url_from_log(log) == "https://tin-horse-beach-ride.trycloudflare.com"
    assert tunnel.quick_url_from_log(log + "later restart ... https://new-words-here.trycloudflare.com |\n") \
        == "https://new-words-here.trycloudflare.com"
    assert tunnel.quick_url_from_log("") is None
    s = Settings(data_dir=tmp_path)
    assert tunnel.quick_url(s) is None and tunnel.portal_url(s) is None
    tunnel.quick_log_path(s).parent.mkdir(parents=True)
    tunnel.quick_log_path(s).write_text(log)
    assert tunnel.portal_url(s) == "https://tin-horse-beach-ride.trycloudflare.com"
    s.public_hostname = "daxton.example.com"
    assert tunnel.portal_url(s) == "https://daxton.example.com"  # a named hostname wins over the quick address


def test_quick_refuses_without_password(tmp_path):
    with pytest.raises(tunnel.TunnelError, match="DASHBOARD_PASSWORD"):
        tunnel.quick(Settings(data_dir=tmp_path))


def test_tunnel_service_mode_reads_the_agent(tmp_path, monkeypatch):
    plist = tmp_path / "ai.daxton.tunnel.plist"
    monkeypatch.setattr(tunnel, "tunnel_plist_path", lambda: plist)
    assert tunnel.tunnel_service_mode() is None and not tunnel.cloudflared_service_installed()
    plist.write_bytes(tunnel.launchd_plist("ai.daxton.tunnel", ["/opt/homebrew/bin/cloudflared", "--no-autoupdate", "tunnel",
                                                               "--url", "http://127.0.0.1:8765"], tmp_path, tmp_path / "t.log"))
    assert tunnel.tunnel_service_mode() == "quick" and tunnel.quick_service_installed()
    plist.write_bytes(tunnel.launchd_plist("ai.daxton.tunnel", ["/opt/homebrew/bin/cloudflared", "--no-autoupdate", "tunnel",
                                                               "run", "daxton"], tmp_path, tmp_path / "t.log"))
    assert tunnel.tunnel_service_mode() == "named" and tunnel.cloudflared_service_installed()
