# Market vs limit stop exits, recommended Equal-100 setup, Jun 1 - Oct 19 (news hour removed).
# Each stop event = all lots of a market stopped in the same second (backtest fills them at that second's VWAP, "model").
# Fills are simulated on the per-second Kalshi tape (data*/ktr): per second {side: [last, volume, notional]}.
#   market: sell into the other side's taker flow (it lifts our side's bids), we get PART of each second's volume at that second's
#           price until filled; leftover after 120 s at the worst price seen - 2c   (same as scripts/stop_fill.py)
#   limit L for W s: rest a sell at L = model + k c; each second our side's taker buys at avg >= L fill PART of their volume at L;
#           after W s the rest is sold at market as above, starting at t0+W.
# python3 -P scripts/limit_exit_study.py -> results/limit_exit.txt / .csv
import json,datetime as dt,numpy as np,pandas as pd
from zoneinfo import ZoneInfo
PT=ZoneInfo("America/Los_Angeles"); ET=ZoneInfo("America/New_York"); sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
SRC={"jh_eqrec_jun":"data_hist","jh_eqrec_jul":"data_hist","jh_eqrec_aug":"data_hist","v9_eqrec_66d":"data","v9_eqrec_recent":"data_recent","v9_eqrec_mon":"data_mon"}
E=[]
for f,d in SRC.items():
    x=pd.read_csv(f"results/{f}_entries.csv"); x["dir"]=d; E.append(x)
E=pd.concat(E).drop_duplicates(["ticker","entry_no"],keep="last")
E["o"]=pd.to_datetime(E.day+" 2026 "+E.window_pt,format="%a %b %d %Y %I:%M %p").dt.tz_localize(PT); E=E[E.o>="2026-06-01"]
E["ts"]=E.o.astype("int64")//10**9
C=pd.read_csv("data_news/calendar.csv"); NEWS={int(dt.datetime.fromisoformat(f"{a}T{b}").replace(tzinfo=ET).timestamp())+900*k
    for a,b,e in zip(C.date,C.time_et,C.event) if e in ("jobs","cpi","fomc") for k in range(4)}
E=E[~E.ts.isin(NEWS)]
S=E[E.exit=="stopped"].copy(); S["t"]=S.exit_time.map(sec)
ev=S.groupby(["ticker","t"]).apply(lambda g:pd.Series(dict(n=g.n.sum(),side=g.side.iloc[0],model=(g.exit_px*g.n).sum()/g.n.sum(),
     ts=g.ts.iloc[0],dir=g.dir.iloc[0],buy=(g.buy*g.n).sum()/g.n.sum(),day=g.o.iloc[0].date()))).reset_index()
PART=0.5; cache={}
def tape(tk,d):
    if tk not in cache:
        try: cache.clear() if len(cache)>300 else None; cache[tk]={int(k):v for k,v in json.load(open(f"{d}/ktr/{tk}.json")).items()}
        except FileNotFoundError: cache[tk]=None
    return cache[tk]
def market(K,o,t0,left,end,worst):
    cash=0.0
    for u in range(t0,min(t0+120,end)):
        a=K.get(u)
        if not a or a[o][1]<=0: continue
        p=1-a[o][2]/a[o][1]; worst=min(worst,p); q=min(left,PART*a[o][1]); cash+=q*p; left-=q
        if left<=1e-9: return cash
    return cash+left*max(worst-0.02,0.01)
VAR=[(k,w) for k in (0,1,2,3) for w in (3,5,10,30)]
rows=[]
for r in ev.itertuples():
    K=tape(r.ticker,r.dir)
    if K is None: continue
    s="y" if r.side.startswith("YES") else "n"; o="n" if s=="y" else "y"; t0=r.ts+r.t; end=r.ts+900
    res=dict(ticker=r.ticker,t=r.t,n=r.n,model=r.model,day=r.day,buy=r.buy,mkt=market(K,o,t0,r.n,end,r.model)/r.n)
    for k,w in VAR:
        L=r.model+k/100; left=r.n; cash=0.0
        for u in range(t0+1,min(t0+1+w,end)):
            a=K.get(u)
            if not a or a[s][1]<=0: continue
            if a[s][2]/a[s][1]>=L-1e-9:
                q=min(left,PART*a[s][1]); cash+=q*L; left-=q
                if left<=1e-9: break
        filled=r.n-left
        if left>1e-9: cash+=market(K,o,min(t0+1+w,end-1),left,end,r.model) if t0+1+w<end else left*0.0
        res[f"L{k}_{w}"]=cash/r.n; res[f"F{k}_{w}"]=filled/r.n
    rows.append(res)
D=pd.DataFrame(rows); D.to_csv("results/limit_exit.csv",index=False)
late=D.t>=780
out=[f"{len(D)} stop events, {D.n.sum():,.0f} contracts, {D.day.nunique()} days (recommended Equal 100, Jun 1 - Oct 19, news hour removed)",
     f"we get {PART:.0%} of each second's matching volume. Exit price = average c per contract stopped; $ = vs market sell.\n",
     f"{'exit':34s} {'avg exit':>8s} {'vs model':>8s} {'vs market':>9s} {'$ total':>9s} {'$/day':>7s} {'limit filled':>12s} | {'last 2 min only':>15s}",
     f"{'backtest model (1st-second VWAP)':34s} {np.average(D.model,weights=D.n)*100:7.2f}c {0:+7.2f}c"]
mk=np.average(D.mkt,weights=D.n)
out.append(f"{'market sell (size-limited)':34s} {mk*100:7.2f}c {(mk-np.average(D.model,weights=D.n))*100:+7.2f}c {0:+8.2f}c {0:+9,.0f}")
nd=D.day.nunique()
for k,w in VAR:
    c=f"L{k}_{w}"; v=np.average(D[c],weights=D.n); g=((D[c]-D.mkt)*D.n).sum(); fl=np.average(D[f"F{k}_{w}"],weights=D.n)
    gl=((D[c]-D.mkt)*D.n)[late].sum()
    out.append(f"{f'limit at stop price +{k}c, {w:>2d} s, then market':34s} {v*100:7.2f}c {(v-np.average(D.model,weights=D.n))*100:+7.2f}c {(v-mk)*100:+8.2f}c {g:+9,.0f} {g/nd:+7.1f} {fl*100:11.0f}% | {gl:+12,.0f}")
best=max(VAR,key=lambda kw:((D[f'L{kw[0]}_{kw[1]}']-D.mkt)*D.n).sum()); c=f"L{best[0]}_{best[1]}"; d=(D[c]-D.mkt)*D.n
out+=[f"\nbest: +{best[0]}c for {best[1]} s. Per event: better {np.mean(d>0)*100:.0f}%, worse {np.mean(d<0)*100:.0f}%. Worst event ${d.min():+,.0f}, best ${d.max():+,.0f}.",
      "By month ($ vs market): "+", ".join(f"{m}: {x:+,.0f}" for m,x in d.groupby(pd.to_datetime(D.day).dt.strftime('%b')).sum().items())]
open("results/limit_exit.txt","w").write("\n".join(out)); print("\n".join(out))
