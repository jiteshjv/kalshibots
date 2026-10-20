# Algo v1 selection: flat vs volatility-tiered moneyness, chosen on train+val only; test reported once.
# Entry after min 2, once per window, first second the rule passes and the leading side's ask is in [lo,95c]; skip 2h range > 3.1%.
# Stop: BTC back at the start (sell as taker 1 s later). Exit: hold to settlement. (run from data/: python3 -P ../scripts/algo_v1.py 67 composite)
src=open("../scripts/sit_vs_tp.py").read(); exec(src[:src.index('print("P&L per contract')])
def entries_t(th,lo=0.80,hi=0.95,maxr=0.031,lat=1):
    E=[]
    for y,op,path,K in W:
        r=R[op]
        if r!=r or r>maxr: continue
        mny=th(r); ref=sum(path[:60])/60
        for s in range(120,870):
            m=(path[60+s]-ref)/ref
            side="y" if m>=mny else ("n" if -m>=mny else None)
            if not side: continue
            e=buy_px(K,op+s+lat,side)
            if e is None or not (lo<=e<=hi): continue
            E.append((y,op,path,K,ref,s+lat+2,side,e)); break
    return E
def tier(a,b,c,cut1=0.006,cut2=0.012): return lambda r:a if r<=cut1 else (b if r<=cut2 else c)
RULES={"flat 0.150%":lambda r:0.0015,"flat 0.175%":lambda r:0.00175,"flat 0.200%":lambda r:0.0020}
for a in (0.0010,0.00125,0.0015):
    for b in (0.0015,0.00175,0.0020):
        for c in (0.0020,0.0025,0.0030):
            if a<=b<=c: RULES[f"tier {a*100:.3f}/{b*100:.3f}/{c*100:.3f}%"]=tier(a,b,c)
cc=lambda g:f"{g.pnl.mean()*100:+5.2f}c±{1.96*g.pnl.std()/math.sqrt(len(g))*100:4.2f}"
out=[]
for lo in (0.80,0.70):
    for nm,th in RULES.items():
        d=sim3(entries_t(th,lo),None,True,False)
        tv=d[d.part!="test"]; out.append((tv.pnl.mean(),lo,nm,d))
print("Ranked by train+val P&L only (test not used to choose). Tier = moneyness for 2h range <=0.6% / 0.6-1.2% / 1.2-3.1%.")
print(f"{'rule':30s} {'buy':6s} {'/day':>5s} {'win%':>5s} | {'train':>14s} {'val':>14s} | {'train+val':>14s} | {'TEST':>14s}")
for sc,lo,nm,d in sorted(out,key=lambda z:-z[0])[:15]:
    print(f"{nm:30s} {int(lo*100)}-95c {len(d)/66.7:5.1f} {(d.pnl>0).mean()*100:5.0f} | {cc(d[d.part=='train']):>14s} {cc(d[d.part=='val']):>14s} | {cc(d[d.part!='test']):>14s} | {cc(d[d.part=='test']):>14s}")
