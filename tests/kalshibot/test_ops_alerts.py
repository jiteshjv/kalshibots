import time

from kalshibot.alerts import Alerter, Watchdog, write_heartbeat


class Post:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, url, data, timeout):
        self.calls.append((url, data))
        if self.fail:
            raise ConnectionError("nope")

        class R:
            status_code = 200
        return R()


def mk(post, **kw):
    kw.setdefault("batch_s", 0.05)
    kw.setdefault("min_interval_s", 0.0)
    return Alerter("123:abc", "42", post=post, **kw)


def test_noop_when_unset(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    p = Post()
    a = Alerter.from_env(post=p)
    assert not a.enabled
    a.send("hi"); a.error("x", RuntimeError("y")); a.daily_summary({})
    assert a.flush(1) and p.calls == [] and a._thread is None


def test_from_env_names(monkeypatch):
    monkeypatch.setenv("MY_TOK", "t"); monkeypatch.setenv("MY_CHAT", "c")
    assert Alerter.from_env("MY_TOK", "MY_CHAT").enabled


def test_batching_and_payload():
    p = Post()
    a = mk(p, batch_s=0.3)
    a.stop_out("KXBTC15M-X", "y", 100, 0.95, 0.42, -54.1)
    a.send("second line")
    assert a.flush(5)
    a.close()
    assert len(p.calls) == 1
    url, data = p.calls[0]
    assert url == "https://api.telegram.org/bot123:abc/sendMessage" and data["chat_id"] == "42"
    assert "STOP KXBTC15M-X YES 100 @ 95.0c -> 42.0c, P&L -$54.10" in data["text"]
    assert "second line" in data["text"]


def test_rate_limit_merges():
    p = Post()
    a = mk(p, batch_s=0.01, min_interval_s=0.4)
    a.send("one")
    time.sleep(0.15)
    for i in range(5):
        a.send(f"m{i}")
    assert a.flush(5)
    a.close()
    assert len(p.calls) == 2  # second message holds all five lines
    assert all(f"m{i}" in p.calls[1][1]["text"] for i in range(5))


def test_dedupe_errors():
    p = Post()
    a = mk(p, dedupe_s=600)
    for _ in range(5):
        a.error("ws", RuntimeError("boom"))
    assert a.flush(5)
    a.close()
    txt = "\n".join(c[1]["text"] for c in p.calls)
    assert txt.count("ERROR in ws: RuntimeError: boom") == 1


def test_failure_does_not_raise():
    p = Post(fail=True)
    a = mk(p)
    a.send("x")
    assert a.flush(5)
    a.close()
    assert a.failed_count == 1 and a.sent_count == 0


def test_long_message_split():
    p = Post()
    a = mk(p)
    for i in range(30):
        a.send(f"{i:02d} " + "x" * 300)
    assert a.flush(5)
    a.close()
    assert len(p.calls) >= 3 and all(len(c[1]["text"]) <= 4096 for c in p.calls)
    assert sum(c[1]["text"].count(" xxx") for c in p.calls) == 30


def test_daily_summary_text():
    p = Post()
    a = mk(p)
    a.daily_summary({"date": "2026-10-19", "pnl": 812.5, "fees": 40.2, "markets": 60, "markets_won": 50,
                     "markets_lost": 10, "entries": 400, "contracts": 50000, "stopped": 30, "take_profit": 300,
                     "settled": 70})
    a.flush(5); a.close()
    assert "Daily summary 2026-10-19 (UTC): P&L +$812.50" in p.calls[0][1]["text"]


def test_watchdog(tmp_path):
    p = Post()
    a = mk(p)
    wd = Watchdog(tmp_path, a, max_age_s=60)
    assert not wd.check()  # no heartbeat file
    write_heartbeat(tmp_path)
    assert wd.check()
    write_heartbeat(tmp_path, time.time() - 120)
    assert not wd.check() and not wd.check()  # alerts once
    a.flush(5); a.close()
    txt = "\n".join(c[1]["text"] for c in p.calls)
    assert "no heartbeat file" in txt and "bot is running again" in txt and txt.count("no heartbeat for") == 1
