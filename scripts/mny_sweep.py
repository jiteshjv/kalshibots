# Best moneyness to get in: entries only from minute 2 on (her 2-minute wait), first second BTC is >= mny past the start AND
# the leading side's ask is in the band; hold to settlement with a stop. (run from data/: python3 -P ../scripts/mny_sweep.py 67 composite)
src=open("../scripts/sit_vs_tp.py").read(); exec(src[:src.index('print("P&L per contract')])
MNY=[0.0005,0.00075,0.0010,0.00125,0.0015,0.00175,0.0020,0.0025,0.0030]
c=lambda g:f"{g.pnl.mean()*100:+5.2f}c±{1.96*g.pnl.std()/math.sqrt(len(g))*100:4.2f}" if len(g)>9 else "      --     "
for lo,hi in ((0.76,0.92),(0.86,0.92),(0.80,0.95)):
  E0={m:entries(m,lo,hi) for m in MNY}
  for vol in (None,0.006):
    for nm,sl,cross in (("drop 20c",0.20,False),("BTC back at start",None,True)):
      print(f"\n=== buy {int(lo*100)}-{int(hi*100)}c, {'any volatility' if vol is None else '2h range <= 0.6%'}, stop: {nm}, hold to settlement (after min 2) ===")
      print(f"{'moneyness':>9s} {'≈ $ at 82K':>10s} {'/day':>5s} {'avg buy':>7s} {'win%':>5s} {'stopped':>7s} | {'train':>14s} {'val':>14s} {'test':>14s} | {'all 66d':>14s}  {'$/trade@6300':>12s}")
      for m in MNY:
        Ek=[e for e in E0[m] if vol is None or (R[e[1]]==R[e[1]] and R[e[1]]<=vol)]
        if len(Ek)<30: continue
        d=sim3(Ek,sl,cross,False); ep=np.mean([e[7] for e in Ek])
        print(f"{m*100:8.3f}% {m*82000:9.0f}$ {len(d)/66.7:5.1f} {ep*100:6.1f}c {(d.pnl>0).mean()*100:5.0f} {(d.kind=='stop').mean()*100:6.0f}% | "
              +" ".join(f"{c(d[d.part==p]):>14s}" for p in ("train","val","test"))+f" | {c(d):>14s}  {d.pnl.mean()*6300:+11.0f}")
