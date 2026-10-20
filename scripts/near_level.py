# Markets that passed the filters (mixed, filters on): how far was the 2-day high/low (our side) at the first entry, in ATR units, and did BTC
# break it later inside the market while we were adding entries? ATR = mean 15-min high-low over the prior 24 h.
# (run from project root: python3 -P scripts/near_level.py)
import numpy as np,pandas as pd,json,datetime as dt
Z=np.load("data/prices_1s.npz"); T0=int(Z["T0"]); P=np.load("data/composite_1s.npy")
meta={x["ticker"]:x for x in json.load(open("data/settled.json"))}
M=pd.read_csv("results/herF_66d_markets.csv"); E=pd.read_csv("results/herF_66d_entries.csv").sort_values(["ticker","entry_no"])
sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
ops=np.sort([int(dt.datetime.fromisoformat(meta[t]["close_time"].replace("Z","+00:00")).timestamp())-900 for t in M.ticker]); A,B=ops[int(.6*len(ops))],ops[int(.8*len(ops))]
rows=[];ent_rows=[]
for t,g in E.groupby("ticker"):
    x=meta[t]; op=int(dt.datetime.fromisoformat(x["close_time"].replace("Z","+00:00")).timestamp())-900
    g1=g[g.side==g.side.iloc[0]]; d=1 if g1.side.iloc[0].startswith("YES") else -1
    t1=op+sec(g1.entry_time.iloc[0])-1; i=t1-T0
    if i-2*86400-300<0: continue
    seg=P[i-2*86400-300:i-300]; L=np.nanmax(seg) if d>0 else np.nanmin(seg)
    bars=P[i-86400:i].reshape(-1,900); atr=np.nanmean(np.nanmax(bars,1)-np.nanmin(bars,1))
    dist=d*(L-P[i])/atr                                                    # ATRs still to go to the level
    path=P[i:op-T0+900]; br=np.nonzero(d*(path-L)>0)[0]
    brk=int(br[0]) if len(br) else None                                    # seconds after first entry that the level broke
    in_window=brk is not None and (t1+brk)<op+720
    side_won=(x["result"]=="yes")==(d>0)
    rows.append(dict(ticker=t,op=op,pnl=M[M.ticker==t].pnl.sum(),bad=bool((g1.exit=="stopped").all()),dist=dist,broke=in_window,
                     broke_s=brk,side_won=side_won,part="train" if op<A else ("val" if op<B else "test")))
    if in_window:
        for e in g1.itertuples():
            ent_rows.append(dict(after=(op+sec(e.entry_time)-1)>=t1+brk,pnl=e.pnl,stopped=e.exit=="stopped",buy=e.buy))
D=pd.DataFrame(rows); X=pd.DataFrame(ent_rows)
print(f"{len(D)} traded markets (filters on). ATR-distance to the 2-day level at the first entry:\n")
print(f"{'distance to 2-day high/low':30s} {'mkts':>5s} {'broke it later':>14s} {'bad':>4s} {'$/mkt':>6s} | train/val/test")
for lo,hi,lab in ((-9,0.25,"< 0.25 ATR"),(0.25,0.5,"0.25-0.5 ATR"),(0.5,1,"0.5-1 ATR"),(1,2,"1-2 ATR"),(2,4,"2-4 ATR"),(4,99,"> 4 ATR")):
    x=D[(D.dist>=lo)&(D.dist<hi)]
    print(f"{lab:30s} {len(x):5d} {x.broke.mean()*100:13.0f}% {x.bad.mean()*100:3.0f}% {x.pnl.mean():+6.0f} | "+"/".join(f"{x[x.part==p].pnl.mean():+.0f}" for p in ("train","val","test")))
b=D[D.broke]; n=D[~D.broke]
print(f"\nBroke the 2-day level inside the market (before min 12): {len(b)} mkts, bad {b.bad.mean()*100:.0f}%, ${b.pnl.mean():+.0f}/mkt (total ${b.pnl.sum():+,.0f}); side still won {b.side_won.mean()*100:.0f}%")
print(f"Did not break it: {len(n)} mkts, bad {n.bad.mean()*100:.0f}%, ${n.pnl.mean():+.0f}/mkt")
if len(X):
    for k,lab in ((False,"entries BEFORE the break"),(True,"entries AFTER the break")):
        y=X[X.after==k]; print(f"  {lab:26s}: {len(y):5d} entries, {y.stopped.mean()*100:3.0f}% stopped, ${y.pnl.mean():+.2f} avg, total ${y.pnl.sum():+,.0f}, avg buy {y.buy.mean()*100:.1f}c")
D.to_csv("results/near_level.csv",index=False)
