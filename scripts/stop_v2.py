# Which stop for v1? Same entries for every stop (v1, taker, official floor_strike as the strike). (run from data/: python3 -P ../scripts/stop_v2.py 67 composite)
# Stops (checked every second, sell as taker 1 s later at the real opposite-side prints):
#   BTC-based: lead (BTC past strike in our direction) < X; "confirm" = must hold 2 s; "avg" = in the last 60 s use the projected settlement
#   average (locked-in part + current price for the rest) instead of the live price.
#   Kalshi-based: our side's last traded price <= P.   Combos: both conditions.
src=open("../scripts/her_full.py").read(); exec(src[:src.index("def sim2")])
KS={ts(x["close_time"])-900:float(x["floor_strike"]) for x in meta if x.get("floor_strike")}
E=[]
for y,op,path,K in W:
    r=R[op]; K0=KS.get(op)
    if r!=r or r>0.031 or K0 is None: continue
    for s in range(120,870):
        m=(path[60+s]-K0)/K0; side="y" if m>=0.00175 else ("n" if -m>=0.00175 else None)
        if not side: continue
        e=buy_px(K,op+s+1,side)
        if e is None or not (0.80<=e<=0.95): continue
        E.append((y,op,path,K,K0,s+3,side,e)); break
def sim(lead_x=None,confirm=1,avg=False,px_stop=None,both=False):
    out=[]
    for y,op,path,K,K0,t0,side,e in E:
        win=y if side=="y" else not y; d=1 if side=="y" else -1; cost=e+fee(e,N)/N; res=None; last=e; cnt=0; kind="settle"
        for u in range(t0+1,900):
            a=K.get(op+u)
            if a:
                o="n" if side=="y" else "y"; q=a[side][1]+a[o][1]
                if q>0: last=(a[side][2]+(a[o][1]-a[o][2]))/q
            b_hit=False
            if lead_x is not None:
                spot=path[60+u]
                if avg and u>=840:
                    k=u-840+1; A=sum(path[60+840:60+u+1])/k; spot=(k*A+(60-k)*spot)/60
                lead=d*(spot-K0)/K0; cnt=cnt+1 if lead<lead_x else 0; b_hit=cnt>=confirm
            p_hit=px_stop is not None and last<=px_stop
            hit=(b_hit and p_hit) if both else (b_hit or p_hit)
            if hit:
                b=sell_px(K,op+u+1,side)
                if b is not None: res=b-cost-fee(b,N)/N; kind="stop"; break
        if res is None: res=(1 if win else 0)-cost
        out.append(dict(part=part(op),pnl=res,kind=kind,win=win))
    return pd.DataFrame(out)
cc=lambda g:f"{g.pnl.mean()*100:+5.2f}c±{1.96*g.pnl.std()/math.sqrt(len(g))*100:4.2f}"
TESTS=[("no stop",{}),
 ("BTC back at strike (current v1)",dict(lead_x=0.0)),
 ("  + confirm 2 s",dict(lead_x=0.0,confirm=2)),
 ("  + last min: projected avg",dict(lead_x=0.0,avg=True)),
 ("  + confirm 2 s + projected avg",dict(lead_x=0.0,confirm=2,avg=True)),
 ("TIGHTER: lead < +0.05%",dict(lead_x=0.0005)),
 ("TIGHTER: lead < +0.10%",dict(lead_x=0.0010)),
 ("TIGHTER: lead < +0.05%, proj avg",dict(lead_x=0.0005,avg=True)),
 ("LOOSER: lead < -0.02%",dict(lead_x=-0.0002)),
 ("LOOSER: lead < -0.05%",dict(lead_x=-0.0005)),
 ("Kalshi price <= 80c",dict(px_stop=0.80)),("Kalshi price <= 70c",dict(px_stop=0.70)),("Kalshi price <= 60c",dict(px_stop=0.60)),
 ("Kalshi price <= 50c",dict(px_stop=0.50)),("Kalshi price <= 40c",dict(px_stop=0.40)),
 ("BTC at strike AND price <= 70c",dict(lead_x=0.0,px_stop=0.70,both=True)),
 ("BTC at strike AND price <= 80c",dict(lead_x=0.0,px_stop=0.80,both=True)),
 ("BTC at strike OR price <= 60c",dict(lead_x=0.0,px_stop=0.60)),
 ("proj avg at strike AND price <= 80c",dict(lead_x=0.0,avg=True,px_stop=0.80,both=True))]
print(f"{len(E)} v1 entries ({len(E)/66.7:.1f}/day), taker, official strike. P&L per contract after fees; $/trade at 1,500 contracts.\n")
print(f"{'stop':38s} {'stop%':>5s} {'win%':>5s} | {'train':>13s} {'val':>13s} {'test':>13s} | {'all 66d':>13s} {'$/trade':>7s}")
for nm,kw in TESTS:
    d=sim(**kw)
    print(f"{nm:38s} {(d.kind=='stop').mean()*100:5.0f} {(d.pnl>0).mean()*100:5.0f} | "+" ".join(f"{cc(d[d.part==p]):>13s}" for p in ("train","val","test"))+f" | {cc(d):>13s} {d.pnl.mean()*1500:+7.0f}")
