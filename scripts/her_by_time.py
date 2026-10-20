# P&L of the working model (fast stop + roll) by PT hour, weekday and window, to look for news / scheduled-event effects. (run from data/)
src=open("../scripts/her_sim.py").read(); exec(src[:src.index("days=len(W)/96")])
import numpy as np
d=run(FAST=True,ROLL=True); days=len(W)/96
t=pd.to_datetime(d.op,unit="s",utc=True).dt.tz_convert(PT); d["hour"]=t.dt.hour; d["dow"]=t.dt.strftime("%a"); d["hm"]=t.dt.strftime("%H:%M"); d["wkend"]=t.dt.dayofweek>=5
def tab(g,lab):
    x=d.groupby(g).agg(mk=("pnl","size"),pnl=("pnl","sum"),avg=("pnl","mean"),sd=("pnl","std"),stops=("stops","sum"),n=("n","sum"))
    x["ci"]=1.96*x.sd/np.sqrt(x.mk)
    print(f"\n=== by {lab} ===\n{'':6s} {'markets':>7s} {'$ total':>9s} {'$/market':>9s} {'±':>6s} {'% lots stopped':>14s}")
    for k,r in x.iterrows(): print(f"{str(k):6s} {r.mk:7.0f} {r.pnl:+9,.0f} {r.avg:+9.1f} {r.ci:6.1f} {r.stops*100/(r.n/100):13.0f}%")
tab("hour","PT hour (window start)"); tab("dow","weekday")
wk=d[~d.wkend]
print("\n=== weekday scheduled times (window that contains the release) ===")
for lab,hms in (("5:30 AM PT (8:30 ET data: CPI, jobs, PPI)",["05:30"]),("6:30 AM PT (US stock open)",["06:30"]),("7:00 AM PT (10:00 ET data)",["07:00"]),
                ("11:00 AM PT (2 PM ET, Fed days)",["11:00"]),("1:00 PM PT (US stock close)",["13:00","12:45"])):
    x=wk[wk.hm.isin(hms)]; o=wk[~wk.hm.isin(hms)]
    print(f"  {lab:44s} {len(x):4d} mkts  ${x.pnl.mean():+7.1f}/mkt (±{1.96*x.pnl.std()/np.sqrt(len(x)):.0f})  vs other weekday windows ${o.pnl.mean():+6.1f}")
print("\n=== 15 worst markets ===")
for r in d.nsmallest(15,"pnl").itertuples(): print(f"  {pd.Timestamp(r.op,unit='s',tz='UTC').tz_convert(PT):%a %b %d %-I:%M %p} PT  {r.n:5d} ct  {r.stops:2d} lots stopped  {r.rolls} rolls  ${r.pnl:+,.0f}")
d.to_csv("../results/her_markets_66d.csv",index=False)
