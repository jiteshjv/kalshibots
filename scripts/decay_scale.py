# Her scaling-in rule (from her chat 2026-10-19): moneyness threshold decays to 0.05% by min 15; buy 100 contracts at each qualifying
# snapshot (every 10 s from min 2), up to 50 entries (5,000 contracts) per market; hold to settlement (no stop in this first pass).
# Uses snap10.pkl (real ask at each snapshot, taker fee). (run from data/ or data_recent/: python3 -P ../scripts/decay_scale.py)
import pandas as pd,numpy as np,math,sys
S=pd.read_pickle("snap10.pkl"); S=S[S.r2<=0.031]
days=S.op.nunique() and (S.op.max()-S.op.min())/86400+1/96
def thr(s,start,shape):
    f=min(max((s-120)/(900-120),0),1)
    return start+(0.0005-start)*(f if shape=="linear" else math.sqrt(f))
def run(start,shape,cap,maxn=50,per=100):
    g=S[S.e<=cap].copy(); g["th"]=[thr(s,start,shape) for s in g.s]; g=g[g.m>=g.th]
    g=g.sort_values(["op","s"]); g=g.groupby("op").head(maxn)
    w=g.groupby("op").agg(n=("pnl","size"),pnl=("pnl","sum"),part=("part","first"),e=("e","mean"),win=("win","mean"))
    return g,w
print(f"{days:.1f} days. Per contract after taker fee; $ at 100 contracts per entry.\n")
print(f"{'threshold':28s} {'cap':>4s} | {'mkts/d':>6s} {'entries/mkt':>11s} {'avg buy':>7s} | {'P&L/ct':>7s} | {'train $/d':>9s} {'val $/d':>8s} {'test $/d':>9s} | {'all $/day':>9s}")
for shape in ("linear","sqrt"):
  for start in (0.0010,0.00125,0.0015,0.0020):
    for cap in (0.95,0.97):
        g,w=run(start,shape,cap)
        pc=g.pnl.mean(); dd={}
        for p in ("train","val","test"):
            x=w[w.part==p]; nd=S[S.part==p].op.nunique()/96 if (S.part==p).any() else 0
            dd[p]=x.pnl.sum()*100/nd if nd else float('nan')
        print(f"{shape} {start*100:.3f}%->0.05%{'':6s} {int(cap*100):>3d}c | {len(w)/days:6.1f} {w.n.mean():11.1f} {g.e.mean()*100:6.1f}c | {pc*100:+6.2f}c | "
              f"{dd['train']:+9,.0f} {dd['val']:+8,.0f} {dd['test']:+9,.0f} | {g.pnl.sum()*100/days:+9,.0f}")
