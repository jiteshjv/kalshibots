# News days: (1) verify data_news/calendar.csv against 1 s prices (move in the 60 s after the release vs the same clock time on other weekdays),
# (2) P&L of the windows around each release (gold gc_A, BTC v8_mixed_f) vs the same windows on normal weekdays.
import numpy as np,pandas as pd,datetime as dt
from zoneinfo import ZoneInfo
ET=ZoneInfo("America/New_York"); PT=ZoneInfo("America/Los_Angeles")
C=pd.read_csv("data_news/calendar.csv")
C["ts"]=[int(dt.datetime.fromisoformat(f"{d}T{t}").replace(tzinfo=ET).timestamp()) for d,t in zip(C.date,C.time_et)]
def load(folder):
    Z=np.load(f"{folder}/prices_1s.npz"); return int(Z["T0"]),np.load(f"{folder}/composite_1s.npy")
def jump(T0,P,ts):
    a,b=ts-10-T0,ts+60-T0
    if a<0 or b>=len(P) or P[a]!=P[a] or P[b]!=P[b]: return np.nan
    return abs(P[b]/P[a]-1)*100
for name,folder in (("gold","data_gold"),("BTC","data_recent")):
    T0,P=load(folder); print(f"\n== {name}: move from 10 s before to 60 s after the release (%)")
    for ev,g in C.groupby("event"):
        j=[jump(T0,P,t) for t in g.ts]; j=[x for x in j if x==x]
        if not j: continue
        base=[]
        for t in g.ts:
            for k in range(-10,11):
                tt=t+k*86400; d=dt.datetime.fromtimestamp(tt,ET)
                if k and d.weekday()<5 and d.strftime("%F") not in set(C.date): base.append(jump(T0,P,tt))
        base=[x for x in base if x==x]
        print(f"   {ev:8s} {len(j):2d} days: median {np.median(j):.3f}% vs normal days {np.median(base):.3f}% ({np.median(j)/np.median(base):.1f}x)")
def mk(f):
    M=pd.concat([pd.read_csv(x) for x in f]).drop_duplicates("ticker")
    M["o"]=pd.to_datetime(M.day+" 2026 "+M.window_pt,format="%a %b %d %Y %I:%M %p").dt.tz_localize(PT); M["ts"]=M.o.astype("int64")//10**9; return M
for name,f in (("gold (Version A, no filters)",["results/gc_A_markets.csv"]),("BTC (mixed, sweep filters)",["results/v8_mixed_f_66d_markets.csv","results/v8_mixed_f_mon_markets.csv"])):
    M=mk(f); M["date"]=M.o.dt.strftime("%F"); M["clock"]=M.o.dt.strftime("%H:%M")
    print(f"\n== {name}: $ per market on news days vs other weekdays, same window")
    for lab,evs,wins in (("8:30 ET data (jobs/CPI/PPI/GDP/retail)",["jobs","cpi","ppi","gdp_pce","retail"],["05:00","05:15","05:30","05:45","06:00"]),("FOMC 2 PM ET (11 AM PT)",["fomc"],["10:45","11:00","11:15","11:30","11:45","12:00"])):
        days=set(C[C.event.isin(evs)].date); wk=M.o.dt.weekday<5
        print("  ",lab, "| news days in data:",len(days&set(M.date)))
        for w in wins:
            a=M[(M.clock==w)&M.date.isin(days)]; b=M[(M.clock==w)&~M.date.isin(set(C.date))&wk]
            print(f"     {w} PT window: news {len(a):2d} mkts ${a.pnl.mean() if len(a) else 0:+8.1f} (total ${a.pnl.sum():+8,.0f}, {a.stopped.gt(0).mean()*100 if len(a) else 0:3.0f}% with a stop) | normal {len(b):3d} mkts ${b.pnl.mean():+7.1f}")
