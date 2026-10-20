import json
import urllib.error
import urllib.request

import pytest

from kalshibot.alerts import write_heartbeat
from kalshibot.dashboard.server import DashboardServer
from kalshibot.journal import Journal


def get(url, method="GET", headers=None):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


@pytest.fixture
def srv(tmp_path):
    j = Journal(tmp_path)
    j.event("info", "start", "paper")
    s = DashboardServer(tmp_path, port=0, kill_file="KILL", export=j.export_dashboard_json, token="s3cret")
    port = s.start()
    yield s, f"http://127.0.0.1:{port}"
    s.stop()
    j.close()


def test_binds_localhost(srv):
    s, _ = srv
    assert s.httpd is None or s.httpd.server_address[0] == "127.0.0.1"


def test_page_and_json(srv):
    s, base = srv
    code, body, h = get(base + "/")
    assert code == 200 and b"<title>kalshibot</title>" in body and "text/html" in h["Content-Type"]
    code, body, h = get(base + "/dashboard.json")
    d = json.loads(body)
    assert code == 200 and d["m"] == [] and d["ev"][0]["kind"] == "start"
    assert h["Cache-Control"] == "no-store"
    assert get(base + "/nope")[0] == 404


def test_health(srv, tmp_path):
    s, base = srv
    assert json.loads(get(base + "/health")[1]) == {"ok": False, "heartbeat_age_s": None, "kill": False}
    write_heartbeat(tmp_path)
    assert json.loads(get(base + "/health")[1])["ok"] is True


def test_kill_requires_token(srv, tmp_path):
    s, base = srv
    assert get(base + "/kill", "POST")[0] == 401
    assert get(base + "/kill", "POST", {"Authorization": "Bearer wrong"})[0] == 401
    assert not (tmp_path / "KILL").exists()
    code, body, _ = get(base + "/kill", "POST", {"Authorization": "Bearer s3cret"})
    assert code == 200 and json.loads(body)["ok"] and (tmp_path / "KILL").exists()
    assert json.loads(get(base + "/health")[1])["kill"] is True
    assert get(base + "/kill", "GET")[0] == 404


def test_kill_disabled_without_token(tmp_path, monkeypatch):
    monkeypatch.delenv("KALSHIBOT_DASH_TOKEN", raising=False)
    s = DashboardServer(tmp_path, port=0)
    port = s.start()
    try:
        code, body, _ = get(f"http://127.0.0.1:{port}/kill", "POST", {"Authorization": "Bearer "})
        assert code == 403 and not (tmp_path / "KILL").exists()
        assert json.loads(get(f"http://127.0.0.1:{port}/dashboard.json")[1])["m"] == []
    finally:
        s.stop()


def test_token_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("KALSHIBOT_DASH_TOKEN", "envtok")
    s = DashboardServer(tmp_path, port=0)
    port = s.start()
    try:
        assert get(f"http://127.0.0.1:{port}/kill", "POST", {"X-Kill-Token": "envtok"})[0] == 200
    finally:
        s.stop()
