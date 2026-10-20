# "What Kalshi uses vs where BTC is": settlement = 60s average of BRTI over the last minute vs the official strike (floor_strike).
# In the last 2 minutes, compare (run from data/: python3 -P ../scripts/avg_gap.py):
#   naive = P(YES) from spot vs strike, as if settlement were the spot at close
#   avg   = P(YES) from the 60s average: part already locked in + spot for the rest, with the right variance
# Then: which one does the Kalshi price track, and does buying when avg-fair beats the ask by k (hold to settle) make money?
import json,math,os,datetime as dt
import numpy as np,pandas as pd
from multiprocessing import Pool
Z=np.load("prices_1s.npz"); T0=int(Z["T0"]); C=np.load("composite_1s.npy")
MODEL_ERR=4.0      # $ sd of composite vs official BRTI (calibration MAE ~$3)
def ts(s): return int(dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
def fee(p,n=5): return math.ceil(0.07*n*p*(1-p)*100-1e-9)/100/n
Phi=lambda z:0.5*(1+math.erf(z/math.sqrt(2)))
meta=sorted(json.load(open("settled.json")),key=lambda x:x["close_time"])
def one(x):
    f=f"ktr/{x['ticker']}.json"
    if x["result"] not in("yes","no") or not os.path.exists(f) or x.get("floor_strike") is None: return []
    cl=ts(x["close_time"]); op=cl-900; i0=op-T0
    if i0-1800<0 or i0+900>len(C): return []
    path=C[i0:i0+900]; pre=C[i0-1800:i0+780]
    if np.isnan(path).any() or np.isnan(pre).mean()>0.05: return []
    pre=pre[~np.isnan(pre)]; sig=np.std(np.diff(pre[::10]))/math.sqrt(10)       # $ per sqrt(second)
    K0=float(x["floor_strike"]); y=x["result"]=="yes"
    K={int(k):v for k,v in json.load(open(f)).items()}
    def buy(t,side):
        q=n=0
        for u in range(t,t+3):
            a=K.get(u)
            if a and a[side][1]>0: q+=a[side][1]; n+=a[side][2]
        return n/q if q else None
    lastyes=None; rows=[]
    for s in range(0,900):
        a=K.get(op+s)
        if a:
            q=a["y"][1]+a["n"][1]
            if q>0: lastyes=(a["y"][2]+(a["n"][1]-a["n"][2]))/q
        if s<780 or s>897: continue
        spot=path[s]; rem=900-s
        naive=Phi((spot-K0)/math.sqrt(sig**2*rem+MODEL_ERR**2))
        if s<840:
            E=spot; var=sig**2*((840-s)+60*61*121/6/3600)
        else:
            k=s-840+1; m=60-k; A=path[840:s+1].mean()
            E=(k*A+m*spot)/60; var=sig**2*m*(m+1)*(2*m+1)/6/3600
        fair=Phi((E-K0)/math.sqrt(var+MODEL_ERR**2))
        by=buy(op+s+1,"y"); bn=buy(op+s+1,"n")
        rows.append(dict(op=op,s=s,y=y,naive=naive,fair=fair,kyes=lastyes,ask_y=by,ask_n=bn,
                         spot_gap=spot-K0,avg_gap=E-K0,sig=sig))
    return rows
if __name__=="__main__":
    with Pool(16) as P: R=[r for rs in P.imap_unordered(one,meta,chunksize=20) for r in rs]
    D=pd.DataFrame(R).sort_values(["op","s"]).reset_index(drop=True)
    ops=np.sort(D.op.unique()); A,B=ops[int(.6*len(ops))],ops[int(.8*len(ops))]
    D["part"]=np.where(D.op<A,"train",np.where(D.op<B,"val","test"))
    days={p:D[D.part==p].op.nunique()/96 for p in ("train","val","test")}
    D.to_pickle("avg_gap.pkl")
    print(f"{D.op.nunique()} windows, {len(D)} rows (seconds 780-897)")
    # 1) calibration and what Kalshi tracks
    k=D.dropna(subset=["kyes"])
    print("\n1) Accuracy in the last 2 minutes (Brier, lower = better), by seconds left:")
    print(f"{'secs left':>10s} {'n':>7s} {'naive spot':>11s} {'60s-avg':>8s} {'Kalshi':>7s} | Kalshi tracks: corr(K,naive) corr(K,avg) | cases where they disagree by >10pts")
    for lo,hi in ((60,120),(30,60),(10,30),(3,10)):
        g=k[(900-k.s>lo)&(900-k.s<=hi)]
        b=lambda p:np.mean((p-g.y)**2)
        dis=g[(g.fair-g.naive).abs()>0.10]
        extra=""
        if len(dis)>20:
            extra=f"n={len(dis)}: Brier naive {np.mean((dis.naive-dis.y)**2):.3f} avg {np.mean((dis.fair-dis.y)**2):.3f} Kalshi {np.mean((dis.kyes-dis.y)**2):.3f}"
        print(f"{lo:4d}-{hi:<5d} {len(g):7d} {b(g.naive):11.4f} {b(g.fair):8.4f} {b(g.kyes):7.4f} |  {np.corrcoef(g.kyes,g.naive)[0,1]:.3f}  {np.corrcoef(g.kyes,g.fair)[0,1]:.3f}      | {extra}")
    # Kalshi vs both: regression of Kalshi logit on naive and avg logits where they disagree
    g=k[(k.s>=840)&((k.fair-k.naive).abs()>0.05)].copy()
    lg=lambda p:np.log(np.clip(p,1e-3,1-1e-3)/(1-np.clip(p,1e-3,1-1e-3)))
    X=np.c_[np.ones(len(g)),lg(g.naive),lg(g.fair)]; beta=np.linalg.lstsq(X,lg(g.kyes),rcond=None)[0]
    print(f"\n   In the last 60s where models disagree by >5pts (n={len(g)}): Kalshi logit = {beta[1]:.2f} x naive + {beta[2]:.2f} x avg  (weights)")
    # 2) trading: first second per window where avg-fair minus ask > k, hold to settle
    print("\n2) Buy the side where (60s-avg fair - ask) > k, first time per window, 1s latency, hold to settle, taker fees")
    for lo_s,hi_s,lab in ((780,840,"min 13-14 (before averaging starts)"),(840,898,"last 60s (averaging window)"),(780,898,"last 2 min")):
        print(f"  -- {lab}")
        for kk in (0.03,0.05,0.08,0.12,0.20):
            out=[]
            for op,g in D[(D.s>=lo_s)&(D.s<hi_s)].groupby("op",sort=False):
                for r in g.itertuples():
                    for side,ask,p in (("y",r.ask_y,r.fair),("n",r.ask_n,1-r.fair)):
                        if ask is None or ask!=ask or ask>=0.99: continue
                        if p-ask-fee(ask)>kk:
                            win=r.y if side=="y" else not r.y
                            out.append((r.part,(1 if win else 0)-ask-fee(ask),ask)); break
                    else: continue
                    break
            o=pd.DataFrame(out,columns=["part","pnl","ask"])
            s=" | ".join(f"{p}: {len(v)/days[p]:5.1f}/d @{v.ask.mean()*100:3.0f}c {v.pnl.mean()*100:+6.2f}c±{1.96*v.pnl.std()/math.sqrt(len(v))*100:.2f}" if len(v)>=10 else f"{p}: --" for p in ("train","val","test") for v in [o[o.part==p]])
            print(f"     k={kk:.2f}  {s}")
