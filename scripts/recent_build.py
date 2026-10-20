# Build data_recent/prices_1s.npz + composite_1s.npy from the downloaded Coinbase/Kraken/Binance files (step 6 of recent_dl.py, standalone).
import sys,json,time
sys.argv=[sys.argv[0],"2026-10-15T20:00"]
src=open("../scripts/recent_dl.py").read()
pre=src[:src.index("kget=limiter")]; post=src[src.index("# 6) per-second"):]
exec(pre); END=max(int(k) for k in json.load(open("kr/all.json")))+1; exec(post)
