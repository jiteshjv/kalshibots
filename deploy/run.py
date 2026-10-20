"""Run one kalshibot from its folder:  python run.py <bot folder> [--check]
The bot folder holds config.yaml, data/ and logs/; its parent holds the person's .env and secrets/kalshi.pem.
Works the same on Windows, macOS and Linux (used by the Windows services and by hand).
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import shutil
import sys
from pathlib import Path


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v:
            os.environ[k] = v


def find_openssl() -> str | None:
    if shutil.which("openssl"):
        return shutil.which("openssl")
    for d in (r"C:\Program Files\Git\usr\bin", r"C:\Program Files\Git\mingw64\bin", r"C:\Program Files\OpenSSL-Win64\bin"):
        if (Path(d) / "openssl.exe").exists():
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            return str(Path(d) / "openssl.exe")
    return None


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    bot = Path(argv[0]).expanduser().resolve()
    check = "--check" in argv[1:]
    person = bot.parent
    load_env(person / ".env")
    key = person / "secrets" / "kalshi.pem"
    if key.exists():
        os.environ["KALSHI_PRIVATE_KEY_PATH"] = str(key)
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
    if not find_openssl():
        print("openssl not found: install Git for Windows (git-scm.com); it is needed to sign Kalshi requests", file=sys.stderr)
        return 3
    (bot / "logs").mkdir(exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(bot / "logs" / "kalshibot.log", maxBytes=20_000_000, backupCount=5,
                                              encoding="utf-8")
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[fh, sh])
    os.chdir(bot)
    from kalshibot.__main__ import main as kb
    if check:
        rc = kb(["check", "--config", str(bot / "config.yaml")])
        if os.environ.get("KALSHI_API_KEY_ID") and key.exists():
            from kalshibot.config import kalshi_credentials
            from kalshibot.marketdata.kalshi_rest import KalshiRest
            try:
                KalshiRest(*kalshi_credentials()).request("GET", "/portfolio/balance", auth=True)
                print("Kalshi key: OK (read-only balance request accepted)")
            except Exception as e:
                print(f"Kalshi key: FAILED ({e})")
                rc = 4
        else:
            print("Kalshi key: not set up yet (.env KALSHI_API_KEY_ID and secrets/kalshi.pem)")
        return rc
    return kb(["run", "--config", str(bot / "config.yaml")])


if __name__ == "__main__":
    raise SystemExit(main())
