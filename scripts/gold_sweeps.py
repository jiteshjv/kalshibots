# Gold sweep-and-reverse: at the first entry, did gold break a level in the last N min (N=5,15)? WITH = broke in our direction (we bet
# continuation), AGAINST = broke the opposite level (we bet the turn). Levels: yesterday (UTC) high/low, Asia (5 PM-1 AM PT) and London
# (12-5 AM PT) session high/low, 7-day high/low, $25/$50 round numbers. Level = extreme of its period, measured before the last N min.
import json,numpy as np,pandas as pd,datetime as dt
from zoneinfo import ZoneInfo
PT=ZoneInfo("America/Los_Angeles")
Z=np.load("data_gold/prices_1s.npz"); T0=int(Z["T0"]); P=np.load("data_gold/composite_1s.npy")
D=pd.read_csv("results/gold_fail_features.csv"); E=pd.read_csv("results/gc_A_entries.csv").sort_values(["ticker","entry_no"])
meta={x["ticker"]:x for x in json.load(open("data_gold/settled.json"))}; first=E.groupby("ticker").first()
sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
def ext(a,b,d):
    s=P[max(a,0):max(b,0)]; s=s[~np.isnan(s)]
    return np.nan if not len(s) else (s.max() if d>0 else s.min())
rows=[]
for t in D.ticker:
    x=meta[t]; op=int(dt.datetime.fromisoformat(x["close_time"].replace("Z","+00:00")).timestamp())-900
    f=first.loc[t]; d=1 if f.side.startswith("YES") else -1; i=op+sec(f.entry_time)-1-T0
    if P[i]!=P[i]: continue
    u=dt.datetime.fromtimestamp(op,dt.timezone.utc); day0=int(dt.datetime(u.year,u.month,u.day,tzinfo=dt.timezone.utc).timestamp())-T0
    lp=dt.datetime.fromtimestamp(op,PT); pday=int(dt.datetime(lp.year,lp.month,lp.day,tzinfo=PT).timestamp())-T0
    per={"yday":(day0-86400,day0),"wk7":(i-7*86400,None),"asia":(pday-7*3600,pday+3600) if lp.hour>=1 else None,"london":(pday,pday+5*3600) if lp.hour>=5 else None}
    r={"ticker":t}
    for N in (300,900):
        cut=i-N; hi5=ext(cut,i+1,1); lo5=ext(cut,i+1,-1); pre=P[cut]
        for k,v in per.items():
            if v is None: r[f"{k}_{N}"]="" ; continue
            a,b=v; b=cut if b is None else min(b,cut)
            H,Lw=ext(a,b,1),ext(a,b,-1)
            up=H==H and pre<H<hi5; dn=Lw==Lw and pre>Lw>lo5                      # crossed the level within the last N s
            r[f"{k}_{N}"]="with" if (up and d>0) or (dn and d<0) else ("against" if (up and d<0) or (dn and d>0) else "")
        for k in (25,50):
            lv_up=(np.floor(pre/k)+1)*k; lv_dn=np.ceil(pre/k-1)*k
            up=hi5>=lv_up; dn=lo5<=lv_dn
            r[f"rn{k}_{N}"]="with" if (up and d>0) or (dn and d<0) else ("against" if (up and d<0) or (dn and d>0) else "")
    rows.append(r)
L=D.merge(pd.DataFrame(rows),on="ticker"); L.to_csv("results/gold_sweeps_features.csv",index=False)
print(f"{len(L)} gold markets, avg ${L.pnl.mean():+.1f}/market, {L.bad.mean()*100:.0f}% full stop-outs")
print(f"{'level broken in the last N min':44s} {'mkts':>5s} {'stop':>5s} {'$/mkt':>6s} | train/val/test $/mkt | total")
def show(lab,m):
    x=L[m]
    if len(x)<15: print(f"{lab:44s} {len(x):5d}  (too few)"); return
    print(f"{lab:44s} {len(x):5d} {x.bad.mean()*100:4.0f}% {x.pnl.mean():+6.1f} | "+"/".join(f"{x[x.part==p].pnl.mean():+.0f}" for p in ("train","val","test"))+f" | ${x.pnl.sum():+,.0f}")
for k,lab in (("yday","yesterday's high/low"),("asia","Asia high/low"),("london","London high/low"),("wk7","7-day high/low"),("rn25","$25 round number"),("rn50","$50 round number")):
    for N in (300,900):
        for w in ("with","against"): show(f"{lab}, last {N//60} min, {'WITH us (bet continuation)' if w=='with' else 'against us (bet the turn)'}",L[f"{k}_{N}"]==w)
