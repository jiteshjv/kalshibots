# Stop fills when 4 accounts (or one at 4x) stop out in the same second: every stop event x4 (full position 5,000 -> 20,000).
# Same tape walk as stop_fill.py; also time to fill and how far the price walked.
import json,numpy as np,pandas as pd
from zoneinfo import ZoneInfo
PT=ZoneInfo("America/Los_Angeles"); sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
M=pd.read_csv("results/v8_mixed_f_66d_markets.csv"); E=pd.read_csv("results/v8_mixed_f_66d_entries.csv")
M["ts"]=pd.to_datetime(M.day+" 2026 "+M.window_pt,format="%a %b %d %Y %I:%M %p").dt.tz_localize(PT).astype("int64")//10**9
op=dict(zip(M.ticker,M.ts)); F=pd.read_csv("results/fast_vol_features.csv")[["ticker","r30"]]; r30=dict(zip(F.ticker,F.r30)); fq=F.r30.quantile(.9)
S=E[E.exit=="stopped"].copy(); S["t"]=S.exit_time.map(sec); S["v"]=S.exit_px*S.n
ev=S.groupby(["ticker","t"]).agg(n=("n","sum"),v=("v","sum"),side=("side","first")).reset_index(); ev["px"]=ev.v/ev.n
cache={}; out=[]
for r in ev.itertuples():
    if r.ticker not in cache: cache[r.ticker]={int(k):v for k,v in json.load(open(f"data/ktr/{r.ticker}.json")).items()}
    K=cache[r.ticker]; o="n" if r.side.startswith("YES") else "y"; t0=op[r.ticker]+r.t; row=dict(ticker=r.ticker,n=r.n,model=r.px,fast=r30.get(r.ticker,0)>fq)
    vol10=sum(K[u][o][1] for u in range(t0,t0+10) if u in K); row["vol10"]=vol10
    for mult in (1,4):
        for part in (1.0,0.5,0.25):
            left=r.n*mult; cash=0; worst=r.px; secs=120
            for u in range(t0,t0+120):
                a=K.get(u)
                if not a or a[o][1]<=0: continue
                p=1-a[o][2]/a[o][1]; worst=min(worst,p); q=min(left,part*a[o][1]); cash+=q*p; left-=q
                if left<=1e-9: secs=u-t0; break
            unf=left; cash+=left*max(worst-0.02,0.01)
            row[f"x{mult}_{int(part*100)}"]=cash/(r.n*mult); row[f"s{mult}_{int(part*100)}"]=secs; row[f"u{mult}_{int(part*100)}"]=unf/(r.n*mult)
    out.append(row)
D=pd.DataFrame(out); D.to_csv("results/stop_fill20k.csv",index=False)
w=lambda x,c:(x[c]*x.n).sum()/x.n.sum()
print(f"{len(D)} stop events. Volume that hit our bid in the 10 s after a stop: median {D.vol10.median():,.0f} contracts, 10th pct {D.vol10.quantile(.1):,.0f}")
print(f"\n{'avg stop fill (cents)':30s} {'backtest':>8s} | {'1 acct, 50%':>11s} | {'4x, 100%':>14s} | {'4x, 50%':>14s} | {'4x, 25%':>14s}")
for lab,m in (("all stops",D.n>0),("calm markets",~D.fast),("fast markets",D.fast),("big stops (>=2,000 at 1x)",D.n>=2000),("big stops in fast markets",(D.n>=2000)&D.fast)):
    x=D[m]; b=w(x,"model")
    print(f"{lab:30s} {b*100:8.1f} | {(w(x,'x1_50')-b)*100:+10.1f}c | "+" | ".join(f"{(w(x,c)-b)*100:+6.1f}c ({x[c.replace('x','s')].median():.0f}s)" for c in ("x4_100","x4_50","x4_25")))
tot=lambda c:((D[c]-D.model)*D.n*4).sum()
print(f"\nCost at 4x size vs the backtest (dollars for all 4 accounts together, 66 days): 100% ${tot('x4_100'):+,.0f} | 50% ${tot('x4_50'):+,.0f} | 25% ${tot('x4_25'):+,.0f}")
print(f"  (4 accounts' backtest profit together ~ 4 x $167K = $668K; per account: 50% case ${tot('x4_50')/4:+,.0f})")
print(f"Stops still not filled after 2 min at 4x/25%: {(D.u4_25>0).mean()*100:.1f}% of events")
x=D[D.ticker.str.contains("26SEP1614[34]")]; print("\nFOMC Sep 16 at 4x:"); print(x[["ticker","n","model","x4_100","x4_50","x4_25","s4_50"]].assign(n=x.n*4).round(3).to_string(index=False))
