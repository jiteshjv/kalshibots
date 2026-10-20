# Gold 2-day sweep filter: 48 CALENDAR hours (current) vs 48 TRADING hours (skips the weekend close Fri 21:00-Sun 22:00 UTC and the
# daily 21:00-22:00 UTC break). Same first-entry check as gold_fail.py (last 5 min ran through the prior high/low on our side).
# Totals use the sheet's "all filters" rules (2-day sweep, 7-9 AM PT, news windows).  python3 -P scripts/gold_sweep_trading.py [gn2]
import sys,json,numpy as np,pandas as pd,datetime as dt
from zoneinfo import ZoneInfo
PRE=sys.argv[1] if len(sys.argv)>1 else "gn2"; PT=ZoneInfo("America/Los_Angeles"); ET=ZoneInfo("America/New_York")
Z=np.load("data_gold/prices_1s.npz"); T0=int(Z["T0"]); P=np.load("data_gold/composite_1s.npy"); n=len(P)
t=T0+np.arange(n); wd=((t//86400)+3)%7; hr=(t%86400)//3600          # weekday Mon=0 (1970-01-01 was a Thursday)
open_=~((wd==5)|((wd==4)&(hr>=21))|((wd==6)&(hr<22))|(hr==21))
CUM=np.concatenate([[0],np.cumsum(open_)])                           # CUM[j] = open seconds before index j
def ext(a,b,d):
    a=max(a,0); seg=P[a:b]
    if b<=a or np.isnan(seg).all(): return np.nan
    return np.nanmax(seg) if d>0 else np.nanmin(seg)
def swept(i,d,k,trading):
    e=i-300
    s=int(np.searchsorted(CUM,CUM[e]-k)) if trading else e-k       # start so that [s,e) holds k open seconds
    lvl=ext(s,e,d); l5=ext(e,i+1,d)
    if lvl!=lvl or l5!=l5: return np.nan
    return int(d*(l5-lvl)>0)
sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
C=pd.read_csv("data_news/calendar.csv"); NEWS=set()
for d_,e_,t_ in zip(C.date,C.event,C.time_et):
    rel=int(dt.datetime.fromisoformat(f"{d_}T{t_}").replace(tzinfo=ET).timestamp())
    if e_ in ("jobs","cpi","claims"): NEWS|={rel+900*k for k in (-1,0,1,2,3)}
    if e_=="fomc": NEWS|={rel+900*k for k in (0,1,2,3)}
for v,lab in (("C","Version C (sheet default)"),("A","Version A")):
    M=pd.read_csv(f"results/{PRE}_{v}_markets.csv"); E=pd.read_csv(f"results/{PRE}_{v}_entries.csv").sort_values(["ticker","entry_no"])
    M["o"]=pd.to_datetime(M.day+" 2026 "+M.window_pt,format="%a %b %d %Y %I:%M %p").dt.tz_localize(PT); M["ts"]=M.o.astype("int64")//10**9; M=M.sort_values("o")
    first=E.groupby("ticker").first()
    cal=[];trd=[]
    for r in M.itertuples():
        g=first.loc[r.ticker]; d=1 if g.side.startswith("YES") else -1; i=r.ts+sec(g.entry_time)-1-T0
        ok=0<=i<n and P[i]==P[i]
        cal.append(swept(i,d,172800,False) if ok else np.nan); trd.append(swept(i,d,172800,True) if ok else np.nan)
    M["cal"]=cal; M["trd"]=trd
    base=(M.o.dt.hour>=7)&(M.o.dt.hour<9)|M.ts.isin(NEWS)
    wk=M.o.dt.dayofweek; early=(wk==6)|((wk==0)&(M.o.dt.hour<12))         # Sunday evening + Monday morning PT
    nd=M.o.dt.date.nunique()
    print(f"\n{lab}: {len(M)} markets, {nd} days")
    print(f"{'2-day sweep filter':28s} {'skipped':>7s} {'skip $':>8s} {'total':>9s} {'max DD':>8s} {'t/DD':>5s} | {'Sun eve + Mon AM total':>22s}")
    for nm,col in (("none (7-9 + news only)",None),("48 calendar hours (now)","cal"),("48 trading hours","trd")):
        sk=base|(M[col]==1) if col else base
        x=M[~sk]; c=x.pnl.cumsum(); dd=-(c-c.cummax()).min()
        print(f"{nm:28s} {int((M[col]==1).sum()) if col else 0:7d} {M[(M[col]==1)&~base].pnl.sum() if col else 0:+8,.0f} {x.pnl.sum():+9,.0f} {-dd:+8,.0f} {x.pnl.sum()/dd:5.1f} | {x[early[~sk]].pnl.sum():+22,.0f}")
    both=M[(M.cal!=M.trd)&~base]
    print(f"  markets where the two disagree: {len(both)} ({int((both.trd==1).sum())} newly skipped by trading hours, ${both[both.trd==1].pnl.sum():+,.0f}; "
          f"{int((both.cal==1).sum())} no longer skipped, ${both[both.cal==1].pnl.sum():+,.0f}); of those on Sun eve/Mon AM: {int(early[both.index].sum())}")
    by=M[~base].assign(m=M.o.dt.strftime("%m %b"))
    print("  by month, total with calendar vs trading hours: "+", ".join(f"{k[3:]} {g[g.cal!=1].pnl.sum():+,.0f} / {g[g.trd!=1].pnl.sum():+,.0f}" for k,g in by.groupby("m")))
