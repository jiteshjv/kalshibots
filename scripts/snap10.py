# Her requested measurement: snapshot every Kalshi 15m market every 10 s (from min 2). At each snapshot, if the 2h BTC range and the
# moneyness pass, buy the leading side as taker (real prints in the next 1-3 s) and hold to settlement. Every qualifying snapshot counts.
# Margin of error clusters by window (snapshots in one window share an outcome). (run from data/: python3 -P ../scripts/snap10.py 67 composite)
src=open("../scripts/her_full.py").read(); exec(src[:src.index("def sim2")])
rows=[]
for y,op,path,K in W:
    ref=sum(path[:60])/60; r2=R[op]
    if r2!=r2: continue
    for s in range(120,891,10):
        m=(path[60+s]-ref)/ref
        if abs(m)<0.0005: continue
        side="y" if m>0 else "n"; e=buy_px(K,op+s+1,side)
        if e is None or e>=0.99: continue
        win=y if side=="y" else not y
        rows.append((op,s,abs(m),r2,e,(1 if win else 0)-e-fee(e,N)/N,win))
S=pd.DataFrame(rows,columns=["op","s","m","r2","e","pnl","win"]); S["part"]=S.op.map(part)
S.to_pickle("snap10.pkl")
def stat(g):
    if len(g)<20: return "      --"
    w=g.groupby("op").pnl.agg(["sum","count"]); mu=w["sum"].sum()/w["count"].sum()
    se=math.sqrt(((w["sum"]-mu*w["count"])**2).sum())/w["count"].sum()
    return f"{mu*100:+5.2f}c±{1.96*se*100:4.2f}"
def line(g,lab):
    nw=g.op.nunique()
    print(f"{lab:34s} snaps {len(g):7d} windows {nw:5d} ({nw/66.7:4.1f}/d) avg buy {g.e.mean()*100:4.1f}c win {g.win.mean()*100:4.1f}% | "
          +" ".join(f"{p}: {stat(g[g.part==p]):>13s}" for p in ("train","val","test"))+f" | all {stat(g):>13s}")
print("Every 10 s snapshot from min 2, buy leading side at the real ask, hold to settlement, taker fee. P&L per contract.\n")
print("=== Her exact test: 2h range <= 0.6%, moneyness >= 0.15% ===")
line(S[(S.r2<=0.006)&(S.m>=0.0015)],"range<=0.6%, mny>=0.15%")
line(S[(S.r2<=0.006)&(S.m>=0.0015)&(S.e<=0.95)],"  ... and price <= 95c")
print("\n=== By 2h range bucket (moneyness >= 0.15%) ===")
for lo,hi in ((0,0.004),(0.004,0.006),(0.006,0.008),(0.008,0.010),(0.010,0.015),(0.015,0.031),(0.031,1)):
    line(S[(S.r2>lo)&(S.r2<=hi)&(S.m>=0.0015)],f"range {lo*100:.1f}-{hi*100:.1f}%")
line(S[(S.r2<=0.031)&(S.m>=0.0015)],"range <= 3.1% (her max)")
print("\n=== By moneyness, 2h range <= 0.6% ===")
for lo,hi in ((0.0005,0.001),(0.001,0.0015),(0.0015,0.002),(0.002,0.003),(0.003,1)):
    line(S[(S.r2<=0.006)&(S.m>=lo)&(S.m<hi)],f"mny {lo*100:.2f}-{min(hi,0.99)*100:.2f}%")
print("\n=== By moneyness, 2h range <= 3.1% ===")
for lo,hi in ((0.0005,0.001),(0.001,0.0015),(0.0015,0.002),(0.002,0.003),(0.003,1)):
    line(S[(S.r2<=0.031)&(S.m>=lo)&(S.m<hi)],f"mny {lo*100:.2f}-{min(hi,0.99)*100:.2f}%")
print("\n=== range <= 0.6%, mny >= 0.15%, by minute of the window ===")
for a,b in ((120,300),(300,480),(480,660),(660,780),(780,900)):
    line(S[(S.r2<=0.006)&(S.m>=0.0015)&(S.s>=a)&(S.s<b)],f"min {a//60}-{b//60}")
