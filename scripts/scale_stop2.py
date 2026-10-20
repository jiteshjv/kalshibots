# Her scale-in rule v2 (her chat, 2026-10-19 ~1:10 PM PT): each 100-contract entry has its own % stop (avg buy 90c -> avg stop 68c, ~25%);
# she keeps buying (and re-buys after stops) while the decaying moneyness holds, until the price reaches 98.5c; up to 50 entries; exit = hold,
# or sell everything once our side trades at 99.5c. (run from data/ or data_recent/: python3 -P ../scripts/scale_stop2.py 67 composite [days])
import sys,math
from zoneinfo import ZoneInfo
src=open("../scripts/her_full.py").read(); exec(src[:src.index("def sim2")])
KS={ts(x["close_time"])-900:float(x["floor_strike"]) for x in meta if x.get("floor_strike")}
PT=ZoneInfo("America/Los_Angeles")
STX=[]
def cost_of(e,PER):            # lots stored as +price (taker fill) or -price (limit fill, maker fee)
    return PER*abs(e)+(fee(e,PER) if e>0 else mfee(-e,PER))
def mfee(p,n): return math.ceil(0.0175*n*p*(1-p)*100-1e-9)/100
def run(START=0.0015,CAP=0.985,X=0.25,TP=None,PER=100,MAXN=50,BTCSTOP=False,LIM=None,ABS=None):
    out=[]
    for y,op,path,K in W:
        r=R[op]; K0=KS.get(op)
        if r!=r or r>0.031 or K0 is None: continue
        side=None; lots=[]; n=0; pnl=0.0; lastp=None; nstop=0; tp_done=False; bid=None
        for u in range(120,900):
            a=K.get(op+u)
            if bid and a:                                  # resting limit entry: fills when trades go THROUGH it (back of the queue)
                L,bs,until=bid; o2="n" if bs=="y" else "y"
                lo=a[bs][0] if a[bs][1]>0 else 9; ovw=a[o2][2]/a[o2][1] if a[o2][1]>0 else -1
                if lo<L-1e-9 or ovw>1-L+1e-9: side=bs; n+=1; lots.append(-L); bid=None
                elif u>=until: bid=None
            if side and a:
                o="n" if side=="y" else "y"; q=a[side][1]+a[o][1]
                if q>0: lastp=(a[side][2]+(a[o][1]-a[o][2]))/q
                if TP and a[side][1]>0 and a[side][2]/a[side][1]>=TP and lots:
                    for e in lots: pnl+=PER*TP-mfee(TP,PER)-cost_of(e,PER)
                    lots=[]; tp_done=True; break
            if side and lots and lastp is not None:
                d=1 if side=="y" else -1
                hit=[e for e in lots if lastp<=(ABS if ABS else abs(e)*(1-X))] if not BTCSTOP else (lots if (path[60+u]-K0)*d<=0 else [])
                if hit:
                    b=sell_px(K,op+u+1,side)
                    if b is not None:
                                for e in hit: pnl+=PER*b-fee(b,PER)-cost_of(e,PER); lots.remove(e); nstop+=1; STX.append((abs(e),(ABS if ABS else abs(e)*(1-X)),b))
            if u%10==0 and u<=880 and n<MAXN:
                th=START+(0.0005-START)*(u-120)/780; m=(path[60+u]-K0)/K0
                s2="y" if m>=th else ("n" if -m>=th else None)
                if s2 and (side is None or s2==side):
                    e=buy_px(K,op+u+1,s2)
                    if e is not None and e<=CAP:
                        if LIM is None: side=s2; n+=1; lots.append(e)
                        elif bid is None: bid=(round(e-LIM,3),s2,u+10)
        if not side: continue
        win=y if side=="y" else not y
        for e in lots: pnl+=(PER if win else 0)-cost_of(e,PER)
        out.append(dict(part=part(op),n=n*PER,pnl=pnl,stops=nstop,win=win,day=pd.Timestamp(op,unit="s",tz="UTC").tz_convert(PT).strftime("%a %b %d")))
    return pd.DataFrame(out)
days=len(W)/96; pd_={p:sum(1 for w in W if part(w[1])==p)/96 for p in ("train","val","test")}
print(f"{days:.1f} days; 100 contracts per entry, up to 50 entries; taker fees (maker on 99.5c sells)\n")
print(f"{'rule':44s} | {'mkts/d':>6s} {'bought':>6s} {'lots stopped':>12s} | {'$/ct':>6s} | {'train $/d':>9s} {'val $/d':>8s} {'test $/d':>9s} | {'all $/day':>9s}")
CF=[("v1 of her rule: 97c cap, stop whole pos at strike/50c",None),
    ("98.5c cap, per-entry 20% stop, hold",dict(X=0.20)),("98.5c cap, per-entry 25% stop, hold",dict(X=0.25)),("98.5c cap, per-entry 30% stop, hold",dict(X=0.30)),
    ("98.5c cap, per-entry 25% stop, sell at 99.5c",dict(X=0.25,TP=0.995)),("98.5c cap, per-entry 20% stop, sell at 99.5c",dict(X=0.20,TP=0.995)),
    ("97c cap, per-entry 25% stop, hold",dict(X=0.25,CAP=0.97)),("98.5c cap, no stop, hold",dict(X=9))]
if len(sys.argv)>4 and sys.argv[4]=="s30": CF=[("25% per entry, sell 99.5c",dict(X=0.25,TP=0.995)),("30% per entry, sell 99.5c",dict(X=0.30,TP=0.995)),("30% per entry, hold",dict(X=0.30))]
elif len(sys.argv)>4 and sys.argv[4]=="stops": CF=[("25% per entry, sell 99.5c",dict(X=0.25,TP=0.995)),("fixed 68c for every entry, sell 99.5c",dict(TP=0.995,ABS=0.68)),
    ("fixed 70c, sell 99.5c",dict(TP=0.995,ABS=0.70)),("fixed 65c, sell 99.5c",dict(TP=0.995,ABS=0.65)),("fixed 60c, sell 99.5c",dict(TP=0.995,ABS=0.60))]
elif len(sys.argv)>4: CF=[("market entries, 25% stop, sell 99.5c",dict(X=0.25,TP=0.995)),("LIMIT 1c under, 25% stop, sell 99.5c",dict(X=0.25,TP=0.995,LIM=0.01)),
    ("LIMIT 2c under, 25% stop, sell 99.5c",dict(X=0.25,TP=0.995,LIM=0.02)),("LIMIT at 0.5c under, 25% stop, sell 99.5c",dict(X=0.25,TP=0.995,LIM=0.005)),
    ("market entries, 25% stop, hold",dict(X=0.25)),("LIMIT 1c under, 25% stop, hold",dict(X=0.25,LIM=0.01))]
for nm,kw in CF:
    if kw is None: continue
    STX.clear(); d=run(**kw); dd={p:d[d.part==p].pnl.sum()/(pd_[p] or 1) for p in pd_}
    print(f"{nm:44s} | {len(d)/days:6.1f} {d.n.mean():6.0f} {d.stops.sum()*100/max(d.n.sum()/100,1):11.0f}% | {d.pnl.sum()/d.n.sum()*100:+5.2f}c | "
          f"{dd['train']:+9,.0f} {dd['val']:+8,.0f} {dd['test']:+9,.0f} | {d.pnl.sum()/days:+9,.0f}")
    if STX: a=np.array(STX); print(f"{'':44s}   stopped lots: avg buy {a[:,0].mean()*100:.1f}c, avg stop level {a[:,1].mean()*100:.1f}c, avg sale {a[:,2].mean()*100:.1f}c (slippage {(a[:,1]-a[:,2]).mean()*100:.1f}c)")
    if len(sys.argv)>3 and sys.argv[3]=='days' and kw in (dict(X=0.25),dict(X=0.25,TP=0.995),dict(X=0.30,TP=0.995)):
        for day,x in d.groupby("day",sort=False): print(f"     {day}: {len(x):3d} markets, bought avg {x.n.mean():5.0f}, {x.stops.sum():3d} lots stopped, ${x.pnl.sum():+9,.0f}")
