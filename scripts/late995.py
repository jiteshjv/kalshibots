# Buy the leading side very late (T seconds before close) if it trades at >= P, hold to settlement. Real taker prints, taker fee per 1,000-contract
# order. (run from project root: python3 -P scripts/late995.py)
import json,math,numpy as np,pandas as pd,datetime as dt,os
fee=lambda p,n:math.ceil(0.07*n*p*(1-p)*100-1e-9)/100
meta=[x for x in json.load(open("data/settled.json")) if x["result"] in ("yes","no") and os.path.exists(f"data/ktr/{x['ticker']}.json")]
rows=[]
for x in meta:
    K={int(k):v for k,v in json.load(open(f"data/ktr/{x['ticker']}.json")).items()}
    cl=int(dt.datetime.fromisoformat(x["close_time"].replace("Z","+00:00")).timestamp()); y=x["result"]=="yes"
    for T in (5,10,20,30,60,120):
        t=cl-T; best=None
        for s in ("y","n"):
            q=n=0
            for u in range(t,t+2):
                a=K.get(u)
                if a and a[s][1]>0: q+=a[s][1]; n+=a[s][2]
            if q: px=n/q; best=max(best,(px,s,q),key=lambda z:z[0]) if best else (px,s,q)
        if best: rows.append(dict(op=cl-900,T=T,px=best[0],side=best[1],vol=best[2],win=(best[1]=="y")==y))
D=pd.DataFrame(rows); ops=np.sort(D.op.unique()); A,B=ops[int(.6*len(ops))],ops[int(.8*len(ops))]; D["part"]=np.where(D.op<A,"train",np.where(D.op<B,"val","test"))
N=1000; D["pnl"]=[(N if w else 0)-N*p-fee(p,N) for p,w in zip(D.px,D.win)]
days=D.op.nunique()/96
print(f"{D.op.nunique()} markets ({days:.0f} days). Buy 1,000 of the leading side at the real price T s before close, hold to settlement.\n")
print(f"{'T before close':>14s} {'buy at':>8s} {'mkts':>5s} {'avg px':>7s} {'win%':>7s} {'need':>6s} | {'$/trade':>8s} {'$/day':>7s} | train/val/test $/day | worst | 2s volume")
for T in (5,10,20,30,60,120):
    for lo,hi in ((0.99,0.995),(0.995,0.999),(0.99,0.999)):
        x=D[(D["T"]==T)&(D.px>=lo)&(D.px<hi)]
        if len(x)<20: continue
        need=(x.px.mean()+fee(x.px.mean(),N)/N)*100
        pd_={p:x[x.part==p].pnl.sum()/(D[D.part==p].op.nunique()/96) for p in ("train","val","test")}
        print(f"{T:>12d} s {lo*100:5.1f}-{hi*100:4.1f}c {len(x):5d} {x.px.mean()*100:6.2f}c {x.win.mean()*100:6.2f}% {need:5.2f}% | {x.pnl.mean():+8.2f} {x.pnl.sum()/days:+7.0f} | "
              +"/".join(f"{v:+.0f}" for v in pd_.values())+f" | {x.pnl.min():+,.0f} | {x.vol.median():,.0f}")
