# Where does gold fail? Chart features at each market's first entry (gold Version A, filters off: results/gc_A_*), compared between markets
# whose first side was fully stopped ("bad") and the rest, with $/market by period. (run from project root: python3 -P scripts/gold_fail.py)
import os,json,numpy as np,pandas as pd,datetime as dt
from zoneinfo import ZoneInfo
Z=np.load("data_gold/prices_1s.npz"); T0=int(Z["T0"]); P=np.load("data_gold/composite_1s.npy")
meta={x["ticker"]:x for x in json.load(open("data_gold/settled.json"))}
SRC=os.environ.get("SRC","gc_A"); M=pd.read_csv(f"results/{SRC}_markets.csv"); E=pd.read_csv(f"results/{SRC}_entries.csv").sort_values(["ticker","entry_no"])
sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1]); PT=ZoneInfo("America/Los_Angeles")
def ext(a,b,d):
    a=max(a,0); seg=P[a:b]
    if b<=a or np.isnan(seg).all(): return np.nan
    return np.nanmax(seg) if d>0 else np.nanmin(seg)
def swept(i,d,k):
    lvl=ext(i-k-300,i-300,d); l5=ext(i-300,i+1,d)
    if lvl!=lvl or l5!=l5: return np.nan
    return int(d*(l5-lvl)>0)
rows=[]
for t,g in E.groupby("ticker"):
    x=meta.get(t)
    if not x: continue
    op=int(dt.datetime.fromisoformat(x["close_time"].replace("Z","+00:00")).timestamp())-900; K0=float(x["floor_strike"])
    g1=g[g.side==g.side.iloc[0]]; d=1 if g1.side.iloc[0].startswith("YES") else -1
    i=op+sec(g1.entry_time.iloc[0])-1-T0
    if i<14400 or P[i]!=P[i]: continue
    p=P[i]; bars=P[max(0,i-86400):i]; bars=bars[:len(bars)//900*900].reshape(-1,900)
    atr=np.nanmean(np.nanmax(bars,1)-np.nanmin(bars,1)) if len(bars) else np.nan
    mom=lambda k:d*(p-P[i-k])/p*100 if P[i-k]==P[i-k] else np.nan
    w=P[op-T0:i+1]; w=w[~np.isnan(w)]; cr=int(np.sum(np.diff(np.sign(w-K0))!=0)) if len(w)>1 else 0
    L2=ext(i-2*86400-300,i-300,d)
    u=dt.datetime.fromtimestamp(op+sec(g1.entry_time.iloc[0]),dt.timezone.utc); day0=int(dt.datetime(u.year,u.month,u.day,tzinfo=dt.timezone.utc).timestamp())
    pdl=ext(day0-86400-T0,day0-T0,d); pdc=int(pdl==pdl and d*(P[i-300]-pdl)<0 and d*(ext(i-300,i+1,d)-pdl)>0)
    hr=dt.datetime.fromtimestamp(op,PT)
    mrow=M[M.ticker==t].iloc[0]
    rows.append(dict(ticker=t,op=op,pnl=mrow.pnl,bad=bool((g1.exit=="stopped").all()),hour=hr.hour,dow=hr.strftime("%a"),minute=sec(g1.entry_time.iloc[0])/60,
        m15=mom(900),m60=mom(3600),m240=mom(14400),vs60=d*(p-np.nanmean(P[i-3600:i]))/atr if atr==atr and atr>0 else np.nan,
        crosses=cr,rv5=np.nanstd(np.diff(P[i-300:i])),to2d=d*(L2-p)/atr if L2==L2 and atr==atr and atr>0 else np.nan,
        sw1h=swept(i,d,3600),sw4h=swept(i,d,14400),sw1d=swept(i,d,86400),sw2d=swept(i,d,2*86400),sw5d=swept(i,d,5*86400),sw7d=swept(i,d,7*86400),prevday=pdc))
D=pd.DataFrame(rows); ops=np.sort(D.op.unique()); A,B=ops[int(.6*len(ops))],ops[int(.8*len(ops))]; D["part"]=np.where(D.op<A,"train",np.where(D.op<B,"val","test"))
D.to_csv("results/gold_fail_features.csv" if SRC=="gc_A" else f"results/gold_fail_features_{SRC}.csv",index=False)
print(f"{len(D)} gold markets (Version A, filters off). Bad = first side fully stopped: {D.bad.mean()*100:.0f}%. Avg ${D.pnl.mean():+.1f}/market.\n")
print(f"{'condition at first entry':52s} {'mkts':>5s} {'bad':>4s} {'$/mkt':>6s} | train/val/test $/mkt | total")
def show(lab,m):
    x=D[m]
    if len(x)<10: print(f"{lab:52s} {len(x):5d}  (too few)"); return
    print(f"{lab:52s} {len(x):5d} {x.bad.mean()*100:3.0f}% {x.pnl.mean():+6.1f} | "+"/".join(f"{x[x.part==p].pnl.mean():+.0f}" if (x.part==p).sum()>=5 else "--" for p in ("train","val","test"))+f" | ${x.pnl.sum():+,.0f}")
print("-- sweeps (last 5 min ran through the prior N high/low, our side)")
for k,l in (("sw1h","1 hour"),("sw4h","4 hours"),("sw1d","1 day"),("sw2d","2 days"),("sw5d","5 days"),("sw7d","7 days")): show(f"   {l}",D[k]==1)
show("   crossed yesterday's (UTC) high/low",D.prevday==1)
show("   4h sweep AND 1h move in the top 20%",(D.sw4h==1)&(D.m60>=D.m60.quantile(.8)))
print("-- distance to the 2-day high/low (ATR)")
for lo,hi in ((-99,0),(0,0.5),(0.5,1),(1,2),(2,99)): show(f"   {lo:+.1f} to {hi:+.1f} ATR",(D.to2d>lo)&(D.to2d<=hi))
print("-- stretch / momentum (fifths)")
for k,l in (("m60","1h move our way"),("m240","4h move our way"),("vs60","vs 1h average (ATR)"),("rv5","5-min volatility"),("crosses","strike crosses so far"),("minute","minute of first entry")):
    q=pd.qcut(D[k].rank(method="first"),5,labels=["low","2","3","4","high"])
    for b in ("low","high"): show(f"   {l}: {b} fifth",q==b)
print("-- time of day (PT, window start)")
for a,b in ((0,3),(3,5),(5,7),(7,9),(9,11),(11,13),(13,15),(15,18),(18,21),(21,24)): show(f"   {a:02d}:00-{b:02d}:00",(D.hour>=a)&(D.hour<b))
print("-- weekday"); [show(f"   {w}",D.dow==w) for w in ("Mon","Tue","Wed","Thu","Fri","Sun")]
