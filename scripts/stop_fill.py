# Size-limited stop fills. her_trades.py sells a whole stop at the first second's VWAP (sell_fast, no size limit). Here each stop event
# (all lots stopped in the same second) is filled against the tape: per second we get only a share PART of the volume that hit our
# side's bid (taker buys of the other side), at that second's price, until filled; anything left after 120 s sells at the worst price seen.
import json,numpy as np,pandas as pd
from zoneinfo import ZoneInfo
PT=ZoneInfo("America/Los_Angeles"); sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
M=pd.read_csv("results/v8_mixed_f_66d_markets.csv"); E=pd.read_csv("results/v8_mixed_f_66d_entries.csv")
M["ts"]=pd.to_datetime(M.day+" 2026 "+M.window_pt,format="%a %b %d %Y %I:%M %p").dt.tz_localize(PT).astype("int64")//10**9
op=dict(zip(M.ticker,M.ts)); F=pd.read_csv("results/fast_vol_features.csv")[["ticker","r30"]]; r30=dict(zip(F.ticker,F.r30))
S=E[E.exit=="stopped"].copy(); S["t"]=S.exit_time.map(sec)
ev=S.groupby(["ticker","t"]).agg(n=("n","sum"),side=("side","first"),px=("exit_px",lambda x:0)).reset_index()
ev["px"]=S.groupby(["ticker","t"]).apply(lambda g:(g.exit_px*g.n).sum()/g.n.sum()).values
cache={}; out=[]
for r in ev.itertuples():
    if r.ticker not in cache: cache[r.ticker]={int(k):v for k,v in json.load(open(f"data/ktr/{r.ticker}.json")).items()}
    K=cache[r.ticker]; o="n" if r.side.startswith("YES") else "y"; t0=op[r.ticker]+r.t
    res={}
    for part in (1.0,0.5,0.25):
        left=r.n; cash=0; worst=r.px
        for u in range(t0,t0+120):
            a=K.get(u)
            if not a or a[o][1]<=0: continue
            p=1-a[o][2]/a[o][1]; worst=min(worst,p); q=min(left,part*a[o][1]); cash+=q*p; left-=q
            if left<=1e-9: break
        cash+=left*max(worst-0.02,0.01); res[part]=cash/r.n
    out.append(dict(ticker=r.ticker,n=r.n,model=r.px,**{f"p{int(k*100)}":v for k,v in res.items()},r30=r30.get(r.ticker,np.nan)))
D=pd.DataFrame(out); D.to_csv("results/stop_fill.csv",index=False)
fast=D.r30>F.r30.quantile(.9)
print(f"{len(D)} stop events (all lots stopped in one second), {D.n.sum():,.0f} contracts, median size {D.n.median():.0f}, 90th pct {D.n.quantile(.9):.0f}\n")
print(f"{'':28s} {'events':>6s} | {'backtest fill':>13s} | {'we get 100% of prints':>21s} | {'50% of prints':>13s} | {'25% of prints':>13s}")
for lab,m in (("all",D.n>0),("calm (other 90%)",~fast),("fast (30-min range top 10%)",fast)):
    x=D[m]; w=lambda c:(x[c]*x.n).sum()/x.n.sum()
    print(f"{lab:28s} {len(x):6d} | {w('model')*100:12.1f}c | "+" | ".join(f"{w(c)*100:6.1f}c ({(w(c)-w('model'))*100:+.1f}c)" for c in ("p100","p50","p25")))
print("\nExtra cost vs the backtest ($, whole 66 days):"+"".join(f"  {c}: ${((D[c]-D.model)*D.n).sum():+,.0f}" for c in ("p100","p50","p25")))
for lab,m in (("by stop size: <=1000",D.n<=1000),("1000-3000",(D.n>1000)&(D.n<=3000)),(">3000",D.n>3000)):
    x=D[m]; print(f"  {lab:22s} {len(x):5d} events: 50%-of-prints costs {((x.p50-x.model)*x.n).sum()/x.n.sum()*100:+.1f}c per contract")
x=D[D.ticker.str.contains("26SEP1614[34]")]; print("\nFOMC Sep 16 stops:"); print(x.round(3).to_string(index=False))
