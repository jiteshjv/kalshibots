# Her scale-in rule with a stop, second by second (run from data/ or data_recent/: python3 -P ../scripts/scale_stop.py 67 composite).
# Every 10 s from min 2: if BTC >= thr(s) past the official strike (thr decays linearly from START to 0.05% at min 15) and the leading ask
# is <= CAP, buy 100 contracts as taker (real prints, 1 s latency); up to 50 entries. Side = first side entered (no flipping).
# Stop on the whole position: BTC back at the strike OR our side trades <= 50c -> sell all as taker; no more entries. Else hold.
import sys,math
from zoneinfo import ZoneInfo
src=open("../scripts/her_full.py").read(); exec(src[:src.index("def sim2")])
KS={ts(x["close_time"])-900:float(x["floor_strike"]) for x in meta if x.get("floor_strike")}
PT=ZoneInfo("America/Los_Angeles")
def run(START,CAP,STOP=True,PER=100,MAXN=50):
    out=[]
    for y,op,path,K in W:
        r=R[op]; K0=KS.get(op)
        if r!=r or r>0.031 or K0 is None: continue
        side=None; n=0; cost=0.0; lastp=None; res=None; stopped=False
        for u in range(120,900):
            a=K.get(op+u)
            if side and a:
                o="n" if side=="y" else "y"; q=a[side][1]+a[o][1]
                if q>0: lastp=(a[side][2]+(a[o][1]-a[o][2]))/q
            if side and STOP and ((path[60+u]-K0)*(1 if side=="y" else -1)<=0 or (lastp is not None and lastp<=0.50)):
                b=sell_px(K,op+u+1,side)
                if b is not None: res=n*PER*b-fee(b,n*PER)-cost; stopped=True; break
            if u%10==0 and u<=880 and n<MAXN:
                f=(u-120)/780; th=START+(0.0005-START)*f; m=(path[60+u]-K0)/K0
                s2="y" if m>=th else ("n" if -m>=th else None)
                if s2 and (side is None or s2==side):
                    e=buy_px(K,op+u+1,s2)
                    if e is not None and e<=CAP: side=s2; n+=1; cost+=PER*e+fee(e,PER)
        if not side: continue
        win=y if side=="y" else not y
        if res is None: res=(n*PER if win else 0)-cost
        out.append(dict(op=op,part=part(op),n=n*PER,pnl=res,stopped=stopped,win=win,day=pd.Timestamp(op,unit="s",tz="UTC").tz_convert(PT).strftime("%a %b %d")))
    return pd.DataFrame(out)
days=len(W)/96
print(f"{days:.1f} days; $ per day at 100 contracts per entry (max 5,000 per market), taker fees\n")
print(f"{'rule':34s} | {'mkts/d':>6s} {'avg size':>8s} {'stop%':>5s} | {'$/ct':>6s} | {'train $/d':>9s} {'val $/d':>8s} {'test $/d':>9s} | {'all $/day':>9s}")
for START in (0.0015,0.0020):
    for CAP in (0.95,0.97):
        for STOP in (True,False):
            d=run(START,CAP,STOP); dd={p:d[d.part==p].pnl.sum()/(sum(1 for w in W if part(w[1])==p)/96 or 1) for p in ("train","val","test")}
            print(f"{START*100:.2f}%->0.05%, <= {int(CAP*100)}c, {'stop' if STOP else 'no stop':7s} | {len(d)/days:6.1f} {d.n.mean():8.0f} {d.stopped.mean()*100:5.0f} | {d.pnl.sum()/d.n.sum()*100:+5.2f}c | "
                  f"{dd['train']:+9,.0f} {dd['val']:+8,.0f} {dd['test']:+9,.0f} | {d.pnl.sum()/days:+9,.0f}")
            if len(sys.argv)>3 and START==0.0015 and CAP==0.97 and STOP:
                for day,x in d.groupby("day",sort=False): print(f"     {day}: {len(x):3d} markets, avg {x.n.mean():5.0f} ct, {x.stopped.sum():2d} stops, ${x.pnl.sum():+9,.0f}")
