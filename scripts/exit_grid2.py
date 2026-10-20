# Best stop and exit target on the best entries from mny_sweep (run from data/: python3 -P ../scripts/exit_grid2.py 67 composite)
src=open("../scripts/sit_vs_tp.py").read(); exec(src[:src.index('print("P&L per contract')])
def sim4(E,sl,cross,tp):
    out=[]
    for y,op,path,K,ref,t0,side,e in E:
        win=y if side=="y" else not y; cost=e*N+fee(e,N); res=None; kind="settle"; last=e
        for u in range(t0+1,900):
            a=K.get(op+u)
            if a:
                o="n" if side=="y" else "y"; q=a[side][1]+a[o][1]
                if tp and a[side][1]>0 and a[side][2]/a[side][1]>=tp: res=tp*N-cost-0.0175*N*tp*(1-tp); kind="tp"; break
                if q>0: last=(a[side][2]+(a[o][1]-a[o][2]))/q
            if (sl is not None and last<=e-sl) or (cross and (path[60+u]-ref)*(1 if side=="y" else -1)<=0):
                b=sell_px(K,op+u+1,side)
                if b is not None: res=b*N-cost-fee(b,N); kind="stop"; break
        if res is None: res=(N if win else 0)-cost
        out.append(dict(part=part(op),pnl=res/N,kind=kind,win=win))
    return pd.DataFrame(out)
c=lambda g:f"{g.pnl.mean()*100:+5.2f}c±{1.96*g.pnl.std()/math.sqrt(len(g))*100:4.2f}"
STOPS=[("none",None,False),("drop 10c",0.10,False),("drop 15c",0.15,False),("drop 20c",0.20,False),("drop 25c",0.25,False),("drop 30c",0.30,False),
       ("BTC back at start",None,True),("BTC back OR drop 20c",0.20,True)]
for lo,hi,mny,vol in ((0.80,0.95,0.00175,None),(0.86,0.92,0.0015,None),(0.80,0.95,0.00175,0.006)):
    E=entries(mny,lo,hi); Ek=[e for e in E if vol is None or (R[e[1]]==R[e[1]] and R[e[1]]<=vol)]
    print(f"\n=== entry: buy {int(lo*100)}-{int(hi*100)}c once BTC >= {mny*100:.3f}% past start (after min 2), {'any volatility' if vol is None else '2h range <= 0.6%'}: {len(Ek)/66.7:.1f}/day ===")
    print(f"{'stop':21s} {'exit':9s} | {'win%':>4s} {'stop%':>5s} | {'train':>14s} {'val':>14s} {'test':>14s} | {'all 66d':>14s} {'$/trade@6300':>12s}")
    rows=[]
    for nm,sl,cross in STOPS:
        for tp in (0.95,0.97,0.99,None):
            d=sim4(Ek,sl,cross,tp); P=[d[d.part==p].pnl.mean() for p in ("train","val","test")]
            rows.append((min(P[0],P[1]),nm,tp,d))
    for sc,nm,tp,d in sorted(rows,key=lambda z:-z[0]):
        print(f"{nm:21s} {'sit' if tp is None else f'sell {int(tp*100)}c':9s} | {(d.pnl>0).mean()*100:4.0f} {(d.kind=='stop').mean()*100:5.0f} | "
              +" ".join(f"{c(d[d.part==p]):>14s}" for p in ("train","val","test"))+f" | {c(d):>14s} {d.pnl.mean()*6300:+11.0f}")
