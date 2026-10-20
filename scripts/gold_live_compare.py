# Compare free live gold quotes (data_goldlive/quotes.csv) against Kalshi KXGOLD15M strikes (= Pyth price at the open)
# and results (Pyth at the close vs strike). Error = source price in the last 10 s before the open - strike, after removing
# each source's median offset. python3 -P scripts/gold_live_compare.py
import json,urllib.request,ssl,datetime as dt,numpy as np,pandas as pd
try:
    import certifi; CTX=ssl.create_default_context(cafile=certifi.where())
except Exception: CTX=ssl.create_default_context()
Q=pd.read_csv("data_goldlive/quotes.csv",names=["t","src","p"]); Q=Q[~Q.src.str.endswith("err")]
t0,t1=Q.t.min(),Q.t.max()
ms=[]; cur=None
for st in ("settled","open","closed"):
    u=f"https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXGOLD15M&status={st}&limit=200"
    with urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"kalshibot"}),context=CTX,timeout=15) as r: ms+=json.load(r)["markets"]
ts=lambda s:dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()
M=pd.DataFrame([dict(tk=m["ticker"],o=ts(m["open_time"]),c=ts(m["close_time"]),k=float(m["floor_strike"]),res=m.get("result","")) for m in ms if m.get("floor_strike")]).drop_duplicates("tk")
M=M[(M.o>t0+15)&(M.o<t1)].sort_values("o")
def px(s,a,b):
    x=Q[(Q.src==s)&(Q.t>=a)&(Q.t<b)]; return x.p.iloc[-1] if len(x) else np.nan
print(f"{len(M)} markets opened during collection ({(t1-t0)/3600:.1f} h). Gold ~${M.k.mean():.0f}: 0.01% = ${M.k.mean()*1e-4:.2f}, 0.044% = ${M.k.mean()*4.4e-4:.2f}")
for s in sorted(Q.src.unique()):
    d=np.array([px(s,o-10,o)-k for o,k in zip(M.o,M.k)]); ok=np.isfinite(d)
    if ok.sum()<2: continue
    off=np.median(d[ok]); e=np.abs(d[ok]-off)
    R=M[M.res.isin(["yes","no"])]; hit=[]
    for o,c,k,res in zip(R.o,R.c,R.k,R.res):
        p=px(s,c-10,c)
        if p==p and abs(p-off-k)>0: hit.append(((p-off)>k)==(res=="yes"))
    upd=Q[Q.src==s].p.diff().ne(0).mean()
    print(f"{s:11s} offset ${off:+6.2f}  error median ${np.median(e):.2f} p90 ${np.quantile(e,.9):.2f} max ${e.max():.2f}  "
          f"result match {np.mean(hit)*100 if hit else float('nan'):.0f}% ({len(hit)})  price changes on {upd*100:.0f}% of polls")
