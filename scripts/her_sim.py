# Her rule, configurable (run from data/ or data_recent/: python3 -P ../scripts/her_sim.py 67 composite [days]).
# Base = working model: every 10 s from min 2, threshold 0.15% -> 0.05% (linear to min 15) past the official strike; buy 100 at market if
# ask < CAP; up to 50 entries; each entry its own 30% stop (market sell); resting sell at 99.5c; else settle.
# ROLL: once every open entry on a side is closed (stopped), new entries may go on the other side if it passes the threshold.
# FAST: a stop sells at the first second with trades after the trigger second (no 1 s wait, no 3 s average).
import sys,math
from zoneinfo import ZoneInfo
src=open("../scripts/her_full.py").read(); exec(src[:src.index("def sim2")])
KS={ts(x["close_time"])-900:float(x["floor_strike"]) for x in meta if x.get("floor_strike")}
PT=ZoneInfo("America/Los_Angeles")
def mfee(p,n): return math.ceil(0.0175*n*p*(1-p)*100-1e-9)/100
def sell_fast(K,t,side):
    o="n" if side=="y" else "y"
    for u in range(t,t+4):
        a=K.get(u)
        if a and a[o][1]>0: return 1-a[o][2]/a[o][1]
    return None
def run(CAP=0.985,X=0.30,TP=0.995,ROLL=False,FAST=False,PER=100,MAXN=50,START=0.0015,OPENCAP=False):
    out=[]
    for y,op,path,K in W:
        r=R[op]; K0=KS.get(op)
        if r!=r or r>0.031 or K0 is None: continue
        side=None; lots=[]; n=0; pnl=0.0; lastp=None; nstop=0; rolls=0; sides=set()
        for u in range(120,900):
            a=K.get(op+u)
            if side and a:
                o="n" if side=="y" else "y"; q=a[side][1]+a[o][1]
                if q>0: lastp=(a[side][2]+(a[o][1]-a[o][2]))/q
                if lots and a[side][1]>0 and a[side][2]/a[side][1]>=TP:
                    for e in lots: pnl+=PER*TP-mfee(TP,PER)-(PER*e+fee(e,PER))
                    lots=[]
            if side and lots and lastp is not None:
                hit=[e for e in lots if lastp<=e*(1-X)]
                if hit:
                    b=sell_fast(K,op+u+1,side) if FAST else sell_px(K,op+u+1,side)
                    if b is not None:
                        for e in hit: pnl+=PER*b-fee(b,PER)-(PER*e+fee(e,PER)); lots.remove(e); nstop+=1
            if u%10==0 and u<=880 and (len(lots)<MAXN if OPENCAP else n<MAXN):
                th=START+(0.0005-START)*(u-120)/780; m=(path[60+u]-K0)/K0
                s2="y" if m>=th else ("n" if -m>=th else None)
                if s2 and (side is None or s2==side or (ROLL and not lots)):
                    e=buy_px(K,op+u+1,s2)
                    if e is not None and e<CAP:
                        if side and s2!=side: rolls+=1; lastp=None
                        side=s2; sides.add(s2); n+=1; lots.append(e)
        if not side: continue
        win=y if side=="y" else not y
        for e in lots: pnl+=(PER if win else 0)-(PER*e+fee(e,PER))
        out.append(dict(op=op,part=part(op),n=n*PER,pnl=pnl,stops=nstop,rolls=rolls,day=pd.Timestamp(op,unit="s",tz="UTC").tz_convert(PT).strftime("%a %b %d")))
    return pd.DataFrame(out)
days=len(W)/96; pdd={p:sum(1 for w in W if part(w[1])==p)/96 for p in ("train","val","test")}
if len(sys.argv)>4: CF=[("fast+roll, 5,000 = max entries per market",dict(FAST=True,ROLL=True)),("fast+roll, 5,000 = max held at once",dict(FAST=True,ROLL=True,OPENCAP=True))]
else: CF=[("base (30% stop, 98.5c cap)",{}),("+ fast stop sale",dict(FAST=True)),("+ roll to other side",dict(ROLL=True)),
    ("+ fast stop + roll",dict(FAST=True,ROLL=True)),("+ fast stop + roll, 96c cap",dict(FAST=True,ROLL=True,CAP=0.96))]
if len(sys.argv)>4: CF=CF[:2]
print(f"{days:.1f} days, 100 contracts per entry\n{'rule':32s} | {'mkts/d':>6s} {'bought':>6s} {'rolls/d':>7s} | {'$/ct':>6s} | {'train $/d':>9s} {'val $/d':>8s} {'test $/d':>9s} | {'all $/day':>9s}")
for nm,kw in CF:
    d=run(**kw); dd={p:d[d.part==p].pnl.sum()/(pdd[p] or 1) for p in pdd}
    print(f"{nm:32s} | {len(d)/days:6.1f} {d.n.mean():6.0f} {d.rolls.sum()/days:7.1f} | {d.pnl.sum()/d.n.sum()*100:+5.2f}c | {dd['train']:+9,.0f} {dd['val']:+8,.0f} {dd['test']:+9,.0f} | {d.pnl.sum()/days:+9,.0f}")
    if len(sys.argv)>3:
        print("    "+"  ".join(f"{dy[:3]} {dy[-2:]}: ${x.pnl.sum():+,.0f}" for dy,x in d.groupby("day",sort=False)))
