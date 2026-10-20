# Liquidity levels at the first entry: did price just run through a prior swing high/low (stops above/below = liquidity) or sit at a round
# number? Signed in our direction: "swept" = in the last 5 min price traded beyond the prior 1h/4h/24h extreme on our side.
# (run from project root after bad_entries.py: python3 -P scripts/liq_levels.py)
import numpy as np,pandas as pd,json,datetime as dt
Z=np.load("data/prices_1s.npz"); T0=int(Z["T0"]); P=np.load("data/composite_1s.npy")
D=pd.read_csv("results/bad_entries_features.csv"); E=pd.read_csv("results/her_66d_entries.csv").sort_values(["ticker","entry_no"])
f=E.groupby("ticker").first(); sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
out=[]
for r in D.itertuples():
    g=f.loc[r.ticker]; t=r.op+sec(g.entry_time)-1; i=t-T0; d=1 if g.side.startswith("YES") else -1; p=P[i]
    row={}
    for lab,k in (("1h",3600),("4h",14400),("24h",86400)):
        if i-k-300<0: row[f"swept_{lab}"]=np.nan; row[f"dist_{lab}"]=np.nan; continue
        prior=P[i-k-300:i-300]; ext=np.nanmax(prior) if d>0 else np.nanmin(prior)     # the prior extreme on our side, before the last 5 min
        last5=P[i-300:i+1]; reach=np.nanmax(last5) if d>0 else np.nanmin(last5)
        row[f"swept_{lab}"]=int(d*(reach-ext)>0)                                      # ran through that level in the last 5 min
        row[f"dist_{lab}"]=d*(p-ext)/p*100                                            # + = beyond it now, - = still short of it
    for step in (1000,500,250):
        lvl=round(p/step)*step; row[f"round{step}"]=abs(p-lvl)
        lo,hi=sorted((P[i-300],p)); row[f"crossed_round{step}"]=int(any(lo<=L<=hi for L in range(int(lo//step)*step,int(hi//step+1)*step+1,step)))
    out.append(row)
L=pd.concat([D,pd.DataFrame(out)],axis=1); ops=np.sort(L.op.unique()); A,B=ops[int(.6*len(ops))],ops[int(.8*len(ops))]; L["part"]=np.where(L.op<A,"train",np.where(L.op<B,"val","test")); L.to_csv("results/liq_features.csv",index=False)
print(f"{len(L)} markets, bad = first side fully stopped (base rate {L.bad.mean()*100:.1f}%)\n")
print(f"{'condition at first entry':52s} {'markets':>7s} {'bad%':>5s} {'$/mkt':>6s} | train / val / test $/mkt")
def show(lab,mask):
    x=L[mask]; o=L[~mask]
    print(f"{lab:52s} {len(x):7d} {x.bad.mean()*100:5.1f} {x.pnl.mean():+6.0f} | {'/'.join(f'{x[x.part==p].pnl.mean():+.0f}' for p in ('train','val','test'))}   (others {o.pnl.mean():+.0f})")
for lab in ("1h","4h","24h"):
    show(f"just ran through the prior {lab} high/low (our side)",L[f"swept_{lab}"]==1)
    show(f"  ...still short of it",L[f"dist_{lab}"]<0)
for s in (1000,500,250): show(f"crossed a ${s} round number in the last 5 min",L[f"crossed_round{s}"]==1)
for s in (1000,500): show(f"within $50 of a ${s} round number",L[f"round{s}"]<=50)
show("1h move our way in the top 20% (extended)",L.m60>=L.m60.quantile(.8))
show("price > 1h average by top 20% (stretched)",L.vs_ma60>=L.vs_ma60.quantile(.8))
show("ran through 4h level AND extended 1h",(L.swept_4h==1)&(L.m60>=L.m60.quantile(.8)))
show("strike crossed 0-2 times so far (clean)",L.crosses<=2)
show("strike crossed 6+ times so far (choppy)",L.crosses>=6)
