# Do weekend highs/lows act as liquidity levels? At each market's first entry (mixed, filters off; results/liq_features.csv):
#  weekend markets: just ran (last 5 min) through the weekend-so-far high/low (since Sat 00:00 UTC) or Friday's (UTC) high/low, on our side
#  weekday markets: just ran through the previous weekend's (Sat-Sun UTC) high/low on our side
# (run from project root: python3 -P scripts/weekend_levels.py)
import numpy as np,pandas as pd,datetime as dt
Z=np.load("data/prices_1s.npz"); T0=int(Z["T0"]); P=np.load("data/composite_1s.npy")
L=pd.read_csv("results/liq_features.csv"); E=pd.read_csv("results/her_66d_entries.csv").sort_values(["ticker","entry_no"])
f=E.groupby("ticker").first(); sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
def ran(i,d,a,b,cross=False):           # did the last 5 min trade beyond the extreme of P[a:b] (indices), on side d?
    if a<0 or b<=a or b>i-300+1: b=min(b,i-300)
    if a<0 or b<=a: return np.nan
    pr=P[a:b]; l5=P[i-300:i+1]
    if np.isnan(pr).all(): return np.nan
    lvl=np.nanmax(pr) if d>0 else np.nanmin(pr)
    if cross and not d*(P[i-300]-lvl)<0: return 0                    # fixed level: must have been on the near side 5 min ago
    return int(d*((np.nanmax(l5) if d>0 else np.nanmin(l5))-lvl)>0)
rows=[]
for r in L.itertuples():
    g=f.loc[r.ticker]; t=r.op+sec(g.entry_time)-1; i=t-T0; d=1 if g.side.startswith("YES") else -1
    u=dt.datetime.fromtimestamp(t,dt.timezone.utc); wd=u.weekday()           # Sat=5, Sun=6
    day0=int(dt.datetime(u.year,u.month,u.day,tzinfo=dt.timezone.utc).timestamp())
    sat0=day0-(wd-5)*86400 if wd>=5 else day0-(wd+2)*86400                   # this weekend's Sat 00:00 UTC (or the last one)
    row=dict(wkend=int(wd>=5))
    if wd>=5:
        row["ran_wkend_sofar"]=ran(i,d,sat0-T0,i-300,cross=True)
        row["ran_friday"]=ran(i,d,sat0-86400-T0,sat0-T0,cross=True)
        row["ran_prev_wkend"]=np.nan
    else:
        row["ran_wkend_sofar"]=np.nan; row["ran_friday"]=np.nan
        row["ran_prev_wkend"]=ran(i,d,sat0-T0,sat0+2*86400-T0,cross=True)
    rows.append(row)
X=pd.concat([L,pd.DataFrame(rows)],axis=1)
def show(lab,m):
    x=X[m]; base=X[(X.wkend==x.wkend.iloc[0]) & ~m] if len(x) else X
    print(f"{lab:62s} {len(x):5d} mkts  bad {x.bad.mean()*100:4.0f}%  ${x.pnl.mean():+5.0f}/mkt | {'/'.join(f'{x[x.part==p].pnl.mean():+.0f}' for p in ('train','val','test'))}"
          f"   (rest of those days ${base.pnl.mean():+.0f}, bad {base.bad.mean()*100:.0f}%)")
print(f"{len(X)} markets; weekend {X.wkend.sum()}, weekday {(1-X.wkend).sum()}\n")
show("WEEKEND: just ran through the weekend-so-far high/low",X.ran_wkend_sofar==1)
show("WEEKEND: just ran through Friday's high/low",X.ran_friday==1)
show("WEEKEND: ran through 24h high/low (current filter)",(X.wkend==1)&(X.swept_24h==1))
show("WEEKDAY: just ran through the previous weekend's high/low",X.ran_prev_wkend==1)
show("WEEKDAY (Mon-Tue only): ran through previous weekend's high/low",(X.ran_prev_wkend==1)&(pd.to_datetime(X.op,unit="s").dt.weekday<=1))
show("WEEKDAY: ran through 24h high/low (current filter)",(X.wkend==0)&(X.swept_24h==1))
X.to_csv("results/weekend_levels.csv",index=False)
