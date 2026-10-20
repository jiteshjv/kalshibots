# What moves the Kalshi KXBTC15M price: BTC (via fair value) or order flow (net taker volume)?
# Per market (data_recent: ktr per-second tape + composite 1 s BTC), 10-second steps from minute 1 to 14:
#   dK   = change in Kalshi YES price (last trade VWAP of the step, YES terms)
#   dF   = change in fair YES = Phi((btc-K)/(K*sigma*sqrt(t_left))), sigma = realized 1 s vol over the prior 30 min
#   flow = net taker flow in the step (YES taker buys - NO taker buys, contracts)
# R^2 of dK on dF alone, flow alone, both; and how much of a step's move reverses in the next 30 s (price pressure fades, BTC doesn't).
import json,math,numpy as np,pandas as pd,datetime as dt
from zoneinfo import ZoneInfo
PT=ZoneInfo("America/Los_Angeles")
Z=np.load("data_recent/prices_1s.npz"); T0=int(Z["T0"]); A=np.load("data_recent/composite_1s.npy")
M=pd.read_csv("results/v9_eqrec_recent_markets.csv")
M["ts"]=pd.to_datetime(M.day+" 2026 "+M.window_pt,format="%a %b %d %Y %I:%M %p").dt.tz_localize(PT).astype("int64")//10**9
Phi=lambda z:0.5*(1+math.erf(z/math.sqrt(2)))
R=[]
for m in M.itertuples():
    try: K={int(k):v for k,v in json.load(open(f"data_recent/ktr/{m.ticker}.json")).items()}
    except FileNotFoundError: continue
    o=m.ts; pre=A[o-1800-T0:o-T0]
    if len(pre)<1800 or np.isnan(pre).mean()>0.2: continue
    r=np.diff(np.log(pre)); sig=np.nanstd(r)
    def kal(a,b):                        # YES-terms VWAP of trades in [a,b)
        v=c=0.0
        for u in range(a,b):
            x=K.get(u)
            if not x: continue
            if x["y"][1]>0: v+=x["y"][1]; c+=x["y"][2]
            if x["n"][1]>0: v+=x["n"][1]; c+=x["n"][1]-x["n"][2]
        return c/v if v>0 else np.nan
    def flow(a,b): return sum((K[u]["y"][1]-K[u]["n"][1]) for u in range(a,b) if u in K)
    def fair(t):
        p=A[t-T0]; left=o+900-t
        return Phi((p-m.strike)/(m.strike*sig*math.sqrt(left))) if p==p and left>0 else np.nan
    steps=list(range(o+60,o+840,10))
    for a in steps:
        k0,k1,k2=kal(a-5,a),kal(a+5,a+10),kal(a+35,a+40)
        R.append(dict(tk=m.ticker,dK=k1-k0,dF=fair(a+9)-fair(a-1),flow=flow(a,a+10),rev=k2-k1,lvl=k1))
D=pd.DataFrame(R).dropna(); D=D[(D.lvl>0.03)&(D.lvl<0.97)]
def r2(X,y):
    X=np.column_stack([np.ones(len(y))]+X); b=np.linalg.lstsq(X,y,rcond=None)[0]; e=y-X@b; return 1-e.var()/y.var(),b
y=D.dK.values; fF=D.dF.values; fl=D.flow.values/1e4
a,_=r2([fF],y); b,_=r2([fl],y); c,bb=r2([fF,fl],y)
# residual flow: flow not explained by BTC
_,bf=r2([fF],fl); rf=fl-(bf[0]+bf[1]*fF); d,bd=r2([fF,rf],y)
big=np.abs(D.dK)>=0.05
out=[f"{D.tk.nunique()} markets (Oct 2026 data_recent), {len(D):,} ten-second steps with the price between 3c and 97c",
f"R^2 of the Kalshi price change explained by:",
f"  BTC (fair-value change) alone      {a*100:5.1f}%",
f"  net taker flow alone               {b*100:5.1f}%",
f"  both                               {c*100:5.1f}%",
f"  BTC + flow NOT explained by BTC    {d*100:5.1f}%   (extra from flow alone: {(d-a)*100:.1f} points)",
f"Big moves (>=5c in 10 s): {big.sum():,} steps. BTC alone explains {r2([fF[big]],y[big])[0]*100:.0f}% of them;"
f" BTC fair value moved the same way {np.mean(np.sign(fF[big])==np.sign(y[big]))*100:.0f}% of the time.",
f"Next 30 s after a big move: average reversal {np.mean(-np.sign(y[big])*D.rev.values[big])*100:+.2f}c (positive = gives back part of the move)",
f"  big moves where BTC barely moved (|fair change| < 1c): {(big&(np.abs(fF)<0.01)).sum():,}, reversal {np.mean((-np.sign(y)*D.rev.values)[big&(np.abs(fF)<0.01)])*100:+.2f}c"]
open("results/price_drivers.txt","w").write("\n".join(out)); print("\n".join(out))
