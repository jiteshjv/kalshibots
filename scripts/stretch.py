# Direct "stretch" at each market's first entry (mixed, filters off): distance of BTC from its 1h / 4h average in our direction, in ATR units
# (ATR = mean 15-min high-low over the prior 24 h). (run from project root: python3 -P scripts/stretch.py)
import numpy as np,pandas as pd
Z=np.load("data/prices_1s.npz"); T0=int(Z["T0"]); P=np.load("data/composite_1s.npy")
Y=pd.read_csv("results/multiday_levels.csv"); E=pd.read_csv("results/her_66d_entries.csv").sort_values(["ticker","entry_no"])
f=E.groupby("ticker").first(); sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
rows=[]
for r in Y.itertuples():
    g=f.loc[r.ticker]; i=r.op+sec(g.entry_time)-1-T0; d=1 if g.side.startswith("YES") else -1; p=P[i]
    bars=P[i-86400:i-86400+96*900].reshape(96,900); atr=np.nanmean(np.nanmax(bars,1)-np.nanmin(bars,1))
    rows.append(dict(s60=d*(p-np.nanmean(P[i-3600:i]))/atr, s240=d*(p-np.nanmean(P[i-14400:i]))/atr, s15=d*(p-np.nanmean(P[i-900:i]))/atr))
Y=pd.concat([Y.reset_index(drop=True),pd.DataFrame(rows)],axis=1); Y.to_csv("results/stretch.csv",index=False)
for k,lab in (("s15","15-min average"),("s60","1-hour average"),("s240","4-hour average")):
    print(f"\nBTC vs its {lab}, in ATRs our way (all markets ${Y.pnl.mean():+.0f}/mkt, bad {Y.bad.mean()*100:.0f}%):")
    for lo,hi in ((-99,0),(0,0.5),(0.5,1),(1,1.5),(1.5,2),(2,3),(3,99)):
        x=Y[(Y[k]>=lo)&(Y[k]<hi)]
        if len(x)<15: print(f"   {lo:+5.1f} to {hi:+5.1f}: {len(x):4d} mkts (too few)"); continue
        print(f"   {lo:+5.1f} to {hi:+5.1f}: {len(x):4d} mkts  bad {x.bad.mean()*100:3.0f}%  ${x.pnl.mean():+5.0f}/mkt | "+"/".join(f"{x[x.part==p].pnl.mean():+.0f}" for p in ("train","val","test")))
print("\nSkip rule tests (what the skipped markets made; negative = skipping helps):")
for k in ("s60","s240"):
    for c in (1.5,2,2.5,3):
        x=Y[Y[k]>=c]; print(f"   skip if {k} >= {c}: {len(x):4d} mkts, bad {x.bad.mean()*100:3.0f}%, made ${x.pnl.sum():+,.0f} | "+"/".join(f"{x[x.part==p].pnl.sum():+,.0f}" for p in ("train","val","test")))
