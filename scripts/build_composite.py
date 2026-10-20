# Build per-second price arrays (Coinbase, Kraken, Binance) and save to data/prices_1s.npz (run from data/).
import json,glob,zipfile,io,csv,datetime as dt,numpy as np
T0=int(dt.datetime(2026,8,9,22,0,tzinfo=dt.timezone.utc).timestamp()); T1=int(dt.datetime(2026,10,15,23,0,tzinfo=dt.timezone.utc).timestamp())
n=T1-T0
def blank(): return np.zeros(n),np.zeros(n)
def load_agg(files):
    pv,v=blank()
    for f in files:
        for k,(a,b,_) in json.load(open(f)).items():
            i=int(k)-T0
            if 0<=i<n: pv[i]+=a; v[i]+=b
    return pv,v
def ffill(pv,v):
    p=np.where(v>0,pv/np.where(v>0,v,1),np.nan)
    idx=np.where(~np.isnan(p),np.arange(n),0); np.maximum.accumulate(idx,out=idx)
    out=p[idx]; age=np.arange(n)-idx; return out,age
cbpv,cbv=load_agg(glob.glob("cb/[0-9]*.json")); krpv,krv=load_agg(glob.glob("kr/*.json"))
cb,cbage=ffill(cbpv,cbv); kr,krage=ffill(krpv,krv)
bn=np.full(n,np.nan); bnv=np.zeros(n)
for f in sorted(glob.glob("bn/*.zip")):
    z=zipfile.ZipFile(f)
    for r in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
        t=int(r[0]); t=t//1000000 if t>1e14 else t//1000; i=t-T0
        if 0<=i<n: bn[i]=float(r[4]); bnv[i]=float(r[5])
for t,c in json.load(open("bn/today.json")):
    i=t//1000-T0
    if 0<=i<n: bn[i]=float(c)
idx=np.where(~np.isnan(bn),np.arange(n),0); np.maximum.accumulate(idx,out=idx); bn=bn[idx]
np.savez_compressed("prices_1s.npz",T0=T0,cb=cb,kr=kr,bn=bn,cbv=cbv,krv=krv,bnv=bnv,cbage=cbage,krage=krage)
print("seconds",n,"| coverage (non-nan): cb %.4f kr %.4f bn %.4f"%tuple(np.mean(~np.isnan(a)) for a in (cb,kr,bn)))
print("median seconds since last trade: cb",np.median(cbage),"kr",np.median(krage),"| share of seconds with a trade: cb %.2f kr %.2f"%(np.mean(cbv>0),np.mean(krv>0)))
