# Fast vs slow volatility (BTC, results/herR_66d = no range cap, filters on, mixed). For each market: range over the 15 / 30 min before the
# window opens and over the 5 min before the first entry, plus the 2h range; P&L by bucket and simulated whole-market skips.
import numpy as np,pandas as pd
from zoneinfo import ZoneInfo
PT=ZoneInfo("America/Los_Angeles")
Z=np.load("data/prices_1s.npz"); T0=int(Z["T0"]); P=np.load("data/composite_1s.npy")
M=pd.read_csv("results/herR_66d_markets.csv"); E=pd.read_csv("results/herR_66d_entries.csv")
M["o"]=pd.to_datetime(M.day+" 2026 "+M.window_pt,format="%a %b %d %Y %I:%M %p").dt.tz_localize(PT); M["ts"]=M.o.astype("int64")//10**9
sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
fe=E.groupby("ticker").entry_time.first().map(sec); M["fe"]=M.ticker.map(fe)
def rng(a,b):
    s=P[max(a-T0,0):b-T0]; s=s[~np.isnan(s)]; return (s.max()/s.min()-1)*100 if len(s) else np.nan
M["r15"]=[rng(t-900,t) for t in M.ts]; M["r30"]=[rng(t-1800,t) for t in M.ts]; M["r2h"]=[rng(t-7200,t) for t in M.ts]
M["r5e"]=[rng(t+f-300,t+f) if f==f else np.nan for t,f in zip(M.ts,M.fe)]
M["conc"]=M.r30/M.r2h
M=M.sort_values("o"); ops=np.sort(M.o.unique()); A,B=ops[int(.6*len(ops))],ops[int(.8*len(ops))]; M["pp"]=np.where(M.o<A,"train",np.where(M.o<B,"val","test"))
M.to_csv("results/fast_vol_features.csv",index=False); nd=M.groupby("pp").day.nunique()
def row(lab,x):
    c=x.pnl.cumsum(); return f"{lab:40s} {len(x):5d} | "+" / ".join(f"{x[x.pp==p].pnl.sum()/nd[p]:+,.0f}" for p in ("train","val","test"))+f" | ${x.pnl.sum():+,.0f} | DD ${(c-c.cummax()).min():,.0f}"
print("$ per market by bucket (fifths, then top tail):")
for k,l in (("r15","15-min range before open"),("r30","30-min range before open"),("r5e","5-min range before 1st entry"),("conc","30m / 2h range (how concentrated)")):
    q=M[k].quantile([.2,.4,.6,.8,.95,.99]).values
    edges=[-1,*q,1e9]; labs=["0-20","20-40","40-60","60-80","80-95","95-99","99+"]
    print(f"  {l}: "+" | ".join(f"{lb}% (<={e:.2f}): ${M[(M[k]>a)&(M[k]<=e)].pnl.mean():+.0f}, {M[(M[k]>a)&(M[k]<=e)].stopped.gt(0).mean()*100:.0f}% stop" for lb,a,e in zip(labs,edges[:-1],edges[1:])))
print(f"\n{'skip rule':40s} {'kept':>5s} | per day train/val/test | total | max DD")
print(row("no cap",M)); print(row("2h range <= 3.1% (current)",M[M.r2h<=3.1]))
for k,cs in (("r15",(0.6,0.8,1.0,1.2)),("r30",(0.8,1.0,1.2,1.5)),("r5e",(0.3,0.4,0.5,0.6))):
    for c in cs: print(row(f"current + skip if {k} > {c}%",M[(M.r2h<=3.1)&~(M[k]>c)]))
