"""Research recorder: every book snapshot/delta, Kalshi trade and BtcTick as gzipped JSON lines.

Files: `<data_dir>/rec/YYYY-MM-DD/HH.jsonl.gz` (Pacific-time date and hour of the record's wall-clock time). One line per record:
    {"ts": 1760900000.123, "kind": "book_delta", "d": {...payload...}}
* Rotates at the top of every hour (the finished file is closed, so it is a complete gzip).
* Flushes (zlib sync flush) every `flush_every_s` seconds, so after a crash everything up to the last flush can be
  read back with `zcat` or `read_records()`. If an hour file already exists when a new Recorder starts (restart
  within the same hour), it writes `HH.1.jsonl.gz`, `HH.2...` instead of appending to a possibly-truncated stream.
* Optional S3 upload (boto3, imported lazily): closed hour files are uploaded by a background thread to
  `s3://<bucket>/<prefix>rec/YYYY-MM-DD/<file>`; finished uploads are listed in `<data_dir>/rec/uploaded.txt`.
  No bucket or no boto3 -> uploads are skipped (logged once). Credentials come from the normal AWS env/role chain.

Thread-safe: `write()` may be called from any thread. Usage: rec = Recorder(dir, bucket); rec.write("trade", {...});
rec.close() at shutdown.
"""
from __future__ import annotations

import datetime as _dt
import gzip
import io
import json
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

log = logging.getLogger("kalshibot.recorder")
UTC = _dt.timezone.utc
from zoneinfo import ZoneInfo as _ZI
PT = _ZI("America/Los_Angeles")                 # recordings: rec/<PT date>/<PT hour>.jsonl.gz


class Recorder:
    def __init__(self, data_dir: str | os.PathLike, s3_bucket: str = "", *, s3_prefix: str = "",
                 flush_every_s: float = 5.0, clock: Callable[[], float] = time.time, s3_client: Any = None,
                 background: bool = False, max_queue: int = 200_000):
        self.root = Path(os.path.expanduser(str(data_dir))) / "rec"
        self.root.mkdir(parents=True, exist_ok=True)
        self.s3_bucket = s3_bucket or ""
        self.s3_prefix = s3_prefix
        self.flush_every_s = flush_every_s
        self._clock = clock
        self._lock = threading.Lock()
        self._gz: gzip.GzipFile | None = None
        self._raw: io.BufferedWriter | None = None
        self._hour_key: str | None = None
        self._path: Path | None = None
        self._last_flush = 0.0
        self.records = 0
        self.closed_files: list[Path] = []
        self._s3 = s3_client
        self._upq: "queue.Queue[Path | None]" | None = None
        self._upthread: threading.Thread | None = None
        if self.s3_bucket:
            self._start_uploader()
        # background=True (the bot): write() only queues the record; a writer thread does JSON, gzip, disk and fsync,
        # so slow or stalled storage never delays trading. A full queue drops records (counted in `dropped`).
        self.dropped = 0
        self._wq: "queue.Queue[tuple | None]" | None = None
        self._wthread: threading.Thread | None = None
        if background:
            self._wq = queue.Queue(maxsize=max_queue)
            self._wthread = threading.Thread(target=self._writer, name="recorder-writer", daemon=True)
            self._wthread.start()

    # ------------------------------------------------------------------ writing
    def write(self, kind: str, payload: dict, ts: float | None = None) -> None:
        """Append one record. ts defaults to now; the hour file is chosen from the wall clock at write time."""
        now = self._clock()
        if self._wq is not None:
            try:
                self._wq.put_nowait((now, kind, payload, ts))
            except queue.Full:
                self.dropped += 1
            return
        self._write_now(now, kind, payload, ts)

    def _writer(self) -> None:
        assert self._wq is not None
        while True:
            item = self._wq.get()
            if item is None:
                return
            try:
                self._write_now(*item)
            except Exception:                                # noqa: BLE001 - keep writing later records
                log.exception("recorder write")

    def _write_now(self, now: float, kind: str, payload: dict, ts: float | None) -> None:
        line = json.dumps({"ts": now if ts is None else ts, "kind": kind, "d": payload},
                          separators=(",", ":"), default=_default) + "\n"
        with self._lock:
            key = _dt.datetime.fromtimestamp(now, PT).strftime("%Y-%m-%d/%H")    # folder per PT date, file per PT hour
            if key != self._hour_key:
                self._rotate(key)
            assert self._gz is not None
            self._gz.write(line.encode())
            self.records += 1
            if now - self._last_flush >= self.flush_every_s:
                self._flush_locked(now)

    def flush(self) -> None:
        with self._lock:
            self._flush_locked(self._clock())

    def _flush_locked(self, now: float) -> None:
        if self._gz is not None:
            self._gz.flush()  # zlib Z_SYNC_FLUSH: everything so far is decodable
            assert self._raw is not None
            self._raw.flush()
            try:
                os.fsync(self._raw.fileno())
            except OSError:
                pass
        self._last_flush = now

    def _rotate(self, key: str) -> None:
        self._close_current()
        day, hh = key.split("/")
        d = self.root / day
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{hh}.jsonl.gz"
        n = 0
        while p.exists():
            n += 1
            p = d / f"{hh}.{n}.jsonl.gz"
        self._raw = open(p, "xb")
        self._gz = gzip.GzipFile(fileobj=self._raw, mode="wb", mtime=0)
        self._hour_key, self._path = key, p
        self._last_flush = self._clock()

    def _close_current(self) -> None:
        if self._gz is None:
            return
        try:
            self._gz.close()
            assert self._raw is not None
            self._raw.flush()
            os.fsync(self._raw.fileno())
        except OSError:
            pass
        finally:
            if self._raw is not None:
                self._raw.close()
        done = self._path
        self._gz = self._raw = None
        self._hour_key = self._path = None
        if done is not None:
            self.closed_files.append(done)
            if self._upq is not None:
                self._upq.put(done)

    @property
    def current_path(self) -> Path | None:
        return self._path

    def close(self, wait_upload_s: float = 30.0) -> None:
        if self._wq is not None and self._wthread is not None:   # write out what is queued first
            self._wq.put(None)
            self._wthread.join(30.0)
        with self._lock:
            self._close_current()
        if self._upq is not None and self._upthread is not None:
            self._upq.put(None)
            self._upthread.join(wait_upload_s)

    # ------------------------------------------------------------------ S3
    def _start_uploader(self) -> None:
        if self._s3 is None:
            try:
                import boto3  # type: ignore

                self._s3 = boto3.client("s3")
            except Exception as e:
                log.warning("recorder: S3 upload disabled (%s)", type(e).__name__)
                return
        self._upq = queue.Queue()
        # catch up on files closed by an earlier run but never uploaded
        for p in self.pending_uploads():
            self._upq.put(p)
        self._upthread = threading.Thread(target=self._upload_loop, name="kalshibot-rec-s3", daemon=True)
        self._upthread.start()
        log.info("recorder: uploading finished hour files to s3://%s/%srec/", self.s3_bucket, self.s3_prefix)

    def _uploaded_set(self) -> set[str]:
        f = self.root / "uploaded.txt"
        try:
            return set(f.read_text().split())
        except OSError:
            return set()

    def pending_uploads(self) -> list[Path]:
        done = self._uploaded_set()
        cur = self._path
        return sorted(p for p in self.root.glob("*/*.jsonl.gz")
                      if p != cur and p.relative_to(self.root).as_posix() not in done)

    def _upload_loop(self) -> None:
        assert self._upq is not None
        while True:
            p = self._upq.get()
            if p is None:
                return
            rel = p.relative_to(self.root).as_posix()          # S3 key with / on Windows too
            if rel in self._uploaded_set():
                continue
            for attempt in range(5):
                try:
                    self._s3.upload_file(str(p), self.s3_bucket, f"{self.s3_prefix}rec/{rel}")
                    with open(self.root / "uploaded.txt", "a") as fh:
                        fh.write(rel + "\n")
                    break
                except Exception as e:
                    log.warning("recorder: upload of %s failed (%s), attempt %d", rel, type(e).__name__, attempt + 1)
                    time.sleep(min(2 ** attempt, 30))


def _default(o: Any) -> Any:
    if hasattr(o, "__dataclass_fields__"):
        from dataclasses import asdict

        return asdict(o)
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def read_records(path: str | os.PathLike) -> Iterator[dict]:
    """Read a recording back, tolerating a truncated tail (crash before close)."""
    with open(path, "rb") as fh:
        raw = fh.read()
    import zlib

    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    data = d.decompress(raw)
    buf = data.decode(errors="replace")
    for ln in buf.splitlines():
        if not ln.strip():
            continue
        try:
            yield json.loads(ln)
        except json.JSONDecodeError:
            return  # partial last line
