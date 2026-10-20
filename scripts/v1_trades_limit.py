# v1 trades in the last N days at 1,500 contracts, entry by a bid 1c below the signal-time ask (back of the queue: fills only when the tape
# trades THROUGH the bid), cancel after 120 s or when BTC is back at the strike, then take the ask if still 80-95c and the signal holds.
# Same stop/hold as v1. Maker fee on limit fills. (run from data/: python3 -P ../scripts/v1_trades_limit.py 67 composite [days])
import sys
from zoneinfo import ZoneInfo
src=open("../scripts/limit_v1.py").read(); exec(src[:src.index("cc=lambda")])
DAYS=float(sys.argv[3]) if len(sys.argv)>3 else 10; SZ=1500; PT=ZoneInfo("America/Los_Angeles"); K1=0.01; T=120
last=max(op for _,op,_,_ in W); start=last+900-DAYS*86400
XT={}
def exit_usd(y,op,path,K,ref,side,t_in,cost):
    win=y if side=="y" else not y; d=1 if side=="y" else -1
    for u in range(t_in+1,900):
        if (path[60+u]-ref)*d<=0:
            b=sell_px(K,op+u+1,side)
            if b is not None: XT.update(t=u,px=b,btc=path[60+u]); return b*SZ-fee(b,SZ)-cost,"stop",win
    XT.update(t=900,px=1.0 if win else 0.0,btc=None); return (SZ if win else 0)-cost,"settle",win
rows=[]
for y,op,path,K in W:
    if op<start: continue
    r=R[op]
    if r!=r or r>0.031: continue
    ref=sum(path[:60])/60; sig=None
    for s in range(120,870):
        m=(path[60+s]-ref)/ref; side="y" if m>=MNY else ("n" if -m>=MNY else None)
        if not side: continue
        e=buy_px(K,op+s+1,side)
        if e is None or not (0.80<=e<=0.95): continue
        sig=(s+1,side,e); break
    if sig is None: continue
    t0,side,e0=sig; d=1 if side=="y" else -1; o="n" if side=="y" else "y"
    tk_usd,tk_how,_=exit_usd(y,op,path,K,ref,side,t0+2,e0*SZ+fee(e0,SZ)); TK=dict(XT)      # plain v1 for comparison
    L=round(e0-K1,2); filled=None
    for u in range(t0,min(t0+T,899)):
        if (path[60+u]-ref)*d<=0: break
        a=K.get(op+u)
        if not a: continue
        lo=a[side][0] if a[side][1]>0 else 9; ovw=a[o][2]/a[o][1] if a[o][1]>0 else -1
        if lo<L-1e-9 or ovw>1-L+1e-9: filled=u; break
    how_in=None
    XT.clear(); t_in=None
    if filled is not None: px=L; how_in="limit"; t_in=filled; usd,how,win=exit_usd(y,op,path,K,ref,side,filled,L*SZ+mfee(L,SZ))
    else:
        u=min(t0+T,899)
        if (path[60+u]-ref)*d>=MNY*ref:
            e=buy_px(K,op+u+1,side)
            if e is not None and 0.80<=e<=0.95: px=e; how_in="taker after 2m"; t_in=u+1; usd,how,win=exit_usd(y,op,path,K,ref,side,u+3,e*SZ+fee(e,SZ))
    t=pd.Timestamp(op,unit="s",tz="UTC").tz_convert(PT)
    mm=lambda x:None if x is None else f"{int(x)//60}:{int(x)%60:02d}"
    rows.append(dict(day=t.strftime("%a %b %d"),window_pt=t.strftime("%-I:%M %p"),side="YES (up)" if side=="y" else "NO (down)",
                     strike=round(ref,2),btc_at_signal=round(path[60+t0-1],2),signal_time=mm(t0-1),signal_px=round(e0,3),
                     entry=how_in or "skipped",entry_time=mm(t_in),buy=None if how_in is None else round(px,3),
                     stop_trigger_btc=round(ref,2),exit=None if how_in is None else how,exit_time=mm(XT.get("t")) if how_in else None,
                     exit_px=round(XT["px"],3) if how_in else None,btc_at_exit=round(XT["btc"],2) if how_in and XT.get("btc") else None,
                     result="YES" if y else "NO",pnl_usd=0.0 if how_in is None else round(usd,2),
                     taker_buy=round(e0,3),taker_exit=tk_how,taker_exit_px=round(TK["px"],3),pnl_usd_taker_v1=round(tk_usd,2)))
D=pd.DataFrame(rows); D.to_csv(f"../results/v1_limit_trades_last{int(DAYS)}d.csv",index=False)
tr=D[D.entry!="skipped"]
print(f"v1 with a bid 1c below, {pd.Timestamp(start,unit='s',tz='UTC').tz_convert(PT):%b %d %-I:%M %p} to {pd.Timestamp(last+900,unit='s',tz='UTC').tz_convert(PT):%b %d %-I:%M %p} PT, 1,500 contracts\n")
print(f"{'day':11s} {'signals':>7s} {'limit fills':>11s} {'took after 2m':>13s} {'skipped':>7s} {'stops':>5s} | {'P&L 1c bid':>10s} {'running':>9s} | {'P&L taker v1':>12s} {'running':>9s}")
r1=r2=0
for day,x in D.groupby("day",sort=False):
    r1+=x.pnl_usd.sum(); r2+=x.pnl_usd_taker_v1.sum()
    print(f"{day:11s} {len(x):7d} {(x.entry=='limit').sum():11d} {(x.entry=='taker after 2m').sum():13d} {(x.entry=='skipped').sum():7d} {(x.exit=='stop').sum():5d} | "
          f"{x.pnl_usd.sum():+10,.0f} {r1:+9,.0f} | {x.pnl_usd_taker_v1.sum():+12,.0f} {r2:+9,.0f}")
print(f"\nTOTAL: {len(tr)} trades of {len(D)} signals ({(D.entry=='limit').mean()*100:.0f}% filled by the 1c-lower bid), avg buy {tr.buy.mean()*100:.1f}c vs {tr.signal_px.mean()*100:.1f}c signal price")
print(f"  1c-lower bid: ${D.pnl_usd.sum():+,.0f} (${tr.pnl_usd.mean():+.0f}/trade, ${D.pnl_usd.sum()/DAYS:+,.0f}/day)   taker v1: ${D.pnl_usd_taker_v1.sum():+,.0f} (${D.pnl_usd_taker_v1.mean():+.0f}/trade)")
