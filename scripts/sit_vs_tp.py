# Sell at 95c vs sit to settlement, same entries and stops (run from data/: python3 -P ../scripts/sit_vs_tp.py 67 composite)
src=open("../scripts/her_full.py").read(); exec(src[:src.index("rows=[]")])
def sim3(E,sl,cross,tp):
    out=[]
    for y,op,path,K,ref,t0,side,e in E:
        win=y if side=="y" else not y; cost=e*N+fee(e,N); res=None; kind="settle"; last=e
        for u in range(t0+1,900):
            a=K.get(op+u)
            if a:
                o="n" if side=="y" else "y"; q=a[side][1]+a[o][1]
                if tp and a[side][1]>0 and a[side][2]/a[side][1]>=0.95: res=0.95*N-cost-0.0175*N*0.95*0.05; kind="tp"; break
                if q>0: last=(a[side][2]+(a[o][1]-a[o][2]))/q
            if (sl is not None and last<=e-sl) or (cross and (path[60+u]-ref)*(1 if side=="y" else -1)<=0):
                b=sell_px(K,op+u+1,side)
                if b is not None: res=b*N-cost-fee(b,N); kind="stop"; break
        if res is None: res=(N if win else 0)-cost
        out.append(dict(part=part(op),pnl=res/N,kind=kind,win=win))
    return pd.DataFrame(out)
print("P&L per contract, taker entry, 95c sell charged the maker fee. Columns: train | val | test | all")
print(f"{'entry rule':38s} {'stop':17s} {'exit':10s} | {'win%':>4s} | {'train':>14s} {'val':>14s} {'test':>14s} {'all 66d':>14s}")
for lo,hi,mny,vol in ((0.86,0.92,0.0010,0.006),(0.88,0.93,0.0010,0.006),(0.76,0.85,0.0010,0.006),(0.76,0.92,0.0010,0.006),(0.86,0.92,0.0015,0.006),(0.86,0.92,0.0015,None),(0.65,0.75,0.0010,0.006)):
    E=entries(mny,lo,hi); Ek=[e for e in E if vol is None or (R[e[1]]==R[e[1]] and R[e[1]]<=vol)]
    for nm,sl,cross in (("drop 20c",0.20,False),("BTC back at start",None,True),("none",None,False)):
        for tp in (True,False):
            d=sim3(Ek,sl,cross,tp)
            c=lambda g:f"{g.pnl.mean()*100:+5.2f}c±{1.96*g.pnl.std()/math.sqrt(len(g))*100:4.2f}"
            lab=f"{int(lo*100)}-{int(hi*100)}c, {mny*100:.2f}%, {'any vol' if vol is None else f'2h<= {vol*100:.1f}%'} ({len(d)/66.7:.0f}/d)"
            print(f"{lab:38s} {nm:17s} {'sell 95c' if tp else 'sit':10s} | {(d.pnl>0).mean()*100:4.0f} | "+" ".join(f"{c(d[d.part==p]):>14s}" for p in ("train","val","test"))+f" {c(d):>14s}")
    print()
