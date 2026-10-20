# Gold news-window skips (run after scripts/gold_after_dl.sh). For SRC in gn_A / gn_C: base = Version + gold filters (skip 2-day+ sweep
# and 7-9 AM PT); then news variants skip whole markets whose window (PT start) is in a set around each release. Prints totals,
# drawdown, train/val/test, per news day. Release times are ET in data_news/calendar.csv (8:30 ET = 5:30 PT; FOMC 14:00 ET = 11:00 PT).
import os,numpy as np,pandas as pd,datetime as dt
from zoneinfo import ZoneInfo
ET=ZoneInfo("America/New_York"); PT=ZoneInfo("America/Los_Angeles")
C=pd.read_csv("data_news/calendar.csv")
C["rel"]=[dt.datetime.fromisoformat(f"{d}T{t}").replace(tzinfo=ET).astimezone(PT) for d,t in zip(C.date,C.time_et)]
BIG={"jobs","cpi","fomc"}
for SRC in ("gn_A","gn_C"):
    M=pd.read_csv(f"results/{SRC}_markets.csv"); F=pd.read_csv(f"results/gold_fail_features_{SRC}.csv")[["ticker","sw2d","hour"]]
    M=M.merge(F,on="ticker",how="left"); M["o"]=pd.to_datetime(M.day+" 2026 "+M.window_pt,format="%a %b %d %Y %I:%M %p").dt.tz_localize(PT)
    M=M.sort_values("o"); M["new"]=M.o>=pd.Timestamp("2026-10-08",tz=PT)
    base=~((M.sw2d==1)|((M.o.dt.hour>=7)&(M.o.dt.hour<9)))
    def wins(kinds,offs):
        s=set()
        for r,e in zip(C.rel,C.event):
            if e in kinds:
                for k in offs: s.add(r+pd.Timedelta(minutes=15*k))
        return M.o.isin(s)
    V={"gold filters only":np.zeros(len(M),bool)}
    for lab,kinds in (("NFP only",{"jobs"}),("FOMC only",{"fomc"}),("Thursday claims only",{"claims"}),("NFP/CPI/FOMC",BIG),("all releases",set(C.event))):
        days=set(C[C.event.isin(kinds)].rel.map(lambda r:r.date()))
        V[f"{lab}: WHOLE DAY OFF"]=M.o.dt.date.isin(days)
        V[f"{lab}: release window"]=wins(kinds,[0]); V[f"{lab}: window before + release"]=wins(kinds,[-1,0]); V[f"{lab}: before + next hour"]=wins(kinds,[-1,0,1,2,3])
    ops=np.sort(M[~M.new].o.unique()); A,B=ops[int(.6*len(ops))],ops[int(.8*len(ops))]
    M["pp"]=np.where(M.new,"NEW Oct 8-19",np.where(M.o<A,"train",np.where(M.o<B,"val","test"))); nd=M.groupby("pp").day.nunique()
    print(f"\n===== {SRC} ({'Version A' if SRC.endswith('A') else 'Version C'}), gold prices to {M.o.max():%b %d}")
    print(f"{'variant':44s} {'skipped':>7s} {'skipped $':>9s} | per trading day: train / val / test / NEW | total | max DD")
    for lab,sk in V.items():
        keep=base&~sk; x=M[keep]; c=x.pnl.cumsum()
        print(f"{lab:44s} {int((base&sk).sum()):7d} {M[base&sk].pnl.sum():+9,.0f} | "+" / ".join(f"{x[x.pp==p].pnl.sum()/max(nd.get(p,1),1):+,.0f}" for p in ("train","val","test","NEW Oct 8-19"))+f" | ${x.pnl.sum():+,.0f} | ${(c-c.cummax()).min():,.0f}")
    print("  per news day (gold filters on): P&L of the window before / release window / next hour (PT), markets traded")
    for r,e in zip(C.rel,C.event):
        w=[M[base&M.o.isin([r+pd.Timedelta(minutes=15*k) for k in ks])] for ks in ([-1],[0],[1,2,3])]
        if sum(len(x) for x in w)==0 and not (M.o.dt.date==r.date()).any(): continue
        print(f"   {r:%a %b %d} {e:8s} {r:%-I:%M %p}: before ${w[0].pnl.sum():+7,.0f} | release ${w[1].pnl.sum():+7,.0f} | next hour ${w[2].pnl.sum():+7,.0f}  ({sum(len(x) for x in w)} mkts)")
