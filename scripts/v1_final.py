# Final v1 trade list at 1,500 contracts (rules fixed from the 66-day backtests; run from data/ or data_recent/):
#   python3 -P ../scripts/v1_final.py 67 composite <days or 0=all> <out.csv>
# Entry: 2h range <= 3.1%; after min 2, BTC >= 0.175% past Kalshi's official strike; leading ask 80-95c -> bid 1c under it (fills only when
# trades go THROUGH the bid), cancel after 120 s or if BTC is back at the strike, then take the ask if still 80-95c and the signal holds.
# Stop: BTC back at the strike OR our side's last trade <= 50c -> sell as taker at the next real prints. Else hold to settlement.
# Fees: maker on limit fills, taker otherwise, per 1,500-contract order; none at settlement. Taker-only v1 shown for comparison.
import sys,math
from zoneinfo import ZoneInfo
src=open("../scripts/her_full.py").read(); exec(src[:src.index("def sim2")])
def mfee(p,n): return math.ceil(0.0175*n*p*(1-p)*100-1e-9)/100
DAYS=float(sys.argv[3]); OUTF=sys.argv[4]; SZ=1500; PT=ZoneInfo("America/Los_Angeles"); MNY=0.00175
KS={ts(x["close_time"])-900:float(x["floor_strike"]) for x in meta if x.get("floor_strike")}
last_op=max(op for _,op,_,_ in W); start=last_op+900-DAYS*86400 if DAYS>0 else 0
def run_exit(y,op,path,K,K0,side,t_in,cost):
    win=y if side=="y" else not y; d=1 if side=="y" else -1; o="n" if side=="y" else "y"; lastp=None
    for u in range(t_in+1,900):
        a=K.get(op+u)
        if a:
            q=a[side][1]+a[o][1]
            if q>0: lastp=(a[side][2]+(a[o][1]-a[o][2]))/q
        why="BTC at strike" if (path[60+u]-K0)*d<=0 else ("price <= 50c" if lastp is not None and lastp<=0.50 else None)
        if why:
            b=sell_px(K,op+u+1,side)
            if b is not None: return b*SZ-fee(b,SZ)-cost,"stop",why,u,b,path[60+u]
    return (SZ if win else 0)-cost,"settle",None,900,1.0 if win else 0.0,None
mm=lambda x:None if x is None else f"{int(x)//60}:{int(x)%60:02d}"
rows=[]
for y,op,path,K in W:
    if op<start: continue
    r=R[op]; K0=KS.get(op)
    if r!=r or r>0.031 or K0 is None: continue
    sig=None
    for s in range(120,870):
        m=(path[60+s]-K0)/K0; side="y" if m>=MNY else ("n" if -m>=MNY else None)
        if not side: continue
        e=buy_px(K,op+s+1,side)
        if e is None or not (0.80<=e<=0.95): continue
        sig=(s+1,side,e); break
    if sig is None: continue
    t0,side,e0=sig; d=1 if side=="y" else -1; o="n" if side=="y" else "y"
    tk=run_exit(y,op,path,K,K0,side,t0+2,e0*SZ+fee(e0,SZ))
    L=round(e0-0.01,2); filled=None
    for u in range(t0,min(t0+120,899)):
        if (path[60+u]-K0)*d<=0: break
        a=K.get(op+u)
        if not a: continue
        lo=a[side][0] if a[side][1]>0 else 9; ovw=a[o][2]/a[o][1] if a[o][1]>0 else -1
        if lo<L-1e-9 or ovw>1-L+1e-9: filled=u; break
    how=None; px=None; t_in=None; ex=None
    if filled is not None: how="1c bid filled"; px=L; t_in=filled; ex=run_exit(y,op,path,K,K0,side,filled,L*SZ+mfee(L,SZ))
    else:
        u=min(t0+120,899)
        if (path[60+u]-K0)*d>=MNY*K0:
            e=buy_px(K,op+u+1,side)
            if e is not None and 0.80<=e<=0.95: how="took ask at 2m"; px=e; t_in=u+1; ex=run_exit(y,op,path,K,K0,side,u+3,e*SZ+fee(e,SZ))
    t=pd.Timestamp(op,unit="s",tz="UTC").tz_convert(PT)
    rows.append(dict(day=t.strftime("%a %b %d"),window_pt=t.strftime("%-I:%M %p"),side="YES (up)" if side=="y" else "NO (down)",
        strike=K0,btc_at_signal=round(path[60+t0-1],2),signal_time=mm(t0-1),signal_px=round(e0,3),entry=how or "skipped",entry_time=mm(t_in),
        buy=None if px is None else round(px,3),exit=ex[1] if ex else None,stop_reason=ex[2] if ex else None,exit_time=mm(ex[3]) if ex else None,
        exit_px=round(ex[4],3) if ex else None,btc_at_exit=round(ex[5],2) if ex and ex[5] else None,result="YES" if y else "NO",
        pnl_usd=round(ex[0],2) if ex else 0.0,taker_exit=tk[1],taker_exit_px=round(tk[4],3),pnl_usd_taker_v1=round(tk[0],2)))
D=pd.DataFrame(rows); D.to_csv(OUTF,index=False)
tr=D[D.entry!="skipped"]
print(f"\n{len(D)} signals, {len(tr)} trades | 1c-bid P&L ${D.pnl_usd.sum():+,.0f}  taker P&L ${D.pnl_usd_taker_v1.sum():+,.0f}")
for day,x in D.groupby("day",sort=False):
    print(f"  {day}: {len(x):3d} signals {(x.entry!='skipped').sum():3d} trades {(x.exit=='stop').sum():2d} stops | 1c bid ${x.pnl_usd.sum():+8,.0f} | taker ${x.pnl_usd_taker_v1.sum():+8,.0f}")
