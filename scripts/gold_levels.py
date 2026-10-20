# Gold: is a level AHEAD of our side close at the first entry? (Version A, filters off: results/gc_A_*.) Levels: round numbers ($25/$50/$100),
# yesterday's (UTC) high/low, the 7-day high/low, the Asia session (5 PM - 1 AM PT) and London (12 - 5 AM PT) high/low of the current day.
# Distance = $ from price to the nearest such level in our direction (above for YES, below for NO). $/market by distance bucket and period.
import json,numpy as np,pandas as pd,datetime as dt
from zoneinfo import ZoneInfo
PT=ZoneInfo("America/Los_Angeles")
Z=np.load("data_gold/prices_1s.npz"); T0=int(Z["T0"]); P=np.load("data_gold/composite_1s.npy")
D=pd.read_csv("results/gold_fail_features.csv"); E=pd.read_csv("results/gc_A_entries.csv").sort_values(["ticker","entry_no"])
meta={x["ticker"]:x for x in json.load(open("data_gold/settled.json"))}
sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
first=E.groupby("ticker").first()
def ext(a,b,d):
    s=P[max(a,0):b]; s=s[~np.isnan(s)]
    return np.nan if not len(s) else (s.max() if d>0 else s.min())
rows=[]
for t in D.ticker:
    x=meta[t]; op=int(dt.datetime.fromisoformat(x["close_time"].replace("Z","+00:00")).timestamp())-900
    f=first.loc[t]; d=1 if f.side.startswith("YES") else -1; i=op+sec(f.entry_time)-1-T0; p=P[i]
    if p!=p: continue
    r={"ticker":t}
    for k in (25,50,100):
        lv=(np.floor(p/k)+1)*k if d>0 else np.ceil(p/k-1)*k
        r[f"rn{k}"]=abs(lv-p)
    u=dt.datetime.fromtimestamp(op,dt.timezone.utc); day0=int(dt.datetime(u.year,u.month,u.day,tzinfo=dt.timezone.utc).timestamp())-T0
    lp=dt.datetime.fromtimestamp(op,PT); pday=int(dt.datetime(lp.year,lp.month,lp.day,tzinfo=PT).timestamp())-T0
    lv={"yday":ext(day0-86400,day0,d),"wk7":ext(i-7*86400,i-300,d),
        "asia":ext(pday-7*3600,pday+3600,d) if lp.hour>=1 else np.nan,"london":ext(pday,pday+5*3600,d) if lp.hour>=5 else np.nan}
    for k,v in lv.items(): r[k]=d*(v-p) if v==v else np.nan       # >0: level still ahead; <=0: already through it
    rows.append(r)
L=D.merge(pd.DataFrame(rows),on="ticker")
print(f"{len(L)} gold markets, avg ${L.pnl.mean():+.1f}/market, {L.bad.mean()*100:.0f}% full stop-outs\n")
print(f"{'level ahead of our side':38s} {'mkts':>5s} {'stop':>5s} {'$/mkt':>6s} | train/val/test $/mkt | total")
def show(lab,m):
    x=L[m]
    if len(x)<20: print(f"{lab:38s} {len(x):5d}  (too few)"); return
    print(f"{lab:38s} {len(x):5d} {x.bad.mean()*100:4.0f}% {x.pnl.mean():+6.1f} | "+"/".join(f"{x[x.part==p].pnl.mean():+.0f}" for p in ("train","val","test"))+f" | ${x.pnl.sum():+,.0f}")
for k in (100,50,25):
    for a,b in ((0,2),(2,5),(5,10),(10,1e9)): show(f"${k} round number ${a}-{b if b<1e9 else 'up'} away",(L[f"rn{k}"]>a)&(L[f"rn{k}"]<=b))
for k,lab in (("yday","yesterday's high/low"),("wk7","7-day high/low"),("asia","Asia session high/low"),("london","London session high/low")):
    show(f"{lab}: already through",L[k]<=0)
    for a,b in ((0,3),(3,8),(8,1e9)): show(f"{lab}: ${a}-{b if b<1e9 else 'up'} ahead",(L[k]>a)&(L[k]<=b))
