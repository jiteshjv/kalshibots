# Multi-day liquidity levels at each market's first entry (mixed, filters off). Rolling: did the last 5 min run beyond the prior N-day
# extreme on our side (window ends 5 min ago). Fixed (calendar UTC): previous day's / previous week's (Mon-Sun) high/low, crossed in the last
# 5 min (was on the near side 5 min ago). "only" = the N-day level was hit but not the next shorter one (isolates the longer level).
# (run from project root: python3 -P scripts/multiday_levels.py)
import numpy as np,pandas as pd,datetime as dt
Z=np.load("data/prices_1s.npz"); T0=int(Z["T0"]); P=np.load("data/composite_1s.npy")
X=pd.read_csv("results/weekend_levels.csv"); E=pd.read_csv("results/her_66d_entries.csv").sort_values(["ticker","entry_no"])
f=E.groupby("ticker").first(); sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
def beyond(i,d,a,b,cross=False):
    if a<0 or b<=a: return np.nan
    pr=P[a:b]
    if np.isnan(pr).mean()>0.5: return np.nan
    lvl=np.nanmax(pr) if d>0 else np.nanmin(pr); l5=P[i-300:i+1]
    if cross and not d*(P[i-300]-lvl)<0: return 0
    return int(d*((np.nanmax(l5) if d>0 else np.nanmin(l5))-lvl)>0)
N=[1,2,3,5,7,14,30]; rows=[]
for r in X.itertuples():
    g=f.loc[r.ticker]; t=r.op+sec(g.entry_time)-1; i=t-T0; d=1 if g.side.startswith("YES") else -1
    row={f"roll{n}d":beyond(i,d,i-n*86400-300,i-300) for n in N}
    u=dt.datetime.fromtimestamp(t,dt.timezone.utc); day0=int(dt.datetime(u.year,u.month,u.day,tzinfo=dt.timezone.utc).timestamp())
    row["prev_day"]=beyond(i,d,day0-86400-T0,day0-T0,cross=True)
    wk0=day0-u.weekday()*86400; row["prev_week"]=beyond(i,d,wk0-7*86400-T0,wk0-T0,cross=True)
    rows.append(row)
Y=pd.concat([X,pd.DataFrame(rows)],axis=1); Y.to_csv("results/multiday_levels.csv",index=False)
def show(lab,m,sub=None):
    for nm,mm in (("all",m),("weekday",m&(Y.wkend==0)),("weekend",m&(Y.wkend==1))):
        x=Y[mm]
        if len(x)<15: print(f"  {lab:44s} {nm:7s} {len(x):5d} mkts  (too few)"); continue
        print(f"  {lab:44s} {nm:7s} {len(x):5d} mkts  bad {x.bad.mean()*100:3.0f}%  ${x.pnl.mean():+5.0f}/mkt | {'/'.join(f'{x[x.part==p].pnl.mean():+.0f}' if (x.part==p).sum()>=5 else '--' for p in ('train','val','test'))}")
print(f"{len(Y)} markets, all-market average ${Y.pnl.mean():+.0f}, bad {Y.bad.mean()*100:.0f}%\n\nRolling N-day high/low, just run through (our side):")
for n in N: show(f"{n}-day level",Y[f"roll{n}d"]==1)
print("\nOnly the longer level (hit N-day but NOT the next shorter one):")
for a,b in ((2,1),(3,2),(5,3),(7,5),(14,7),(30,14)): show(f"{a}-day level, not {b}-day",(Y[f"roll{a}d"]==1)&(Y[f"roll{b}d"]==0))
print("\nFixed calendar levels, crossed in the last 5 min:")
show("previous UTC day's high/low",Y.prev_day==1); show("previous week's high/low",Y.prev_week==1)
