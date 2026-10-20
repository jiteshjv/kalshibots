# BTC key-level / post-break cooldown / chop study on the recommended setup (j5 runs + NFP/CPI/FOMC hour skipped), Jun 1 - Oct 19.
# Features at the market's FIRST ENTRY (side d): distance in ATR to the 2-day / 5-day high (YES) or low (NO) still ahead of us,
# minutes since BTC last broke its 2-day high or low (either side), strike crosses from the open to the first entry.
# python3 -P scripts/level_study.py  -> results/level_study.txt / .csv
import numpy as np,pandas as pd,datetime as dt
from zoneinfo import ZoneInfo
PT=ZoneInfo("America/Los_Angeles"); ET=ZoneInfo("America/New_York"); sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
SRC={}
for d in ("data_hist","data","data_recent","data_mon"):
    Z=np.load(f"{d}/prices_1s.npz"); SRC[d]=(int(Z["T0"]),np.load(f"{d}/composite_1s.npy"))
def sec_slice(a,b):                                   # 1 s prices [a,b) from the folder that covers it
    for d in ("data_mon","data_recent","data","data_hist"):
        T0,A=SRC[d]
        if a-T0>=0 and b-T0<=len(A):
            s=A[a-T0:b-T0]
            if np.isfinite(s).mean()>0.9: return s
    return None
parts=[]
for d,(T0,A) in SRC.items():
    a=(-T0)%60; B=A[a:]; n=len(B)//60; B=B[:n*60].reshape(n,60)
    with np.errstate(all="ignore"): parts.append(pd.DataFrame({"t":T0+a+60*np.arange(n),"h":np.nanmax(B,1),"l":np.nanmin(B,1),"c":B[:,-1]}))
P=pd.concat(parts).dropna().drop_duplicates("t",keep="last").sort_values("t").set_index("t")
P=P.reindex(range(int(P.index.min()),int(P.index.max())+60,60)).ffill(); tmin=int(P.index[0])
H,L,Cl=P.h.values,P.l.values,P.c.values
hi2=pd.Series(H).shift(5).rolling(2880,min_periods=2000).max().values; lo2=pd.Series(L).shift(5).rolling(2880,min_periods=2000).min().values
hi5=pd.Series(H).shift(5).rolling(7200,min_periods=5000).max().values; lo5=pd.Series(L).shift(5).rolling(7200,min_periods=5000).min().values
ph=pd.Series(H).shift(1).rolling(2880,min_periods=2000).max().values; pl=pd.Series(L).shift(1).rolling(2880,min_periods=2000).min().values
brk=(H>ph)|(L<pl); last=pd.Series(np.where(brk,np.arange(len(H)),np.nan)).ffill().values
b15=(pd.Series(H).rolling(15).max()-pd.Series(L).rolling(15).min()).values; atr=pd.Series(b15[::1]).rolling(1440,min_periods=720).mean().values  # mean 15-min range, prior day
ix=lambda ts:int((ts-tmin)//60)
F=[f"jh_j5_{m}" for m in ("jun","jul","aug")]+[f"v9_j5_{p}" for p in ("66d","recent","mon")]
M=pd.concat([pd.read_csv(f"results/{f}_markets.csv") for f in F]).drop_duplicates("ticker",keep="last")
E=pd.concat([pd.read_csv(f"results/{f}_entries.csv") for f in F]).drop_duplicates(["ticker","entry_no"],keep="last")
M["o"]=pd.to_datetime(M.day+" 2026 "+M.window_pt,format="%a %b %d %Y %I:%M %p").dt.tz_localize(PT); M=M[M.o>="2026-06-01"].sort_values("o").reset_index(drop=True)
M["ts"]=M.o.astype("int64")//10**9; E=E[E.ticker.isin(M.ticker)].sort_values(["ticker","entry_no"]); first=E.groupby("ticker").first()
C=pd.read_csv("data_news/calendar.csv"); C["rel"]=[dt.datetime.fromisoformat(f"{a}T{b}").replace(tzinfo=ET).timestamp() for a,b in zip(C.date,C.time_et)]
NEWS={int(r+900*k) for r,e in zip(C.rel,C.event) if e in ("jobs","cpi","fomc") for k in range(4)}
M=M[~M.ts.isin(NEWS)].reset_index(drop=True)
M["d"]=M.ticker.map(first.side.str.startswith("YES").map({True:1,False:-1})); M["fe"]=M.ticker.map(first.entry_time.map(sec))
rows=[]
for r in M.itertuples():
    t=r.ts+int(r.fe); i=ix(t)-1; p=Cl[i]; a=atr[i]; d=r.d
    L2=hi2[i] if d>0 else lo2[i]; L5=hi5[i] if d>0 else lo5[i]
    s=sec_slice(r.ts,t); cr=int(np.sum(np.diff(np.sign(s[np.isfinite(s)]-r.strike))!=0)) if s is not None and len(s)>1 else np.nan
    rows.append(dict(dist2=d*(L2-p)/(a*p/100) if False else d*(L2-p)/(a) if a==a and a>0 else np.nan,
                     dist5=d*(L5-p)/a if a==a and a>0 else np.nan, cool=(i-last[i]) if last[i]==last[i] else 1e9, crosses=cr, atr_pct=a/p*100))
M=pd.concat([M,pd.DataFrame(rows)],axis=1)
M["part"]=np.where(M.o<pd.Timestamp("2026-08-10 17:00",tz=PT),"Jun-Jul","Aug-Oct"); nd=M.groupby("part").o.apply(lambda x:x.dt.date.nunique())
M.to_csv("results/level_study.csv",index=False)
print(f"{len(M)} traded markets (recommended setup, news hour removed). Avg ${M.pnl.mean():+.1f}/market; ATR median {M.atr_pct.median():.2f}% of price")
def bucket(k,edges,lab):
    print(f"  {lab}: "+" | ".join(f"{a:g}..{b:g}: {len(x)} mkts ${x.pnl.mean():+.0f} {x.stopped.gt(0).mean()*100:.0f}%stop" for a,b in zip(edges[:-1],edges[1:]) for x in [M[(M[k]>a)&(M[k]<=b)]]))
print("\n$ per market by feature:")
bucket("dist2",[-1e9,0,0.5,1,2,4,1e9],"2-day level ahead (ATR; <=0 = already through)")
bucket("dist5",[-1e9,0,0.5,1,2,4,1e9],"5-day level ahead (ATR)")
bucket("cool",[-1,30,60,120,240,1e10],"minutes since a 2-day break")
bucket("crosses",[-1,2,5,10,15,20,40,1e9],"strike crosses before first entry")
print(f"\n{'skip the market if ...':46s} {'skip':>5s} {'skip $':>9s} | per day Jun-Jul / Aug-Oct | total    DD   t/DD")
def row(lab,sk):
    sk=sk.fillna(False); x=M[~sk]; c=x.pnl.cumsum(); dd=-(c-c.cummax()).min()
    print(f"{lab:46s} {int(sk.sum()):5d} {M[sk].pnl.sum():+9,.0f} | "+" / ".join(f"{x[x.part==p].pnl.sum()/nd[p]:+6,.0f}" for p in ("Jun-Jul","Aug-Oct"))+f" | ${x.pnl.sum()/1e3:+6.1f}K ${-dd/1e3:5.1f}K {x.pnl.sum()/dd:5.1f}")
row("(recommended setup)",pd.Series(False,index=M.index))
for k,lab in (("dist2","2-day"),("dist5","5-day")):
    for x in (0.5,1.0): row(f"{lab} level ahead within {x} ATR",(M[k]>0)&(M[k]<=x))
for x in (30,60,120): row(f"2-day high/low broken in the last {x} min",M.cool<=x)
for x in (10,15,20): row(f"{x}+ strike crosses before the first entry",M.crosses>=x)
