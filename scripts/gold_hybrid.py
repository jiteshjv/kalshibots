# Fill gaps in the Dukascopy gold price (data_gold/composite_1s.npy) with Binance PAXGUSDT 1 s closes scaled to the Kalshi/Pyth level
# (trailing median strike/PAXG over the previous 20 markets, no look-ahead). Reports both sources' accuracy vs the official settle.
# Run from data_gold/: python3 -P ../scripts/gold_hybrid.py   (backs up the Dukascopy-only files to *_duka.*)
import json,glob,zipfile,csv,io,os,shutil,datetime as dt,numpy as np
tsof=lambda s:dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()
M=json.load(open("settled.json")); Z=np.load("prices_1s.npz"); T0=int(Z["T0"]); comp=np.load("composite_1s.npy"); n=len(comp)
if not os.path.exists("composite_1s_duka.npy"): shutil.copy("composite_1s.npy","composite_1s_duka.npy"); shutil.copy("prices_1s.npz","prices_1s_duka.npz")
else: comp=np.load("composite_1s_duka.npy")
px=np.full(n,np.nan)
for f in sorted(glob.glob("bn/PAXGUSDT-1s-*.zip")):
    z=zipfile.ZipFile(f)
    for r in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
        t=int(r[0]); t=t//1000000 if t>1e14 else t//1000; i=t-T0
        if 0<=i<n: px[i]=float(r[4])
idx=np.where(~np.isnan(px),np.arange(n),0); np.maximum.accumulate(idx,out=idx); age=np.arange(n)-idx; px=px[idx]; px[age>120]=np.nan
rat=[]; scale=np.full(n,np.nan); prev=0; cur=np.nan
for x in M:
    i=int(tsof(x["close_time"]))-900-T0
    if not x.get("floor_strike") or not (0<=i<n): continue
    scale[prev:i]=cur; v=px[i-30:i+30]; v=v[~np.isnan(v)]
    if len(v): rat.append(float(x["floor_strike"])/np.median(v))
    cur=np.median(rat[-20:]) if rat else np.nan; prev=i
scale[prev:]=cur; pax=px*scale
def acc(a,sel):
    e=[];ok=[]
    for x in M:
        cl=int(tsof(x["close_time"]))-T0
        if sel(cl) and x.get("expiration_value") and x.get("floor_strike") and 0<cl<n and a[cl-1]==a[cl-1]:
            e.append(a[cl-1]-float(x["expiration_value"])); ok.append((a[cl-1]>=float(x["floor_strike"]))==(x["result"]=="yes"))
    e=np.abs(e); return f"{len(ok)} mkts, median err ${np.median(e):.2f}, p90 ${np.percentile(e,90):.2f}, outcome match {np.mean(ok)*100:.1f}%"
both=lambda cl:comp[cl-1]==comp[cl-1]
print("Dukascopy (overlap):",acc(comp,both)); print("PAXG      (overlap):",acc(pax,both))
gap=np.isnan(comp); out=np.where(gap,pax,comp)
print("PAXG fills",int((gap&~np.isnan(pax)).sum()),"s; after fill coverage",f"{np.mean(~np.isnan(out)):.3f}"); print("PAXG (gap only):",acc(pax,lambda cl:not both(cl)))
ok_=np.where(~np.isnan(out))[0]; print("price now ends", dt.datetime.fromtimestamp(T0+ok_[-1],dt.timezone.utc))
np.save("composite_1s.npy",out); np.savez_compressed("prices_1s.npz",T0=T0,cb=out,kr=out,bn=out)
