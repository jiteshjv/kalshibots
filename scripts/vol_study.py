# BTC news / short-term volatility / sharp-move study, Jun 1 - Oct 19 (no-cap runs: python3 -P scripts/vol_study.py nc|ncs).
# Features per market from a merged per-minute BTC series (data_hist, data, data_recent, data_mon), measured before the window opens
# (and at the first entry for the move-against feature). Every rule is a whole-market skip applied to the no-cap run.
import sys,json,numpy as np,pandas as pd,datetime as dt
from zoneinfo import ZoneInfo
PT=ZoneInfo("America/Los_Angeles"); ET=ZoneInfo("America/New_York"); N=sys.argv[1] if len(sys.argv)>1 else "ncs"
sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
# ---- merged minute bars
parts=[]
for d in ("data_hist","data","data_recent","data_mon"):
    Z=np.load(f"{d}/prices_1s.npz"); T0=int(Z["T0"]); A=np.load(f"{d}/composite_1s.npy")
    a=(-T0)%60; A=A[a:]; t0=T0+a; n=len(A)//60; B=A[:n*60].reshape(n,60)
    with np.errstate(all="ignore"): parts.append(pd.DataFrame({"t":t0+60*np.arange(n),"h":np.nanmax(B,1),"l":np.nanmin(B,1),"c":B[:,-1]}))
P=pd.concat(parts).dropna().drop_duplicates("t",keep="last").sort_values("t").set_index("t")
P=P.reindex(range(int(P.index.min()),int(P.index.max())+60,60)).ffill()
H,L,Cl=P.h.values,P.l.values,P.c.values; tmin=int(P.index[0])
idx=lambda ts:int((ts-tmin)//60)
def rng(a,b):
    i,j=idx(a),idx(b)
    if i<0 or j<=i: return np.nan
    return (H[i:j].max()-L[i:j].min())/Cl[i:j].mean()*100
def jump(a,b):
    i,j=idx(a),idx(b)
    if i<1 or j<=i: return np.nan
    return np.nanmax(np.abs(Cl[i:j]/Cl[i-1:j-1]-1))*100
# ---- markets
F=[f"jh_{N}_{m}" for m in ("jun","jul","aug")]+[f"v9_{N}_{p}" for p in ("66d","recent","mon")]
M=pd.concat([pd.read_csv(f"results/{f}_markets.csv") for f in F]).drop_duplicates("ticker",keep="last")
E=pd.concat([pd.read_csv(f"results/{f}_entries.csv") for f in F]).drop_duplicates(["ticker","entry_no"],keep="last")
M["o"]=pd.to_datetime(M.day+" 2026 "+M.window_pt,format="%a %b %d %Y %I:%M %p").dt.tz_localize(PT); M=M[M.o>="2026-06-01"].sort_values("o").reset_index(drop=True)
M["ts"]=M.o.astype("int64")//10**9
E=E[E.ticker.isin(M.ticker)].sort_values(["ticker","entry_no"])
first=E.groupby("ticker").first(); stc=E[E.exit=="stopped"].groupby("ticker").n.sum()
M["d"]=M.ticker.map(first.side.str.startswith("YES").map({True:1,False:-1})); M["fe"]=M.ticker.map(first.entry_time.map(sec))
M["stopped_ct"]=M.ticker.map(stc).fillna(0); M["bought"]=M.ticker.map(E.groupby("ticker").n.sum()).fillna(0)
M["r2h"]=[rng(t-7200,t) for t in M.ts]; M["r30"]=[rng(t-1800,t) for t in M.ts]; M["r15"]=[rng(t-900,t) for t in M.ts]; M["r24"]=[rng(t-86400,t) for t in M.ts]
M["jump15"]=[jump(t-900,t) for t in M.ts]
M["rel2h"]=[r/np.nanmedian([rng(t-k*86400-7200,t-k*86400) for k in range(1,15)]) for r,t in zip(M.r2h,M.ts)]
M["rel24"]=[r/np.nanmedian([rng(t-k*86400-86400,t-k*86400) for k in range(1,15)]) for r,t in zip(M.r24,M.ts)]
def mv30(t,fe,d):
    if fe!=fe: return np.nan
    j=idx(t+fe); i=j-30
    return d*(Cl[j]/Cl[i]-1)*100 if i>=0 else np.nan
M["m30e"]=[mv30(t,f,d) for t,f,d in zip(M.ts,M.fe,M.d)]; M["r30e"]=[rng(t+f-1800,t+f) if f==f else np.nan for t,f in zip(M.ts,M.fe)]
# ---- news windows (window start PT = release time + k*15 min)
C=pd.read_csv("data_news/calendar.csv"); C["rel"]=[dt.datetime.fromisoformat(f"{a}T{b}").replace(tzinfo=ET).timestamp() for a,b in zip(C.date,C.time_et)]
def near(evs,ks):
    s=set()
    for r,e in zip(C.rel,C.event):
        if e in evs:
            for k in ks: s.add(int(r+900*k))
    return M.ts.isin(s)
M["hour"]=M.o.dt.hour
M["part"]=np.where(M.o<pd.Timestamp("2026-08-10 17:00",tz=PT),"Jun-Jul","Aug-Oct")
M.to_csv(f"results/vol_study_{N}.csv",index=False)
nd=M.groupby("part").o.apply(lambda x:x.dt.date.nunique())
base=M.r2h<=3.1
def row(lab,skip):
    x=M[~skip]; c=x.pnl.cumsum(); dd=-(c-c.cummax()).min(); s=M[skip]
    return (f"{lab:52s} {int(skip.sum()):5d} {s.pnl.sum():+9,.0f} | "+" / ".join(f"{x[x.part==p].pnl.sum()/nd[p]:+6,.0f}" for p in ("Jun-Jul","Aug-Oct"))
            +f" | ${x.pnl.sum()/1e3:+6.1f}K ${-dd/1e3:5.1f}K {x.pnl.sum()/dd:5.1f} | {x.stopped_ct.sum()/1e6:5.2f}M")
print(f"{len(M)} markets Jun 1 - Oct 19 ({N}), {M.o.dt.date.nunique()} days. Avg ${M.pnl.mean():+.1f}/market.")
print("\n$ per market / % with a stop, by feature bucket (all markets, no cap):")
for k,lab in (("r2h","2h range %"),("rel2h","2h range vs its 14-day time-of-day median (x)"),("rel24","24h range vs 14-day median (x)"),("r30","30-min range %"),("r15","15-min range %"),("jump15","biggest 1-min move in last 15 min %")):
    q=M[k].quantile([.5,.8,.9,.95,.99]).values; e=[-1e9,*q,1e9]; labs=["0-50%","50-80%","80-90%","90-95%","95-99%","top 1%"]
    print(f"  {lab}: "+" | ".join(f"{lb} (<={b:.2f}): ${M[(M[k]>a)&(M[k]<=b)].pnl.mean():+.0f} {M[(M[k]>a)&(M[k]<=b)].stopped.gt(0).mean()*100:.0f}%" for lb,a,b in zip(labs,e[:-1],e[1:])))
print(f"\n{'rule (skip the whole market if ...)':52s} {'skip':>5s} {'skip $':>9s} | per day Jun-Jul / Aug-Oct | total    DD   t/DD | stopped")
print(row("no cap",np.zeros(len(M),bool)))
for c in (3.1,2.5,2.0,1.5): print(row(f"2h range > {c}%"+(" (now)" if c==3.1 else ""),M.r2h>c))
R=[("relative 2h range > 2x",M.rel2h>2),("relative 2h range > 2.5x",M.rel2h>2.5),("relative 2h range > 3x",M.rel2h>3),
   ("24h range > 1.5x its normal",M.rel24>1.5),("24h range > 2x its normal",M.rel24>2),
   ("30-min range > 0.7% and moved against our side",(M.r30e>0.7)&(M.m30e<=0)),("30-min range > 0.9% and moved against our side",(M.r30e>0.9)&(M.m30e<=0)),
   ("1-min move > 0.3% in last 15 min",M.jump15>0.3),("1-min move > 0.4% in last 15 min",M.jump15>0.4),("1-min move > 0.5% in last 15 min",M.jump15>0.5),
   ("NFP/CPI/FOMC: release window",near({"jobs","cpi","fomc"},[0])),("NFP/CPI/FOMC: window before + release",near({"jobs","cpi","fomc"},[-1,0])),
   ("NFP/CPI/FOMC: release + next hour",near({"jobs","cpi","fomc"},[0,1,2,3])),("NFP/CPI/FOMC: before + release + next hour",near({"jobs","cpi","fomc"},[-1,0,1,2,3])),
   ("FOMC 11:00-12:00 PT",near({"fomc"},[0,1,2,3])),("all releases (no claims): release + next hour",near({"jobs","cpi","ppi","gdp_pce","retail","fomc"},[0,1,2,3])),
   ("Thursday claims: release + next hour",near({"claims"},[0,1,2,3])),("US morning 6-9 AM PT (all days)",(M.hour>=6)&(M.hour<9))]
for lab,m in R: print(row(lab+"  [+ 3.1% cap]",base.eq(False)|m.fillna(False)))