# Her full rule set on 66 days (run from data/: python3 -P ../scripts/her_full.py 67 composite).
# Entry : >= mny past the start (after min 2), 2h high-low range before the window <= vol, buy the leading side as taker in a band.
# Exit  : 95c resting sell (opt fill) + a stop when price goes against her (sell as taker 1s later), else settle.
# Her fingerprint: ~85% winners, ~15% stopped out, of those ~2/3 would have won if held (10% of all) and ~1/3 lost it all (5%);
#   avg win : avg loss ~ 300 : 1200 = 1 : 4.
# Chosen on train/val (worse of the two), test shown. The order book (can she get out?) isn't in the history; see paper trader.
import math,numpy as np,pandas as pd
src=open("../scripts/exit_test.py").read(); exec(src[:src.index("print(f\"windows")])
def rng(op):
    i=op-T0; seg=ARR[max(0,i-7200):i]
    return (np.nanmax(seg)-np.nanmin(seg))/np.nanmean(seg) if np.sum(seg==seg)>3600 else np.nan
R={op:rng(op) for _,op,_,_ in W}
def sim2(E,sl,cross):
    out=[]
    for y,op,path,K,ref,t0,side,e in E:
        win=y if side=="y" else not y; cost=e*N+fee(e,N); res=None; kind="settle"; last=e
        for u in range(t0+1,900):
            a=K.get(op+u)
            if a:
                o="n" if side=="y" else "y"; q=a[side][1]+a[o][1]
                if a[side][1]>0 and a[side][2]/a[side][1]>=0.95: res=0.95*N-cost-fee(0.95,N); kind="tp"; break
                if q>0: last=(a[side][2]+(a[o][1]-a[o][2]))/q
            if (sl is not None and last<=e-sl) or (cross and (path[60+u]-ref)*(1 if side=="y" else -1)<=0):
                b=sell_px(K,op+u+1,side)
                if b is not None: res=b*N-cost-fee(b,N); kind="stop"; break
        if res is None: res=(N if win else 0)-cost
        out.append(dict(part=part(op),pnl=res/N,kind=kind,win=win))
    return pd.DataFrame(out)
STOPS=[("none",None,False),("drop 15c",0.15,False),("drop 25c",0.25,False),("drop 35c",0.35,False),("BTC back at start",None,True)]
rows=[]
for lo,hi in ((0.65,0.75),(0.76,0.85)):
    for mny in (0.0005,0.00075,0.0010,0.00125,0.0015,0.0020):
        E=entries(mny,lo,hi)
        for vol in (0.004,0.005,0.006,None):
            Ek=[e for e in E if vol is None or (R[e[1]]==R[e[1]] and R[e[1]]<=vol)]
            if len(Ek)<30: continue
            for nm,sl,cross in STOPS:
                d=sim2(Ek,sl,cross); r=dict(band=f"{int(lo*100)}-{int(hi*100)}c",mny=mny,vol=vol,stop=nm)
                for p in ("train","val","test"):
                    g=d[d.part==p]
                    r[p]=(len(g)/pdays[p],g.pnl.mean(),1.96*g.pnl.std()/math.sqrt(len(g)) if len(g)>1 else 0) if len(g) else (0,np.nan,0)
                st=d[d.kind=="stop"]; w=d[d.pnl>0]; l=d[d.pnl<=0]
                r.update(win=(d.pnl>0).mean(),stopped=(d.kind=="stop").mean(),stop_would_win=st.win.mean() if len(st) else np.nan,
                         ratio=(-l.pnl.mean()/w.pnl.mean()) if len(w) and len(l) else np.nan,avgw=w.pnl.mean(),avgl=l.pnl.mean())
                rows.append(r)
D=pd.DataFrame(rows); D["score"]=D.apply(lambda r:min(r.train[1],r.val[1]),axis=1)
f=lambda t:f"{t[0]:4.1f}/d {t[1]*100:+5.2f}c±{t[2]*100:4.2f}"
def show(G,title):
    print(f"\n{title}")
    print(f"{'band':7s} {'mny':>6s} {'vol<=':>5s} {'stop':17s} | {'win%':>4s} {'stop%':>5s} {'stopped->would win':>18s} {'loss:win':>8s} | {'train':20s} | {'val':20s} | test")
    for r in G.itertuples():
        print(f"{r.band:7s} {r.mny*100:5.3f}% {'any' if r.vol!=r.vol or r.vol is None else f'{r.vol*100:.1f}%':>5s} {r.stop:17s} | {r.win*100:4.0f} {r.stopped*100:5.0f} {r.stop_would_win*100 if r.stop_would_win==r.stop_would_win else 0:17.0f}% {r.ratio:7.1f}x | {f(r.train):20s} | {f(r.val):20s} | {f(r.test)}")
print(f"{len(D)} configs over {len(W)} windows ({len(W)/96:.0f} days); P&L per contract after taker fees, 1s latency")
show(D.sort_values("score",ascending=False).head(25),"=== Top 25 by the worse of train/val ===")
H=D[(D.win.between(0.80,0.90))&(D.stopped.between(0.08,0.25))].copy()
H["dist"]=(H.win-0.85).abs()+(H.stopped-0.15).abs()+((H.stop_would_win-0.67).abs()/2)+((H.ratio-4).abs()/20)
show(H.sort_values("dist").head(20),"=== Closest to her fingerprint (85% win, 15% stopped, 2/3 of stops would have won, loss 4x win) ===")
for band in ("65-75c","76-85c"):
    show(D[(D.band==band)&(D.vol.isin([0.006]))&(D.stop.isin(["none","drop 25c","BTC back at start"]))].sort_values(["stop","mny"]),f"=== {band}, 2h range <= 0.6%, by moneyness ===")
D.to_pickle("her_full.pkl")
