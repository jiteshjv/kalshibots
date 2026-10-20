# Follow-up to her_full.py: her own numbers (85% x +$300, 15% x -$1,200, break-even without the stop) only add up if she buys ~88-92c
# and sells at 95c with a ~20c stop. Same grid on high-priced entries. (run from data/: python3 -P ../scripts/her_full90.py 67 composite)
src=open("../scripts/her_full.py").read(); exec(src[:src.index("rows=[]")])
STOPS=[("none",None,False),("drop 10c",0.10,False),("drop 15c",0.15,False),("drop 20c",0.20,False),("BTC back at start",None,True)]
rows=[]
for lo,hi in ((0.86,0.92),(0.88,0.93)):
    for mny in (0.0010,0.0015,0.0020,0.0025):
        E=entries(mny,lo,hi)
        for vol in (0.005,0.006,None):
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
_h=open("../scripts/her_full.py").read(); exec(_h[_h.index("def show"):_h.index("print(f\"{len(D)} configs over")])
print(f"{len(D)} configs, high-priced entries")
show(D.sort_values("score",ascending=False).head(20),"=== Top 20 by the worse of train/val ===")
H=D[(D.win.between(0.78,0.92))&(D.stopped.between(0.08,0.25))].copy()
H["dist"]=(H.win-0.85).abs()+(H.stopped-0.15).abs()+((H.stop_would_win-0.67).abs()/2)+((H.ratio-4).abs()/20)
show(H.sort_values("dist").head(15),"=== Closest to her fingerprint ===")
show(D[(D.vol==0.006)&(D.band=="86-92c")].sort_values(["mny","stop"]),"=== 86-92c, 2h range <= 0.6%: stop vs no stop ===")
