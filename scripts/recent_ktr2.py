# Second Kalshi trade-tape downloader for data_recent/: same as recent_dl.py step 2, walking the market list from the other end.
# Skips files that exist; atomic writes, so running beside recent_dl.py is safe. (run from data_recent/)
import json,os,sys
sys.argv=[sys.argv[0],"2026-10-15T20:00"]
src=open("../scripts/recent_dl.py").read(); exec(src[:src.index("# 3) Coinbase")])
for x in M[::-1]: ktr(x)
print("FINISHED",flush=True)
