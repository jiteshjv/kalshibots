# What did the BTC chart look like at the first entry of markets that went bad (first side fully stopped / reversal) vs the rest?
# Features at the first entry second t (signed so + = in the direction we bought): momentum over 1m..4h, position in the 2h range,
# distance from the 1h average, strike crossings so far in the window, 5-min realized volatility, 1h trend agreement.
# (run from project root: python3 -P scripts/bad_entries.py)
import json,numpy as np,pandas as pd,datetime as dt
Z=np.load("data/prices_1s.npz"); T0=int(Z["T0"]); P=np.load("data/composite_1s.npy")
meta={x["ticker"]:x for x in json.load(open("data/settled.json"))}
M=pd.read_csv("results/her_66d_markets.csv"); E=pd.read_csv("results/her_66d_entries.csv").sort_values(["ticker","entry_no"])
first=E.groupby("ticker").first(); 
sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
rows=[]
for r in M.itertuples():
    x=meta.get(r.ticker); f=first.loc[r.ticker] if r.ticker in first.index else None
    if x is None or f is None: continue
    op=int(dt.datetime.fromisoformat(x["close_time"].replace("Z","+00:00")).timestamp())-900; K0=float(x["floor_strike"])
    t=op+sec(f.entry_time)-1; i=t-T0; d=1 if f.side.startswith("YES") else -1
    if i<14400 or i>=len(P): continue
    p=P[i]
    if p!=p: continue
    mom=lambda k:d*(p-P[i-k])/p*100
    seg=P[i-7200:i]; hi,lo=np.nanmax(seg),np.nanmin(seg); pos=(p-lo)/(hi-lo) if hi>lo else .5
    w=P[op-T0:i+1]; cr=int(np.sum(np.diff(np.sign(w-K0))!=0))
    rv=np.nanstd(np.diff(P[i-300:i]))
    first_leg=E[(E.ticker==r.ticker)&(E.side==f.side)]
    bad=bool((first_leg.exit=="stopped").all())
    rows.append(dict(ticker=r.ticker,op=op,pnl=r.pnl,bad=bad,rolled="then" in r.side,min=sec(f.entry_time)/60,
        m1=mom(60),m5=mom(300),m15=mom(900),m30=mom(1800),m60=mom(3600),m240=mom(14400),
        pos2h=pos if d>0 else 1-pos, vs_ma60=d*(p-np.nanmean(P[i-3600:i]))/p*100, crosses=cr, rv5=rv,
        trend_agree=int(np.sign(P[i]-P[i-3600])==d)))
D=pd.DataFrame(rows); D.to_csv("results/bad_entries_features.csv",index=False)
ops=np.sort(D.op.unique()); A,B=ops[int(.6*len(ops))],ops[int(.8*len(ops))]; D["part"]=np.where(D.op<A,"train",np.where(D.op<B,"val","test"))
print(f"{len(D)} markets; bad (first side fully stopped) {D.bad.mean()*100:.1f}%; they lost ${D[D.bad].pnl.sum():,.0f}, the rest made ${D[~D.bad].pnl.sum():+,.0f}\n")
print(f"{'feature at first entry':34s} {'bad mkts':>9s} {'good mkts':>9s}")
lab={"m1":"BTC move last 1 min (%, our way)","m5":"last 5 min","m15":"last 15 min","m30":"last 30 min","m60":"last 60 min","m240":"last 4 h",
     "pos2h":"place in 2h range (1 = at extreme our way)","vs_ma60":"vs 1h average (%, our way)","crosses":"strike crosses so far","rv5":"5-min volatility ($/s)",
     "trend_agree":"1h trend same way (share)","min":"minute of first entry"}
for k,l in lab.items(): print(f"{l:44s} {D[D.bad][k].mean():9.3f} {D[~D.bad][k].mean():9.3f}")
print("\nMarket P&L by feature fifth (train | val | test, $ per market):")
for k in ("m5","m15","m60","m240","pos2h","vs_ma60","crosses","rv5"):
    q=pd.qcut(D[k].rank(method="first"),5,labels=["low","2","3","4","high"])
    print(f"  {lab[k][:40]:40s}",end="")
    for g in ["low","2","3","4","high"]:
        x=D[q==g]; print(f" | {g:4s} bad {x.bad.mean()*100:3.0f}% ${x.pnl.mean():+5.0f} ({'/'.join(f'{x[x.part==p].pnl.mean():+.0f}' for p in ('train','val','test'))})",end="")
    print()
