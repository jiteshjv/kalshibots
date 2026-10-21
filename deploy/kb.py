"""Run a kalshibot tool with a person's .env loaded:  python kb.py <person folder> <tool> [args]
    python C:\\kalshibot\\app\\kb.py C:\\kalshibot\\jitesh alerts test        send a Telegram test message
    python C:\\kalshibot\\app\\kb.py C:\\kalshibot\\jitesh backup --pair C:\\kalshibot\\jitesh\\btc\\data D:\\backup\\jitesh-btc
Tools: alerts (test | watch --data-dir <bot>\\data), backup (see python kb.py <folder> backup --help).
"""
import importlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
sys.path.insert(0, str(HERE))
from run import load_env  # noqa: E402

if len(sys.argv) < 3:
    print(__doc__)
    raise SystemExit(2)
load_env(Path(sys.argv[1]) / ".env")
mod = importlib.import_module(f"kalshibot.{sys.argv[2]}")
raise SystemExit(mod.main(sys.argv[3:]))
