# Which BTC sweep filters survive out of sample? Unfiltered 25%/0.03% runs; the filtered runs' skip list gives each market's sweep label
# (longest level run through: 2/3/5/7/14/30-day, or "4h sweep + stretched 1h"). Rules are whole-market skips chosen on Jun-Jul (train)
# and checked on Aug 1 - Oct 20 (test).  python3 -P scripts/sweep_split.py
import pandas as pd,numpy as np,os
def load(h,l,kind="markets"):
    fs=[f"results/{h}_{m}_{kind}.csv" for m in ("jun","jul","aug")]+[f"results/{p}_{kind}.csv" for p in (f"{l}_66d",f"{l}_recent",f"{l}_oct20",f"{l}_mon")]
    return pd.concat([pd.read_csv(f) for f in fs if os.path.exists(f) and os.path.getsize(f)>2]).drop_duplicates("ticker",keep="last")
LV={"2-day sweep":2,"3-day sweep":3,"5-day sweep":5,"7-day sweep":7,"14-day sweep":14,"30-day sweep":30}
def dd(p): c=np.cumsum(p); return -(c-np.maximum.accumulate(c)).min() if len(p) else 0
for lab,(h0,l0),(h1,l1) in (("Equal 100",("jh_eq_s25l3","v9_eq_s25l3"),("jh_eq_s25l3_f","v9_eq_s25l3_f")),("Mixed",("jh_s25l3off","v9_s25l3"),("jh_s25l3","v9_s25l3_f"))):
    M=load(h0,l0); S=load(h1,l1,"skipped").set_index("ticker").why
    M["o"]=pd.to_datetime(M.day+" 2026 "+M.window_pt,format="%a %b %d %Y %I:%M %p"); M=M[M.o>="2026-06-01"].sort_values("o").reset_index(drop=True)
    M["lv"]=M.ticker.map(lambda t:LV.get(S.get(t),0)); M["h4"]=M.ticker.map(lambda t:S.get(t)=="4h sweep + stretched 1h")
    tr=M.o<"2026-08-01"; nd={p:M[m].o.dt.date.nunique() for p,m in (("train",tr),("test",~tr))}
    R=[("no sweep filter",M.lv<0),("current: 2-day+ and 4h",(M.lv>=2)|M.h4),("4h only",M.h4)]
    for a in (2,3,5,7,14,30): R.append((f">= {a}-day sweep",M.lv>=a))
    for a,b in ((3,14),(3,7),(5,14)): R.append((f"{a}- to {b}-day sweep",(M.lv>=a)&(M.lv<=b)))
    print(f"\n{lab}, 25%/0.03% stop: train Jun-Jul ({nd['train']} days), test Aug 1 - Oct 20 ({nd['test']} days). $ = vs no filter; skipped markets' own P&L in brackets")
    print(f"{'skip rule':26s} | {'train: skips':>12s} {'$ vs none':>9s} {'DD':>7s} | {'test: skips':>11s} {'$ vs none':>9s} {'DD':>7s}")
    for nm,sk in R:
        out=[]
        for m in (tr,~tr):
            x=M[m]; s=sk[m]; out+= [int(s.sum()), -x[s].pnl.sum(), dd(x.pnl.values*~s.values)]
        print(f"{nm:26s} | {out[0]:12d} {out[1]:+9,.0f} {out[2]:7,.0f} | {out[3]:11d} {out[4]:+9,.0f} {out[5]:7,.0f}")
