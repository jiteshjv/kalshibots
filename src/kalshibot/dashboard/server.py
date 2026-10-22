"""Tiny stdlib HTTP server for the kalshibot dashboard.

    GET  /                 the dashboard page (dashboard/index.html)
    GET  /dashboard.json   `<data_dir>/dashboard.json` (regenerated on request if `export` is given and the file is
                           older than `max_age_s`)
    GET  /health           {"ok": bool, "heartbeat_age_s": float|null, "kill": bool}
    POST /kill             creates the kill file `<data_dir>/<kill_file>` (sell everything, then stop); requires the token from env
                           KALSHIBOT_DASH_TOKEN, sent as header "Authorization: Bearer <token>" or "X-Kill-Token".
                           If the env var is unset the endpoint answers 403 (disabled).
    POST /disarm           same, creates `<data_dir>/<disarm_file>` (no new entries; positions close normally, then idle)
    POST /rearm            same token; asks the app (on_rearm) to delete the switch files and trade again (only when idle)
    GET  /market.json      one market's chart: per-second BTC / bids / asks, every entry check, lots (?t=<ticker>, default: now)
    GET  /markets.json     the markets with a chart (running + the last 200 finished)
    GET  /settings.json    the changeable settings (current values, limits, labels) - needs the running bot
    POST /settings         same token; JSON body {"strategy.size_late": 10, ...}; saved to <data_dir>/settings.json

Binds 127.0.0.1 by default (read it from your own computer over an SSH tunnel). Inside Docker set
KALSHIBOT_DASH_HOST=0.0.0.0 and publish the port as "127.0.0.1:8787:8787" so it is still only reachable from the
server itself.

Usage from the app:   srv = DashboardServer(data_dir, cfg.io.dashboard_port, kill_file=cfg.risk.kill_file,
                                            export=journal.export_dashboard_json); srv.start(); ... srv.stop()
Standalone:            python -m kalshibot.dashboard.server --data-dir /data --port 8787
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

STATIC = Path(__file__).resolve().parent
TOKEN_ENV = "KALSHIBOT_DASH_TOKEN"
HOST_ENV = "KALSHIBOT_DASH_HOST"
_EMPTY = b'{"m":[],"e":{},"meta":{"note":"no data yet"},"ev":[]}'


class DashboardServer:
    def __init__(self, data_dir: str | os.PathLike, port: int = 8787, host: str | None = None,
                 kill_file: str = "KILL", disarm_file: str = "DISARM", export: Callable[[Path], object] | None = None, max_age_s: float = 15.0,
                 token: str | None = None, heartbeat_max_age_s: float = 90.0):
        self.data_dir = Path(os.path.expanduser(str(data_dir)))
        self.port = port
        self.host = host or os.environ.get(HOST_ENV) or "127.0.0.1"
        self.kill_path = self.data_dir / kill_file
        self.disarm_path = self.data_dir / disarm_file
        self.json_path = self.data_dir / "dashboard.json"
        self.export = export
        self.max_age_s = max_age_s
        self.heartbeat_max_age_s = heartbeat_max_age_s
        self._token = token if token is not None else os.environ.get(TOKEN_ENV, "")
        self._export_lock = threading.Lock()
        self.httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.on_rearm: Callable[[], tuple[bool, str]] | None = None   # app.rearm
        self.on_kill: Callable[[], None] | None = None  # optional callback (e.g. alert) after the kill file is made
        self.on_get_settings: Callable[[], dict] | None = None              # app.settings_view
        self.on_market: Callable[[str | None], dict] | None = None          # app.market_chart (one market's chart)
        self.on_markets: Callable[[], list] | None = None                   # app.market_chart_list
        self.on_set_settings: Callable[[dict], tuple[bool, str, list]] | None = None   # app.update_settings

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> int:
        """Start serving in a daemon thread. Returns the bound port (useful with port=0)."""
        handler = _make_handler(self)
        self.httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="kalshibot-dashboard", daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    # ------------------------------------------------------------------ helpers
    def dashboard_bytes(self) -> bytes:
        if self.export is not None:
            with self._export_lock:
                try:
                    age = time.time() - self.json_path.stat().st_mtime
                except OSError:
                    age = 1e9
                if age > self.max_age_s:
                    try:
                        self.export(self.json_path)
                    except Exception:
                        pass
        try:
            return self.json_path.read_bytes()
        except OSError:
            return _EMPTY

    def health(self) -> dict:
        from ..alerts import heartbeat_age

        age = heartbeat_age(self.data_dir)
        return {"ok": age is not None and age <= self.heartbeat_max_age_s,
                "heartbeat_age_s": None if age is None else round(age, 1), "kill": self.kill_path.exists()}

    def check_token(self, given: str) -> bool:
        return bool(self._token) and hmac.compare_digest(given.encode(), self._token.encode())

    def make_disarm(self, source: str) -> None:
        self.disarm_path.write_text(f"disarmed via dashboard ({source}) at {time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")

    def make_kill(self, source: str) -> None:
        self.kill_path.write_text(f"killed via dashboard ({source}) at {time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
        if self.on_kill:
            try:
                self.on_kill()
            except Exception:
                pass


def _make_handler(srv: DashboardServer):
    class H(BaseHTTPRequestHandler):
        server_version = "kalshibot"
        sys_version = ""

        def log_message(self, *a):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: dict) -> None:
            self._send(code, json.dumps(obj).encode())

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                try:
                    self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
                except OSError:
                    self._send(500, b"dashboard page missing", "text/plain")
            elif path == "/dashboard.json":
                self._send(200, srv.dashboard_bytes())
            elif path == "/health":
                self._json(200, srv.health())
            elif path in ("/market.json", "/markets.json"):
                cb = srv.on_market if path == "/market.json" else srv.on_markets
                if cb is None:
                    return self._json(409, {"ok": False, "error": "the market chart needs the running bot"})
                from urllib.parse import parse_qs
                q = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
                try:
                    self._json(200, cb(q.get("t", [None])[0]) if path == "/market.json" else cb())
                except Exception as e:  # noqa: BLE001
                    self._json(500, {"ok": False, "error": str(e)[:200]})
            elif path == "/settings.json":
                if srv.on_get_settings is None:
                    return self._json(409, {"ok": False, "error": "settings need the running bot"})
                self._json(200, srv.on_get_settings())
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(min(n, 65536)) if n else b""
            if path not in ("/kill", "/disarm", "/rearm", "/settings"):
                return self._send(404, b"not found", "text/plain")
            if not srv._token:
                return self._json(403, {"ok": False, "error": f"kill endpoint disabled: set {TOKEN_ENV}"})
            auth = self.headers.get("Authorization", "")
            given = auth[7:].strip() if auth.lower().startswith("bearer ") else (self.headers.get("X-Kill-Token") or "")
            if not srv.check_token(given):
                time.sleep(0.5)  # slow down guessing
                return self._json(401, {"ok": False, "error": "bad token"})
            if path == "/settings":
                if srv.on_set_settings is None:
                    return self._json(409, {"ok": False, "error": "settings need the running bot"})
                try:
                    raw = json.loads(body or b"{}")
                except ValueError:
                    return self._json(400, {"ok": False, "error": "body must be JSON"})
                ok, msg, errs = srv.on_set_settings(raw if isinstance(raw, dict) else {})
                return self._json(200 if ok else 400, {"ok": ok, "message": msg, "errors": errs})
            if path == "/rearm":
                if srv.on_rearm is None:
                    return self._json(409, {"ok": False, "error": "rearm needs the running bot"})
                ok, msg = srv.on_rearm()
                return self._json(200 if ok else 409, {"ok": ok, "error": None if ok else msg, "message": msg})
            if path == "/disarm":
                srv.make_disarm(self.client_address[0])
                return self._json(200, {"ok": True, "disarm_file": str(srv.disarm_path)})
            srv.make_kill(self.client_address[0])
            self._json(200, {"ok": True, "kill_file": str(srv.kill_path)})

    return H


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m kalshibot.dashboard.server")
    ap.add_argument("--data-dir", default=os.environ.get("KALSHIBOT_DATA_DIR", "~/kalshibot-data"))
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default=None)
    ap.add_argument("--kill-file", default="KILL")
    a = ap.parse_args(argv)
    s = DashboardServer(a.data_dir, a.port, a.host, a.kill_file)
    s.start()
    print(f"dashboard on http://{s.host}:{s.port}/")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        s.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
