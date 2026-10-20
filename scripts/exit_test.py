# Exit-rule grid for her entry (>= mny past start, buy 65-75c as taker, 1s latency), with the rule_search date split.
# (run from data/: python3 -P ../scripts/exit_test.py 67 composite)
# Exits, checked every second after entry (earliest trigger wins):
#   TP  : resting sell at tp; filled when side-takers pay >= tp (opt) or >= tp+1c (cons)
#   SL  : side's last traded price <= entry - sl  -> sell as taker 1s later (1 - opposite-taker VWAP)
#   X   : BTC proxy back at/through the start price -> sell as taker 1s later
#   TIME: at second texit, sell as taker
# Otherwise settle. 5 contracts, per-order rounded taker fees on both legs.
import math,statistics as st,numpy as np,pandas as pd
src=open("../scripts/friend_v3.py").read(); exec(src[:src.index('print("Rules:')])
ops=np.sort(pd.read_pickle("cands.pkl").op.unique()); A,B=ops[int(.6*len(ops))],ops[int(.8*len(ops))]
part=lambda op:"train" if op<A else ("val" if op<B else "test")
pdays={p:sum(1 for w in W if part(w[1])==p)/96 for p in ("train","val","test")}
N=5
def entries(mny,lo=0.65,hi=0.75,lat=1):
    E=[]
    for y,op,path,K in W:
        ref=sum(path[:60])/60
        for s in range(120,870):
            m=(path[60+s]-ref)/ref
            side="y" if m>=mny else ("n" if -m>=mny else None)
            if not side: continue
            e=buy_px(K,op+s+lat,side)
            if e is None or not (lo<=e<=hi): continue
            E.append((y,op,path,K,ref,s+lat+2,side,e)); break
    return E
def sim(E,tp,sl,cross,texit,mode):
    out=[]
    for y,op,path,K,ref,t0,side,e in E:
        win=y if side=="y" else not y; cost=e*N+fee(e,N); res=None; last=e
        for u in range(t0+1,900):
            a=K.get(op+u)
            if a:
                o="n" if side=="y" else "y"; q=a[side][1]+a[o][1]
                if tp is not None and a[side][1]>0 and a[side][2]/a[side][1]>=(tp if mode=="opt" else tp+0.01):
                    res=tp*N-cost-fee(tp,N); break
                if q>0: last=(a[side][2]+(a[o][1]-a[o][2]))/q
            trig=(sl is not None and last<=e-sl) or (cross and ((path[60+u]-ref)*(1 if side=="y" else -1)<=0)) or (texit and u>=texit)
            if trig:
                b=sell_px(K,op+u+1,side)
                if b is not None: res=b*N-cost-fee(b,N); break
        if res is None: res=(N if win else 0)-cost
        out.append((part(op),res/N))
    return out
def summ(r,p):
    v=[x for q,x in r if q==p]
    return f"{len(v)/pdays[p]:4.1f}/d {np.mean(v)*100:+5.2f}c±{1.96*np.std(v)/math.sqrt(len(v))*100:.2f} win{np.mean(np.array(v)>0)*100:4.0f}%"
print(f"windows {len(W)}; split train<{pd.to_datetime(A,unit='s'):%b %d} val<{pd.to_datetime(B,unit='s'):%b %d} test after\n")
for mny in (0.0010,0.0015):
    E=entries(mny)
    print(f"=== entry: >= {mny*100:.2f}% past start, buy 65-75c taker: {len(E)} trades ===")
    print(f"{'TP':>4s} {'SL':>4s} {'cross':5s} {'time':>4s} {'fill':4s} | {'train':28s} | {'val':28s} | test")
    rows=[]
    for tp in (None,0.85,0.90,0.95):
        for sl in (None,0.15,0.25,0.35):
            for cross in (False,True):
                for texit in (None,780,840):
                    for mode in (("opt","cons") if tp else ("opt",)):
                        r=sim(E,tp,sl,cross,texit,mode)
                        mt=np.mean([x for q,x in r if q=="train"]); mv=np.mean([x for q,x in r if q=="val"])
                        rows.append((min(mt,mv),tp,sl,cross,texit,mode,r))
    rows.sort(key=lambda z:-z[0])
    for k,(_,tp,sl,cross,texit,mode,r) in enumerate(rows):
        if k<12 or (tp==0.95 and sl is None and not cross and texit is None and mode=="opt") or (tp is None and sl is None and not cross and texit is None):
            tag=" <- baseline" if (tp in (None,0.95) and sl is None and not cross and texit is None) else ""
            print(f"{'-' if tp is None else int(tp*100):>4} {'-' if sl is None else int(sl*100):>4} {str(cross):5s} {'-' if texit is None else texit//60:>4} {mode:4s} | {summ(r,'train'):28s} | {summ(r,'val'):28s} | {summ(r,'test')}{tag}")
    print(f"({len(rows)} exit combos; sorted by the WORSE of train/val; test shown but not used to choose)\n")
