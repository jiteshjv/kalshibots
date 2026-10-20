# Limit-order entry for algo v1 (run from data/: python3 -P ../scripts/limit_v1.py 67 composite).
# Signal = v1 (after min 2, BTC >= 0.175% past start, leading ask 80-95c, 2h range <= 3.1%). Instead of taking the ask, rest a bid
# k cents below the signal-time price; it fills when the price trades back down to it (cons = needs a trade THROUGH the bid, i.e. back of
# the queue; touch = a trade AT the bid is enough). Cancel after T s or when BTC is back at the strike. Optional: if not filled at cancel,
# take the ask then (if still 80-95c and the signal still holds). Filled -> v1 stop (BTC back at strike, sell as taker) else hold.
# Maker fee on limit fills, taker fee otherwise.
src=open("../scripts/sit_vs_tp.py").read(); exec(src[:src.index('print("P&L per contract')])
def mfee(p,n): return math.ceil(0.0175*n*p*(1-p)*100-1e-9)/100
MNY=0.00175
def hold_from(y,op,path,K,ref,side,t_in,cost_ct):     # v1 stop / hold from second t_in; returns pnl per contract
    win=y if side=="y" else not y; d=1 if side=="y" else -1
    for u in range(t_in+1,900):
        if (path[60+u]-ref)*d<=0:
            b=sell_px(K,op+u+1,side)
            if b is not None: return b-cost_ct-fee(b,N)/N,"stop",win
    return (1 if win else 0)-cost_ct,"settle",win
def run(k,T,mode,take_after):
    out=[]
    for y,op,path,K in W:
        r=R[op]
        if r!=r or r>0.031: continue
        ref=sum(path[:60])/60; sig=None
        for s in range(120,870):
            m=(path[60+s]-ref)/ref
            side="y" if m>=MNY else ("n" if -m>=MNY else None)
            if not side: continue
            e=buy_px(K,op+s+1,side)
            if e is None or not (0.80<=e<=0.95): continue
            sig=(s+1,side,e); break
        if sig is None: continue
        t0,side,e0=sig; d=1 if side=="y" else -1; o="n" if side=="y" else "y"
        if k is None:                                   # plain v1: take the ask
            p,kind,win=hold_from(y,op,path,K,ref,side,t0+2,e0+fee(e0,N)/N); out.append(dict(part=part(op),pnl=p,kind=kind,win=win,how="taker",e=e0)); continue
        L=round(e0-k,2); filled=None
        for u in range(t0,min(t0+T,899)):
            if (path[60+u]-ref)*d<=0: break               # BTC back at strike -> cancel
            a=K.get(op+u)
            if not a: continue
            lo=a[side][0] if a[side][1]>0 else 9; ovw=a[o][2]/a[o][1] if a[o][1]>0 else -1
            hit=(lo<L-1e-9 or ovw>1-L+1e-9) if mode=="cons" else (lo<=L+1e-9 or ovw>=1-L-1e-9)
            if hit: filled=u; break
        if filled is not None:
            p,kind,win=hold_from(y,op,path,K,ref,side,filled,L+mfee(L,N)/N); out.append(dict(part=part(op),pnl=p,kind=kind,win=win,how="limit",e=L)); continue
        if take_after:
            u=min(t0+T,899)
            if (path[60+u]-ref)*d>=MNY*ref:
                e=buy_px(K,op+u+1,side)
                if e is not None and 0.80<=e<=0.95:
                    p,kind,win=hold_from(y,op,path,K,ref,side,u+3,e+fee(e,N)/N); out.append(dict(part=part(op),pnl=p,kind=kind,win=win,how="taker_late",e=e))
    return pd.DataFrame(out)
cc=lambda g:f"{g.pnl.mean()*100:+5.2f}c±{1.96*g.pnl.std()/math.sqrt(len(g))*100:4.2f}" if len(g)>9 else "     --"
base=run(None,0,None,False); nb=len(base)
print(f"v1 signals: {nb/66.7:.1f}/day. P&L per contract; fill% = share of v1 signals that end in a trade.\n")
print(f"{'entry':34s} {'fill%':>5s} {'/day':>5s} {'avg buy':>7s} {'win%':>5s} | {'train':>13s} {'val':>13s} {'test':>13s} | {'all 66d':>13s} | {'win% limit fills':>16s}")
def show(lab,d):
    lw=d[d.how=="limit"].win.mean()*100 if (d.how=="limit").any() else float('nan')
    print(f"{lab:34s} {len(d)/nb*100:5.0f} {len(d)/66.7:5.1f} {d.e.mean()*100:6.1f}c {d.win.mean()*100:5.1f} | "+" ".join(f"{cc(d[d.part==p]):>13s}" for p in ("train","val","test"))+f" | {cc(d):>13s} | {lw:15.1f}%")
show("take the ask (v1 as is)",base)
for mode in ("touch","cons"):
    for k in (0.00,0.01,0.02,0.03,0.05):
        for T in (30,120,600):
            for ta in (False,True):
                show(f"bid ask-{int(k*100)}c, {T}s, {mode}{', then take' if ta else ''}",run(k,T,mode,ta))
    print()
