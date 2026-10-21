# Put the earlier BTC price history in front of a new data folder's prices (later folders win where they overlap), so its
# backtest's sweep checks see 30+ days. Keeps the folder's own files in <folder>/own/.  python3 -P scripts/stitch_history.py <folder>
import sys,os,glob,shutil,numpy as np
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); tgt=os.path.abspath(sys.argv[1])
def own(d,f): return f"{d}/own/{f}" if os.path.exists(f"{d}/own/{f}") else f"{d}/{f}"
cands=[os.path.join(ROOT,d) for d in ("data","data_recent","data_mon","data_oct20")]+sorted(glob.glob(os.path.join(ROOT,"data_daily","*")))
cands=[d for d in cands if os.path.exists(own(d,"composite_1s.npy")) and os.path.abspath(d)<=tgt or os.path.abspath(d)==tgt]
cands=[d for d in cands if os.path.exists(own(d,"composite_1s.npy"))]
if tgt not in [os.path.abspath(d) for d in cands]: cands.append(tgt)
P=[(int(np.load(own(d,"prices_1s.npz"))["T0"]),np.load(own(d,"composite_1s.npy"))) for d in cands]
T0=min(t for t,_ in P); T1=max(t+len(a) for t,a in P); A=np.full(T1-T0,np.nan)
for t,a in P:
    ok=np.isfinite(a); A[t-T0:t-T0+len(a)][ok]=a[ok]
os.makedirs(f"{tgt}/own",exist_ok=True)
for f in ("prices_1s.npz","composite_1s.npy"):
    if not os.path.exists(f"{tgt}/own/{f}"): shutil.copy(f"{tgt}/{f}",f"{tgt}/own/{f}")
Z=dict(np.load(f"{tgt}/own/prices_1s.npz")); np.savez_compressed(f"{tgt}/prices_1s.npz",T0=T0,cb=Z["cb"],kr=Z["kr"],bn=Z["bn"]); np.save(f"{tgt}/composite_1s.npy",A)
print(f"stitched {len(cands)} folders: {T1-T0} s of history, coverage {np.isfinite(A).mean():.3f}")
