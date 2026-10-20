# Contracts traded per KXBTC15M market (both taker sides, whole 15 min) by month, from the per-second tapes (data*/ktr).
# python3 -P scripts/monthly_volume.py
import json,glob,os,statistics as S
from concurrent.futures import ProcessPoolExecutor
def vol(f):
    try:
        k=json.load(open(f)); return os.path.basename(f), sum(v["y"][1]+v["n"][1] for v in k.values())
    except Exception: return os.path.basename(f), None
if __name__=="__main__":
    fs=[f for d in ("data_hist","data","data_recent","data_mon","data_oct20") for f in glob.glob(f"{d}/ktr/*.json")]
    with ProcessPoolExecutor(16) as ex: R=dict(ex.map(vol,fs,chunksize=50))
    M={}
    for t,v in R.items():
        if v: M.setdefault(t[11:14],[]).append(v)
    print(f"{'month':5s} {'markets':>7s} {'median':>11s} {'average':>11s} {'busiest 10%':>12s}")
    for m in ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT"]:
        if m in M:
            v=sorted(M[m]); print(f"{m.title():5s} {len(v):7,d} {S.median(v):11,.0f} {sum(v)/len(v):11,.0f} {v[int(len(v)*.9)]:12,.0f}")
