# Summary of the paper trader's results (python3 -P scripts/paper_report.py [logs-dir]).
# trades_v2.csv (from 2026-10-19, algo v1 + old variants, with stops and order-book depth); trades.csv = earlier rows (old variants only).
import sys,os,math
import pandas as pd
OUT=sys.argv[1] if len(sys.argv)>1 else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),"logs","paper")
rd=lambda f:pd.read_csv(os.path.join(OUT,f)) if os.path.exists(os.path.join(OUT,f)) else pd.DataFrame()
W=rd("windows.csv"); T2=rd("trades_v2.csv"); T1=rd("trades.csv")
if W.empty: print("no settled windows yet"); sys.exit()
days=len(W)/96
print(f"{len(W)} settled windows (~{days:.1f} days)")
ok=W.dropna(subset=["our_settle"])
if len(ok):
    err=(ok.our_settle-ok.settle_value.astype(float)).abs()
    print(f"BTC proxy vs official settlement: outcome match {ok.our_outcome_match.mean()*100:.1f}%  avg error ${err.mean():.2f}")
if len(T1):                                     # earlier rows -> same columns as v2
    T1=T1.assign(outcome=T1.tp_hit.map({1:"tp",0:"settle"}),pnl_per_ct=T1.pnl_tp_per_ct,usd_1500=T1.usd_tp_1500,usd_6300=float("nan"))
T=pd.concat([x for x in (T1,T2) if len(x)],ignore_index=True)
if T.empty: print("no paper trades yet"); sys.exit()
print(f"\n{'variant':14s} {'trades':>6s} {'/day':>5s} {'win%':>5s} {'stop%':>5s} {'tp%':>4s} {'avg buy':>7s} {'P&L/ct':>13s} {'$/trade 1500':>12s} {'$/day 1500':>10s} {'$/trade 6300':>12s} {'total $1500':>11s}")
for v,g in T.groupby("variant"):
    n=len(g); p=g.pnl_per_ct; u=g.usd_1500.dropna(); b=g.usd_6300.dropna()
    print(f"{v:14s} {n:6d} {n/days:5.1f} {g.win.mean()*100:5.1f} {(g.outcome=='stop').mean()*100:5.0f} {(g.outcome=='tp').mean()*100:4.0f} {g.best_ask.mean()*100:6.1f}c "
          f"{p.mean()*100:+6.2f}c±{1.96*p.std()/math.sqrt(n)*100 if n>1 else 0:4.1f} {u.mean() if len(u) else float('nan'):+12.0f} {u.mean()*n/days if len(u) else float('nan'):+10.0f} "
          f"{b.mean() if len(b) else float('nan'):+12.0f} {u.sum():+11.0f}")
s=T2[(T2.variant=="v1")] if len(T2) else T2
if len(s):
    st=s[s.outcome=="stop"]
    print(f"\nv1 order book: bids within 20c of entry cover 1,500 in {(s.bid_depth_20c>=1500).mean()*100:.0f}% of entries, 6,300 in {(s.bid_depth_20c>=6300).mean()*100:.0f}%")
    if len(st): print(f"v1 stops ({len(st)}): best bid {st.stop_bid.mean()*100:.1f}c, avg fill 1,500 {st.stop_avg_1500.mean()*100:.1f}c, 6,300 {st.stop_avg_6300.mean()*100:.1f}c "
                      f"(book could not take 6,300 in {st.stop_avg_6300.isna().mean()*100:.0f}% of stops)")
print("\nAim: ~300 v1 trades (about 2 weeks). Edge = P&L/ct clearly above 0 after the ± range.")
