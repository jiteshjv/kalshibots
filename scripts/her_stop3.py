# Smarter stop for the best sizing version (200/entry before min 6 then 100, no entries after min 12, <= 5,000 held, fast stop, roll,
# sell 99.5c). Stop variants decide WHEN a 30%-down entry is sold. (run from data/ or data_recent/: python3 -P ../scripts/her_stop3.py 67 composite)
import sys,math
src=open("../scripts/her_sim.py").read(); exec(src[:src.index("def run(")])
def run(mode="price",LEAD=0.0,CONF=1,CAP=0.985,X=0.30,TP=0.995,MAXHELD=5000,START=0.0015,LAST=720,END=900,FLOOR=0.0005,FIRST=120,STEP=10,SZF=1.0):
    out=[]
    for y,op,path,K in W:
        r=R[op]; K0=KS.get(op)
        if r!=r or r>0.031 or K0 is None: continue
        side=None; lots=[]; pnl=0.0; lastp=None; bought=0; below={}; nst=0; wrong=0
        for u in range(FIRST,900):
            a=K.get(op+u)
            if side and a:
                o="n" if side=="y" else "y"; q=a[side][1]+a[o][1]
                if q>0: lastp=(a[side][2]+(a[o][1]-a[o][2]))/q
                if lots and a[side][1]>0 and a[side][2]/a[side][1]>=TP:
                    for e,n in lots: pnl+=n*TP-mfee(TP,n)-(n*e+fee(e,n))
                    lots=[]
            if side and lots and lastp is not None:
                d=1 if side=="y" else -1; lead=d*(path[60+u]-K0)/K0
                hit=[]
                for L in lots:
                    pdown=lastp<=L[0]*(1-X)
                    below[id(L)]=below.get(id(L),0)+1 if pdown else 0
                    if mode=="price": c=pdown
                    elif mode=="and_btc": c=pdown and lead<LEAD
                    elif mode=="confirm": c=below[id(L)]>=CONF
                    elif mode=="or_btc": c=pdown or lead<LEAD
                    elif mode=="btc_only": c=lead<LEAD
                    elif mode=="late_and": c=pdown and (lead<LEAD or u>=780)
                    if c: hit.append(L)
                if hit:
                    b=sell_fast(K,op+u+1,side)
                    if b is not None:
                        win=y if side=="y" else not y
                        for L in hit: e,n=L; pnl+=n*b-fee(b,n)-(n*e+fee(e,n)); lots.remove(L); nst+=1; wrong+=win
            held=sum(n for _,n in lots)
            if u%STEP==0 and u<=LAST and held<MAXHELD:
                th=START+(FLOOR-START)*min((u-FIRST)/(END-FIRST),1.0); m=(path[60+u]-K0)/K0
                s2="y" if m>=th else ("n" if -m>=th else None)
                if s2 and (side is None or s2==side or not lots):
                    e=buy_px(K,op+u+1,s2)
                    if e is not None and e<CAP:
                        if side and s2!=side: lastp=None
                        n=int(min((200 if u<360 else 100)*SZF,MAXHELD-held))
                        if n>0: side=s2; lots.append((e,n)); bought+=n
        if not side: continue
        win=y if side=="y" else not y
        for e,n in lots: pnl+=(n if win else 0)-(n*e+fee(e,n))
        out.append(dict(part=part(op),n=bought,pnl=pnl,stops=nst,wrong=wrong))
    return pd.DataFrame(out)
CF=[("price -30% (current)",dict()),
 ("price -30% AND BTC lead < +0.03%",dict(mode="and_btc",LEAD=0.0003)),("price -30% AND BTC lead < +0.02%",dict(mode="and_btc",LEAD=0.0002)),
 ("price -30% AND BTC lead < +0.01%",dict(mode="and_btc",LEAD=0.0001)),("price -30% AND BTC at strike",dict(mode="and_btc",LEAD=0.0)),
 ("price -30% held 2 s",dict(mode="confirm",CONF=2)),("price -30% held 5 s",dict(mode="confirm",CONF=5)),
 ("price -30% OR BTC lead < -0.02%",dict(mode="or_btc",LEAD=-0.0002)),
 ("BTC only: lead < 0 (no price stop)",dict(mode="btc_only",LEAD=0.0)),
 ("price -30% AND (BTC < +0.02% or last 2 min)",dict(mode="late_and",LEAD=0.0002))]
if len(sys.argv)>3 and sys.argv[3]=="step":
    BASE=dict(mode="and_btc",LEAD=0.0001)
    CF=[("every 10 s, 200/100 (current)",dict(BASE)),("every 5 s, 200/100 (2x pace)",dict(BASE,STEP=5)),("every 5 s, 100/50 (same pace)",dict(BASE,STEP=5,SZF=0.5)),
        ("every 2 s, 40/20 (same pace)",dict(BASE,STEP=2,SZF=0.2)),("every 20 s, 400/200 (same pace)",dict(BASE,STEP=20,SZF=2.0)),("every 20 s, 200/100 (half pace)",dict(BASE,STEP=20))]
elif len(sys.argv)>3 and sys.argv[3]=="first":
    BASE=dict(mode="and_btc",LEAD=0.0001)
    CF=[(f"first check at min {f/60:g}",dict(BASE,FIRST=f)) for f in (30,60,90,120,180,240)]
elif len(sys.argv)>3 and sys.argv[3]=="mny":
    BASE=dict(mode="and_btc",LEAD=0.0001)
    CF=[(f"{a*100:.3f}% -> {b*100:.2f}% by min 15",dict(BASE,START=a,FLOOR=b)) for a,b in
        ((0.0010,0.0005),(0.00125,0.0005),(0.0015,0.0005),(0.00175,0.0005),(0.0020,0.0005),(0.0015,0.0003),(0.0015,0.0004),(0.0015,0.0006),(0.0015,0.0007),(0.00175,0.0007))]
elif len(sys.argv)>3:
    BASE=dict(mode="and_btc",LEAD=0.0001)
    CF=[("0.15% -> 0.05% at min 15 (current)",dict(BASE)),("0.15% -> 0.05% at min 12",dict(BASE,END=720)),("0.15% -> 0.04% at min 12",dict(BASE,END=720,FLOOR=0.0004)),
        ("0.15% -> 0.06% at min 12",dict(BASE,END=720,FLOOR=0.0006)),("0.15% -> 0.07% at min 12",dict(BASE,END=720,FLOOR=0.0007)),("0.15% -> 0.05% at min 10, flat after",dict(BASE,END=600))]
days=len(W)/96; pdd={p:sum(1 for w in W if part(w[1])==p)/96 for p in ("train","val","test")}
print(f"{days:.1f} days\n{'stop':46s} | {'stops/d':>7s} {'wrong%':>6s} | {'train $/d':>9s} {'val $/d':>8s} {'test $/d':>9s} | {'all $/day':>9s}")
for nm,kw in CF:
    d=run(**kw); dd={p:d[d.part==p].pnl.sum()/(pdd[p] or 1) for p in pdd}
    print(f"{nm:46s} | {d.stops.sum()/days:7.0f} {d.wrong.sum()/max(d.stops.sum(),1)*100:5.0f}% | {dd['train']:+9,.0f} {dd['val']:+8,.0f} {dd['test']:+9,.0f} | {d.pnl.sum()/days:+9,.0f}")
