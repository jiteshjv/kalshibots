# Compare fair-value variants with the recommended Equal-100 baseline (eqrec), Jun 1 - Oct 19, NFP/CPI/FOMC hour removed.
# python3 -P scripts/fv_compare.py eqrec fvs1 fvs2 fve2 fve5
import sys,datetime as dt,pandas as pd,numpy as np
from zoneinfo import ZoneInfo
PT=ZoneInfo("America/Los_Angeles"); ET=ZoneInfo("America/New_York")
C=pd.read_csv("data_news/calendar.csv"); NEWS={int(dt.datetime.fromisoformat(f"{a}T{b}").replace(tzinfo=ET).timestamp())+900*k
    for a,b,e in zip(C.date,C.time_et,C.event) if e in ("jobs","cpi","fomc") for k in range(4)}
LAB={"eqrec":"Recommended (bid-based stop)","fvs1":"Stop on fair value only","fvs2":"Stop on fair value OR bid",
     "fve2":"+ skip buys > 2c over fair","fve5":"+ skip buys > 5c over fair"}
print(f"{'variant':32s} {'total':>9s} {'max DD':>8s} {'t/DD':>5s} {'worst day':>9s} {'contracts':>10s} {'stopped':>9s} {'avg stop exit':>13s} {'Jun-Jul/day':>11s} {'Aug-Oct/day':>11s}")
for n in sys.argv[1:]:
    M=[];E=[]
    for p in ("jh_{}_jun","jh_{}_jul","jh_{}_aug","v9_{}_66d","v9_{}_recent","v9_{}_mon"):
        M.append(pd.read_csv(f"results/{p.format(n)}_markets.csv")); E.append(pd.read_csv(f"results/{p.format(n)}_entries.csv"))
    M=pd.concat(M).drop_duplicates("ticker",keep="last"); E=pd.concat(E).drop_duplicates(["ticker","entry_no"],keep="last")
    M["o"]=pd.to_datetime(M.day+" 2026 "+M.window_pt,format="%a %b %d %Y %I:%M %p").dt.tz_localize(PT)
    M=M[(M.o>="2026-06-01")&~(M.o.astype("int64")//10**9).isin(NEWS)].sort_values("o"); E=E[E.ticker.isin(M.ticker)]
    c=M.pnl.cumsum(); dd=-(c-c.cummax()).min(); d=M.groupby(M.o.dt.date).pnl.sum()
    part=M.o<pd.Timestamp("2026-08-10 17:00",tz=PT); S=E[E.exit=="stopped"]
    nd1=M[part].o.dt.date.nunique(); nd2=M[~part].o.dt.date.nunique()
    print(f"{LAB.get(n,n):32s} ${M.pnl.sum()/1e3:+7.1f}K ${-dd/1e3:6.1f}K {M.pnl.sum()/dd:5.1f} ${d.min():+8,.0f} {E.n.sum()/1e6:9.2f}M {S.n.sum()/1e6:8.2f}M {np.average(S.exit_px,weights=S.n)*100:12.1f}c"
          f" {M[part].pnl.sum()/nd1:+11,.0f} {M[~part].pnl.sum()/nd2:+11,.0f}")
