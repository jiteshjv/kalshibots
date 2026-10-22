# Gold gates re-check: leave-one-out on the gold bot's market gates, from results/gg_* (scripts/run_gold_gates.sh).
#   python3 -P scripts/gold_gates.py
import pandas as pd,numpy as np,datetime as dt
from zoneinfo import ZoneInfo
PT=ZoneInfo("America/Los_Angeles"); ET=ZoneInfo("America/New_York")
C=pd.read_csv("data_news/calendar.csv"); NEWS=set()
for d,e,t in zip(C.date,C.event,C.time_et):
    rel=int(dt.datetime.fromisoformat(f"{d}T{t}").replace(tzinfo=ET).timestamp())
    if e in ("jobs","cpi","claims"): NEWS|={rel+900*k for k in (-1,0,1,2,3)}
    if e=="fomc": NEWS|={rel+900*k for k in (0,1,2,3)}
def load(n):
    M=pd.read_csv(f"results/gg_{n}_markets.csv"); F=pd.read_csv(f"results/gold_fail_features_gg_{n}.csv").set_index("ticker")
    M["o"]=pd.to_datetime(M.day+" 2026 "+M.window_pt,format="%a %b %d %Y %I:%M %p"); M=M.sort_values("o").reset_index(drop=True)
    M["ts"]=M.o.dt.tz_localize(PT).astype("int64")//10**9
    M["sweep"]=M.ticker.map(F.sw2d).fillna(0)==1; M["am79"]=M.o.dt.hour.between(7,8); M["news"]=M.ts.isin(NEWS)
    M["per"]=np.where(M.o<"2026-09-16","Aug 3-Sep 15",np.where(M.o<"2026-10-08","Sep 16-Oct 7","Oct 8-20 (new)"))
    M["utc"]=M.o.dt.tz_localize(PT).dt.tz_convert("UTC").dt.date
    return M
def stats(M,keep,lim=600):
    T=M[keep].copy()
    if lim:                                   # daily loss limit: no new markets for the rest of the UTC day after -$lim
        out=[];
        for _,g in T.groupby("utc"):
            c=g.pnl.cumsum().shift(fill_value=0); out.append(g[c>-lim])
        T=pd.concat(out)
    c=T.pnl.cumsum(); d=T.groupby(T.o.dt.date).pnl.sum()
    per=T.groupby("per").pnl.sum()
    return T.pnl.sum(),(c-c.cummax()).min(),(d<0).sum(),len(d),len(T),[per.get(p,0) for p in ("Aug 3-Sep 15","Sep 16-Oct 7","Oct 8-20 (new)")]
def line(lab,s): print(f"{lab:44s} ${s[0]:>8,.0f}  DD {s[1]:>7,.0f}  losing days {s[2]:2d}/{s[3]}  mkts {s[4]:5d} | "+" / ".join(f"{x:+7,.0f}" for x in s[5]))
M=load("r120"); ALL=~(M.sweep|M.am79|M.news)
print("Gold, bot settings after the Oct 21 update (equal 40), Aug 3 - Oct 20.  Periods: Aug 3-Sep 15 / Sep 16-Oct 7 / Oct 8-20 (new)\n")
line("All gates on (bot now)",stats(M,ALL))
line("  without the 2-day+ sweep skip",stats(M,~(M.am79|M.news)))
line("  without the 7-9 AM PT skip",stats(M,~(M.sweep|M.news)))
line("  without the news windows",stats(M,~(M.sweep|M.am79)))
line("  without the daily loss limit",stats(M,ALL,lim=0))
line("No gates at all (range cap 1.2% only)",stats(M,M.pnl==M.pnl,lim=0))
print("\nWhat each gate removes (markets it skips, with every other gate on):")
for k,lab in (("sweep","2-day+ sweep"),("am79","7-9 AM PT"),("news","news windows")):
    o=[x for x in ("sweep","am79","news") if x!=k]; R=M[M[k]&~M[o].any(axis=1)]
    per=R.groupby("per").pnl.sum()
    print(f"  {lab:14s} {len(R):4d} mkts, ${R.pnl.sum():+8,.0f} (${R.pnl.mean():+.1f}/mkt) | "+" / ".join(f"{per.get(p,0):+7,.0f}" for p in ("Aug 3-Sep 15","Sep 16-Oct 7","Oct 8-20 (new)"))+f" | stopped-out first side {R.stopped.gt(0).mean():.0%} vs {M[ALL].stopped.gt(0).mean():.0%}")
print("\n2-hour range cap (other gates on):")
for n,lab in (("r100","none"),("r080","0.8%"),("r100c","1.0%"),("r120","1.2% (bot now)"),("r150","1.5%"),("r200","2.0%")):
    X=load(n); line(f"  cap {lab}",stats(X,~(X.sweep|X.am79|X.news)))
print("\n7-9 AM PT, hour by hour (no time skip, other gates on):")
for h in range(5,11):
    R=M[(M.o.dt.hour==h)&~(M.sweep|M.news)]; per=R.groupby("per").pnl.sum()
    print(f"  {h:2d}:00 {len(R):4d} mkts ${R.pnl.sum():+8,.0f} | "+" / ".join(f"{per.get(p,0):+7,.0f}" for p in ("Aug 3-Sep 15","Sep 16-Oct 7","Oct 8-20 (new)")))
print("\nDaily loss limit:")
for L in (0,300,450,600,900,1200): line(f"  ${L}" if L else "  none",stats(M,ALL,lim=L))
