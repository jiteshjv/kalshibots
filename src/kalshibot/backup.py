"""Back up order-book recordings: copy finished hourly files (<data_dir>/rec/<date>/<hour>.jsonl.gz) to another folder
(an external drive, a network share, a OneDrive / iCloud folder...). The file still being written is skipped. Optionally
delete local copies older than --keep-days once they are safely in the backup (same size).

    python -m kalshibot.backup --from C:\\kalshibot\\jitesh\\btc\\data --to D:\\kalshibot-backup\\jitesh-btc [--keep-days 30]
    (or several --from/--to pairs: --pair <from> <to> --pair <from> <to>)
Safe to run as often as you like (it only copies new or changed files).
"""
from __future__ import annotations

import argparse
import os
import shutil
import time
from pathlib import Path


def backup(src_data: str | os.PathLike, dest: str | os.PathLike, keep_days: float | None = None,
           now: float | None = None, settle_s: float = 120) -> dict:
    now = time.time() if now is None else now
    src = Path(os.path.expanduser(str(src_data))) / "rec"
    dst = Path(os.path.expanduser(str(dest)))
    copied = skipped = deleted = 0
    size = 0
    if not src.exists():
        return {"copied": 0, "skipped": 0, "deleted": 0, "bytes": 0, "note": f"no recordings in {src}"}
    for f in sorted(src.glob("*/*.jsonl.gz")):
        age = now - f.stat().st_mtime
        if age < settle_s:                          # still being written (the current hour)
            skipped += 1
            continue
        out = dst / f.parent.name / f.name
        if not out.exists() or out.stat().st_size != f.stat().st_size:
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".part")
            shutil.copy2(f, tmp)
            os.replace(tmp, out)
            copied += 1
            size += f.stat().st_size
        if keep_days and age > keep_days * 86400 and out.exists() and out.stat().st_size == f.stat().st_size:
            f.unlink()
            deleted += 1
    for d in src.iterdir():                          # tidy empty date folders
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    return {"copied": copied, "skipped": skipped, "deleted": deleted, "bytes": size}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m kalshibot.backup", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src")
    ap.add_argument("--to", dest="dst")
    ap.add_argument("--pair", nargs=2, action="append", metavar=("FROM", "TO"), default=[])
    ap.add_argument("--keep-days", type=float, default=None, help="delete local recordings older than this (after backup)")
    a = ap.parse_args(argv)
    pairs = list(a.pair) + ([(a.src, a.dst)] if a.src and a.dst else [])
    if not pairs:
        ap.error("give --from and --to (or --pair FROM TO)")
    for s, d in pairs:
        r = backup(s, d, a.keep_days)
        print(f"{s} -> {d}: copied {r['copied']} files ({r['bytes'] / 1e6:.1f} MB), deleted {r['deleted']} local, "
              f"skipped {r['skipped']} still being written" + (f" ({r['note']})" if r.get("note") else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
