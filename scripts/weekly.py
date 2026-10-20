# Weekly + volatility-regime breakdown for one friend-style config (run from data/). Reuses friend_v3 setup.
import sys,collections
src=open("../scripts/friend_v3.py").read(); exec(src[:src.index('print("Rules:')])
import numpy as np
def run_dated(mny,persist,lo,hi,exitrule,fill95):
    global W
    out=[]
    for w in W:
        Wsave=W; W=[w]; r=run(mny,persist,lo,hi,exitrule,fill95); W=Wsave
        if r:
            y,op,path,K=w; ref=sum(path[:60])/60
            vol=(max(path[:60+120])-min(path[:60+120]))/ref    # crude: range over first 3 min of window
            # 2h range before window from composite
            i=op-T0; seg=ARR[i-7200:i]; vol2h=(np.nanmax(seg)-np.nanmin(seg))/np.nanmean(seg)
            out.append((op,r[0][0],vol2h))
    return out
for cfg in ((0.0015,0,0.76,0.85,"hold","opt"),(0.0020,4,0.76,0.85,"hold","opt")):
    r=run_dated(*cfg)
    print(f"\nconfig {cfg}: {len(r)} trades")
    wk=collections.defaultdict(list)
    for op,p,_ in r: wk[dt.datetime.fromtimestamp(op,dt.timezone.utc).strftime("%G-W%V")].append(p)
    for k in sorted(wk): v=wk[k]; print(f"  {k}: {len(v):4d} trades  win {sum(a>0 for a in v)/len(v)*100:5.1f}%  P&L/ct {np.mean(v)*100:+6.2f}c")
    vs=np.array([v for *_,v in r]); ps=np.array([p for _,p,_ in r]); qs=np.quantile(vs,[0,1/3,2/3,1])
    for a,b in zip(qs,qs[1:]):
        m=(vs>=a)&(vs<=b); print(f"  2h range {a*100:.2f}-{b*100:.2f}%: {m.sum():4d} trades  win {np.mean(ps[m]>0)*100:5.1f}%  P&L/ct {ps[m].mean()*100:+6.2f}c")
