# How much worse do stops get at 10x size? Recommended + fair-value stop (Equal 100), Jun 1 - Oct 19, news hour removed.
# Each stop event (lots stopped in one second) is re-sold on the per-second tape: each second we take PART of the volume that hits our
# side's bid (other side's taker buys) at that second's price, until filled; leftover after 120 s at the worst price seen - 2c.
# Our own selling is assumed not to move the price (so 10x results are optimistic).  python3 -P scripts/crowd_exit.py
import json,datetime as dt,numpy as np,pandas as pd
from zoneinfo import ZoneInfo
PT=ZoneInfo("America/Los_Angeles"); ET=ZoneInfo("America/New_York"); sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
SRC={"jh_fvs2_jun":"data_hist","jh_fvs2_jul":"data_hist","jh_fvs2_aug":"data_hist","v9_fvs2_66d":"data","v9_fvs2_recent":"data_recent","v9_fvs2_mon":"data_mon"}
E=pd.concat([pd.read_csv(f"results/{f}_entries.csv").assign(dir=d) for f,d in SRC.items()]).drop_duplicates(["ticker","entry_no"],keep="last")
M=pd.concat([pd.read_csv(f"results/{f}_markets.csv") for f in SRC]).drop_duplicates("ticker",keep="last")
for X in (E,M): X["o"]=pd.to_datetime(X.day+" 2026 "+X.window_pt,format="%a %b %d %Y %I:%M %p").dt.tz_localize(PT); X["ts"]=X.o.astype("int64")//10**9
C=pd.read_csv("data_news/calendar.csv"); NEWS={int(dt.datetime.fromisoformat(f"{a}T{b}").replace(tzinfo=ET).timestamp())+900*k
    for a,b,e in zip(C.date,C.time_et,C.event) if e in ("jobs","cpi","fomc") for k in range(4)}
E=E[(E.o>="2026-06-01")&~E.ts.isin(NEWS)]; M=M[(M.o>="2026-06-01")&~M.ts.isin(NEWS)]
S=E[E.exit=="stopped"].copy(); S["t"]=S.exit_time.map(sec)
ev=S.groupby(["ticker","t"]).agg(n=("n","sum"),side=("side","first"),ts=("ts","first"),dir=("dir","first"),px=("exit_px","mean")).reset_index()
cache={}
def tape(tk,d):
    if tk not in cache:
        if len(cache)>400: cache.clear()
        try: cache[tk]={int(k):v for k,v in json.load(open(f"{d}/ktr/{tk}.json")).items()}
        except FileNotFoundError: cache[tk]=None
    return cache[tk]
def sell(K,o,t0,end,q,part):
    left=q; cash=0.0; worst=None; last=t0
    for u in range(t0,min(t0+120,end)):
        a=K.get(u)
        if not a or a[o][1]<=0: continue
        p=1-a[o][2]/a[o][1]; worst=p if worst is None else min(worst,p); take=min(left,part*a[o][1]); cash+=take*p; left-=take; last=u
        if left<=1e-9: return cash/q,last-t0,0
    w=worst if worst is not None else 0.01
    return (cash+left*max(w-0.02,0.01))/q,last-t0,left
R=[]
for r in ev.itertuples():
    K=tape(r.ticker,r.dir)
    if K is None: continue
    o="n" if r.side.startswith("YES") else "y"; t0=r.ts+r.t; end=r.ts+900; row=dict(ticker=r.ticker,n=r.n,t=r.t)
    for k in (1,10):
        for part in (1.0,0.5):
            p,dur,left=sell(K,o,t0,end,r.n*k,part); row[f"p{k}_{int(part*100)}"]=p; row[f"d{k}_{int(part*100)}"]=dur; row[f"l{k}_{int(part*100)}"]=left
    R.append(row)
D=pd.DataFrame(R); D.to_csv("results/crowd_exit.csv",index=False)
mk=D.groupby("ticker").apply(lambda g:pd.Series({**{c:np.average(g[c],weights=g.n) for c in D.columns if c[0]=="p"},"n":g.n.sum(),"d10_50":g.d10_50.max(),"d1_50":g.d1_50.max(),"l10_50":g.l10_50.sum()}),include_groups=False)
mk=mk.join(M.set_index("ticker")[["pnl","day","window_pt"]]); worst=mk.sort_values("pnl").head(25)
cost=lambda x,a,b:((x[b]-x[a])*x.n).sum()      # $ change at 1x size; x10 for the 10x position
print(f"{len(D):,} stop events in {D.ticker.nunique():,} markets, {D.n.sum()/1e6:.2f}M contracts (1x).  Taking 50% of each second's matching volume:\n")
print(f"{'':34s} {'1x exit':>8s} {'10x exit':>9s} {'10x worse by':>12s} {'10x extra cost':>14s} {'secs to fill 1x/10x':>20s}")
def line(lab,x):
    a=np.average(x.p1_50,weights=x.n); b=np.average(x.p10_50,weights=x.n)
    print(f"{lab:34s} {a*100:7.1f}c {b*100:8.1f}c {(a-b)*100:11.1f}c {-cost(x,'p1_50','p10_50')*10:+14,.0f} {np.median(x.d1_50):9.0f} / {np.median(x.d10_50):.0f} (median)")
line("all stops",D); big=D.ticker.isin(worst.index); line("25 biggest-loss markets",D[big])
for lo,hi,l in ((0,500,"stops of < 500 contracts (1x)"),(500,2000,"500-2,000"),(2000,1e9,"2,000+")): line(l,D[(D.n>=lo)&(D.n<hi)])
print(f"\nP&L impact at 10x size (all stops, 50%): 1x total P&L ${M.pnl.sum():+,.0f} -> 10x would be ${M.pnl.sum()*10:+,.0f} minus ${-cost(D,'p1_50','p10_50')*10:,.0f} extra exit cost"
      f" = ${M.pnl.sum()*10+cost(D,'p1_50','p10_50')*10:+,.0f}  (taking 100%: minus ${-cost(D,'p1_100','p10_100')*10:,.0f})")
print("\n25 biggest-loss markets (1x P&L; exit price at 1x and 10x, 50% of volume; seconds to finish the 10x sale):")
for tk,x in worst.iterrows():
    print(f"  {x.day} {x.window_pt:>8s}  1x ${x.pnl:+7,.0f}  {x.n:5,.0f} stopped  exit 1x {x.p1_50*100:5.1f}c  10x {x.p10_50*100:5.1f}c ({(x.p10_50-x.p1_50)*100:+5.1f}c, ${(x.p10_50-x.p1_50)*x.n*10:+8,.0f})  10x took {x.d10_50:3.0f}s"+(f"  unsold {x.l10_50:,.0f}" if x.l10_50>0 else ""))
