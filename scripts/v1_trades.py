# Every v1 trade in the last N days of data, at 1,500 contracts (run from data/: python3 -P ../scripts/v1_trades.py 67 composite [days]).
# v1: 2h range <= 3.1%; after min 2, BTC >= 0.175% past start; buy leading side as taker if 80-95c; stop when BTC back at start; else hold.
# Prices are the real Kalshi trade prices (per-contract VWAP of the prints); fees computed for one 1,500-contract order each way.
import sys
from zoneinfo import ZoneInfo
src=open("../scripts/sit_vs_tp.py").read(); exec(src[:src.index('print("P&L per contract')])
DAYS=float(sys.argv[3]) if len(sys.argv)>3 else 10; SZ=1500; PT=ZoneInfo("America/Los_Angeles")
last=max(op for _,op,_,_ in W); start=last+900-DAYS*86400
E=[e for e in entries(0.00175,0.80,0.95) if e[1]>=start and R[e[1]]==R[e[1]] and R[e[1]]<=0.031]
rows=[]
for y,op,path,K,ref,t0,side,e in E:
    win=y if side=="y" else not y; d=1 if side=="y" else -1; cost=e*SZ+fee(e,SZ); res=None; how="settle"; ex=None
    for u in range(t0+1,900):
        if (path[60+u]-ref)*d<=0:
            b=sell_px(K,op+u+1,side)
            if b is not None: res=b*SZ-fee(b,SZ)-cost; how="stop"; ex=b; xs=u; break
    if res is None: res=(SZ if win else 0)-cost; ex=1.0 if win else 0.0; xs=900
    t=pd.Timestamp(op,unit="s",tz="UTC").tz_convert(PT)
    rows.append(dict(day=t.strftime("%a %b %d"),window_pt=t.strftime("%-I:%M %p"),side="YES (up)" if side=="y" else "NO (down)",
                     entry_min=round((t0-2)/60,1),buy=round(e,3),exit=how,exit_px=round(ex,3),exit_min=round(xs/60,1),
                     outcome_won=bool(win),pnl_usd=round(res,2)))
D=pd.DataFrame(rows); D.to_csv(f"../results/v1_trades_last{int(DAYS)}d.csv",index=False)
print(f"v1 trades, {pd.Timestamp(start,unit='s',tz='UTC').tz_convert(PT):%b %d %-I:%M %p} to {pd.Timestamp(last+900,unit='s',tz='UTC').tz_convert(PT):%b %d %-I:%M %p} PT, 1,500 contracts\n")
g=D.groupby("day",sort=False)
print(f"{'day':11s} {'trades':>6s} {'wins':>5s} {'stops':>5s} {'losses held':>11s} {'P&L $':>9s} {'running $':>10s}")
run=0
for day,x in g:
    run+=x.pnl_usd.sum()
    print(f"{day:11s} {len(x):6d} {(x.pnl_usd>0).sum():5d} {(x.exit=='stop').sum():5d} {((x.exit=='settle')&(~x.outcome_won)).sum():11d} {x.pnl_usd.sum():+9,.0f} {run:+10,.0f}")
print(f"\nTOTAL {len(D)} trades ({len(D)/DAYS:.1f}/day): {(D.pnl_usd>0).mean()*100:.0f}% profitable, {(D.exit=='stop').mean()*100:.0f}% stopped, "
      f"P&L ${D.pnl_usd.sum():+,.0f} (${D.pnl_usd.mean():+.0f}/trade, ${D.pnl_usd.sum()/DAYS:+,.0f}/day)")
print(f"avg win ${D[D.pnl_usd>0].pnl_usd.mean():+,.0f}  avg stop ${D[D.exit=='stop'].pnl_usd.mean():+,.0f}  "
      f"held to 0: {((D.exit=='settle')&(~D.outcome_won)).sum()} trades avg ${D[(D.exit=='settle')&(~D.outcome_won)].pnl_usd.mean():+,.0f}")
print(f"best ${D.pnl_usd.max():+,.0f}  worst ${D.pnl_usd.min():+,.0f}")
