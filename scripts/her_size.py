# Does entry sizing matter? Working model (fast stop, roll, 30% per-entry stop, < 98.5c, sell 99.5c, <= 5,000 held) with different
# size schedules per entry. (run from data/ or data_recent/: python3 -P ../scripts/her_size.py 67 composite)
import sys,math
src=open("../scripts/her_sim.py").read(); exec(src[:src.index("def run(")])
def run(size_fn,CAP=0.985,X=0.30,TP=0.995,MAXHELD=5000,START=0.0015,LAST=880):
    out=[]
    for y,op,path,K in W:
        r=R[op]; K0=KS.get(op)
        if r!=r or r>0.031 or K0 is None: continue
        side=None; lots=[]; k=0; pnl=0.0; lastp=None; bought=0
        for u in range(120,900):
            a=K.get(op+u)
            if side and a:
                o="n" if side=="y" else "y"; q=a[side][1]+a[o][1]
                if q>0: lastp=(a[side][2]+(a[o][1]-a[o][2]))/q
                if lots and a[side][1]>0 and a[side][2]/a[side][1]>=TP:
                    for e,n in lots: pnl+=n*TP-mfee(TP,n)-(n*e+fee(e,n))
                    lots=[]
            if side and lots and lastp is not None:
                hit=[L for L in lots if lastp<=L[0]*(1-X)]
                if hit:
                    b=sell_fast(K,op+u+1,side)
                    if b is not None:
                        for L in hit: e,n=L; pnl+=n*b-fee(b,n)-(n*e+fee(e,n)); lots.remove(L)
            held=sum(n for _,n in lots)
            if u%10==0 and u<=LAST and held<MAXHELD:
                th=START+(0.0005-START)*(u-120)/780; m=(path[60+u]-K0)/K0
                s2="y" if m>=th else ("n" if -m>=th else None)
                if s2 and (side is None or s2==side or not lots):
                    e=buy_px(K,op+u+1,s2)
                    if e is not None and e<CAP:
                        if side and s2!=side: lastp=None; k=0
                        n=int(min(size_fn(k,e,u),MAXHELD-held))
                        if n>0: side=s2; k+=1; lots.append((e,n)); bought+=n
        if not side: continue
        win=y if side=="y" else not y
        for e,n in lots: pnl+=(n if win else 0)-(n*e+fee(e,n))
        out.append(dict(part=part(op),n=bought,pnl=pnl))
    return pd.DataFrame(out)
SCHED=[("flat 100 (her)",lambda k,e,u:100),
 ("front-loaded: 300,200,..100 (first 10 bigger)",lambda k,e,u:300 if k<3 else (200 if k<10 else 100)),
 ("front-loaded strong: 500 x3, then 100",lambda k,e,u:500 if k<3 else 100),
 ("back-loaded: 50 first 10, then 150",lambda k,e,u:50 if k<10 else 150),
 ("by price: more when cheap (100 x (1-p)/0.10)",lambda k,e,u:max(25,min(400,100*(1-e)/0.10))),
 ("by price: more when expensive",lambda k,e,u:max(25,min(400,100*0.10/max(1-e,0.02)))),
 ("by time: 200 before min 6, 100 after",lambda k,e,u:200 if u<360 else 100)]
KW={}
if len(sys.argv)>3:
    f100=lambda k,e,u:100; f200=lambda k,e,u:200 if u<360 else 100
    SCHED=[("flat 100 (her)",f100),("flat 100, no entries after min 11",f100),("flat 100, no entries after min 12",f100),("flat 100, no entries after min 13",f100),
           ("200 before min 6 then 100",f200),("200 before min 6 then 100, none after min 12",f200)]
    KW={"flat 100, no entries after min 11":dict(LAST=660),"flat 100, no entries after min 12":dict(LAST=720),"flat 100, no entries after min 13":dict(LAST=780),
        "200 before min 6 then 100, none after min 12":dict(LAST=720)}
days=len(W)/96; pdd={p:sum(1 for w in W if part(w[1])==p)/96 for p in ("train","val","test")}
print(f"{days:.1f} days\n{'sizing':48s} | {'avg bought':>10s} | {'$/ct':>6s} | {'train $/d':>9s} {'val $/d':>8s} {'test $/d':>9s} | {'all $/day':>9s} | {'$/day per 1,000 ct/mkt':>22s}")
for nm,fn in SCHED:
    d=run(fn,**KW.get(nm,{})); dd={p:d[d.part==p].pnl.sum()/(pdd[p] or 1) for p in pdd}
    print(f"{nm:48s} | {d.n.mean():10.0f} | {d.pnl.sum()/d.n.sum()*100:+5.2f}c | {dd['train']:+9,.0f} {dd['val']:+8,.0f} {dd['test']:+9,.0f} | {d.pnl.sum()/days:+9,.0f} | {d.pnl.sum()/days/(d.n.mean()/1000):+22,.0f}")
