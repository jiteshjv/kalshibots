import datetime as dt
import gzip
import json

from kalshibot.recorder import Recorder, read_records

from zoneinfo import ZoneInfo
T0 = dt.datetime(2026, 10, 19, 13, 59, 58, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()   # folders are PT


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_hourly_rotation_and_gzip(tmp_path):
    clk = Clock(T0)
    r = Recorder(tmp_path, clock=clk, flush_every_s=0)
    r.write("btc", {"p": 67000.1})
    clk.t += 1
    r.write("trade", {"taker_side": "y", "price": 0.95, "qty": 10})
    p13 = r.current_path
    clk.t += 2  # 14:00:01
    r.write("book_delta", {"price": 0.5, "delta": -3, "side": "yes"})
    p14 = r.current_path
    r.close()
    assert p13 == tmp_path / "rec" / "2026-10-19" / "13.jsonl.gz"
    assert p14 == tmp_path / "rec" / "2026-10-19" / "14.jsonl.gz"
    with gzip.open(p13, "rt") as fh:
        lines = [json.loads(x) for x in fh]
    assert [x["kind"] for x in lines] == ["btc", "trade"]
    assert lines[1]["d"]["price"] == 0.95 and lines[0]["ts"] == T0
    with gzip.open(p14, "rt") as fh:
        assert json.loads(fh.readline())["kind"] == "book_delta"
    assert r.closed_files == [p13, p14] and r.records == 3


def test_flush_makes_data_readable_before_close(tmp_path):
    clk = Clock(T0 + 10)
    r = Recorder(tmp_path, clock=clk, flush_every_s=0)
    for i in range(5):
        r.write("btc", {"i": i})
    got = list(read_records(r.current_path))  # file still open (simulates a crash)
    assert [x["d"]["i"] for x in got] == [0, 1, 2, 3, 4]
    r.close()


def test_restart_same_hour_uses_new_part(tmp_path):
    clk = Clock(T0 + 10)
    r = Recorder(tmp_path, clock=clk)
    r.write("btc", {"i": 1})
    r.close()
    r2 = Recorder(tmp_path, clock=clk)
    r2.write("btc", {"i": 2})
    assert r2.current_path.name == "14.1.jsonl.gz"
    r2.close()


class FakeS3:
    def __init__(self):
        self.calls = []

    def upload_file(self, src, bucket, key):
        self.calls.append((bucket, key))


def test_s3_upload_of_closed_files(tmp_path):
    clk = Clock(T0)
    s3 = FakeS3()
    r = Recorder(tmp_path, s3_bucket="my-bucket", s3_prefix="host1/", clock=clk, s3_client=s3)
    r.write("btc", {"i": 1})
    clk.t += 5  # next hour
    r.write("btc", {"i": 2})
    r.close()
    assert ("my-bucket", "host1/rec/2026-10-19/13.jsonl.gz") in s3.calls
    assert ("my-bucket", "host1/rec/2026-10-19/14.jsonl.gz") in s3.calls
    assert len(s3.calls) == 2
    # a new recorder does not re-upload
    s3b = FakeS3()
    r3 = Recorder(tmp_path, s3_bucket="my-bucket", clock=clk, s3_client=s3b)
    r3.close()
    assert s3b.calls == [] and r3.pending_uploads() == []


def test_no_bucket_no_uploader(tmp_path):
    r = Recorder(tmp_path)
    r.write("x", {})
    r.close()
    assert r._upthread is None
