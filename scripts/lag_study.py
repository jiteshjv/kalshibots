# Millisecond lag study, Oct 8-15 (run from data/: python3 -P ../scripts/lag_study.py). Inputs from ms_dl.py.
# 1) Event study: sharp BTC moves on Coinbase / Binance -> how fast do Kalshi traded prices follow, and who trades in the gap?
# 2) Latency sweep: her rule (BTC >= 0.10% past the official strike after min 2) with reaction delays 0 ms .. 2 s.
#    Entry price at delay L = price paid by the first taker buying our side at or after signal+L (an actual print = what the ask was).
import json,glob,math,io,zipfile,datetime as dt
import numpy as np,pandas as pd
def ts(s): return dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()
def fee(p): return math.ceil(0.07*5*p*(1-p)*100-1e-9)/100/5
# --- BTC trades
cb=np.array([r for f in sorted(glob.glob("cb_ms/[0-9]*.json")) for r in json.load(open(f))],dtype=object)
cbt=cb[:,0].astype(float); cbp=cb[:,1].astype(float); o=np.argsort(cbt,kind="stable"); cbt,cbp=cbt[o],cbp[o]
bt=[];bp=[]
for f in sorted(glob.glob("bn_agg/*.zip")):
    z=zipfile.ZipFile(f); d=pd.read_csv(z.open(z.namelist()[0]),header=None,usecols=[1,5])
    t=d[5].to_numpy(dtype=float); t=t/1e6 if t[0]>1e14 else t/1e3
    bt.append(t); bp.append(d[1].to_numpy(dtype=float))
bnt=np.concatenate(bt); bnp=np.concatenate(bp); o=np.argsort(bnt,kind="stable"); bnt,bnp=bnt[o],bnp[o]
print(f"Coinbase trades {len(cbt):,}  Binance trades {len(bnt):,}")
def last(T,P,t):
    i=np.searchsorted(T,t,side="right")-1; return P[np.clip(i,0,len(P)-1)]
# --- Kalshi windows
meta={x["ticker"]:x for x in json.load(open("settled.json"))}
WIN=[]
for f in sorted(glob.glob("ktr_ms/*.json")):
    tk=f.split("/")[-1][:-5]; x=meta[tk]
    if x["result"] not in ("yes","no") or x.get("floor_strike") is None: continue
    k=json.load(open(f))
    if not k: continue
    a=np.array(k,dtype=object); t=a[:,0].astype(float); side=a[:,1]; px=a[:,2].astype(float); q=a[:,3].astype(float)
    yes=np.where(side=="y",px,1-px)                                  # implied YES price of each print
    cl=ts(x["close_time"]); WIN.append(dict(tk=tk,op=cl-900,cl=cl,K0=float(x["floor_strike"]),y=x["result"]=="yes",t=t,side=side,px=px,q=q,yes=yes))
print(f"Kalshi windows {len(WIN)}  prints {sum(len(w['t']) for w in WIN):,}")
# ---------- 1) event study
BINS=[(-1.0,-0.5),(-0.5,-0.2),(-0.2,0),(0,0.05),(0.05,0.1),(0.1,0.2),(0.2,0.5),(0.5,1.0),(1.0,2.0),(2.0,5.0)]
def events(T,P,w,J,look=0.5):
    i0,i1=np.searchsorted(T,[w["op"]+120,w["cl"]-120]); ev=[]; lastev=-1e18
    for i in range(i0,i1):
        t=T[i]
        if t-lastev<5: continue
        j=np.searchsorted(T,t-look); ch=P[i]-P[j]
        if abs(ch)>=J: ev.append((t,1 if ch>0 else -1)); lastev=t
    return ev
for src,T,P in (("Coinbase",cbt,cbp),("Binance",bnt,bnp)):
  for J in (20,40):
    rows=[]
    for w in WIN:
        for t0,d in events(T,P,w,J):
            base_m=(w["t"]>=t0-3)&(w["t"]<t0-1)
            if base_m.sum()==0: continue
            sp=lambda m:(w["yes"][m] if d>0 else 1-w["yes"][m])
            base=np.average(sp(base_m),weights=w["q"][base_m])
            r=[]
            for a,b in BINS:
                m=(w["t"]>=t0+a)&(w["t"]<t0+b)
                if m.sum()==0: r+= [np.nan,0.0,0.0]; continue
                chg=np.average(sp(m),weights=w["q"][m])-base
                dirvol=w["q"][m&(w["side"]==("y" if d>0 else "n"))].sum(); vol=w["q"][m].sum()
                r+=[chg,dirvol,vol]
            rows.append(r)
    R=np.array(rows)
    print(f"\n1) {src} moves >= ${J} within 0.5s ({len(R)} events, min 2-13 of windows). Kalshi price of the side BTC moved toward, vs 1-3s before:")
    print(f"   {'time after move':>16s} {'price change':>12s} {'% of 5s move':>12s} {'share of volume buying that side':>34s} {'prints/event':>12s}")
    full=np.nanmean(R[:,3*(len(BINS)-1)])
    for k,(a,b) in enumerate(BINS):
        c=np.nanmean(R[:,3*k]); dv=R[:,3*k+1].sum(); v=R[:,3*k+2].sum()
        print(f"   {int(a*1000):>6d} to {int(b*1000):>5d}ms {c*100:+11.2f}c {c/full*100 if full else 0:11.0f}% {dv/v*100 if v else 0:33.0f}% {np.mean(~np.isnan(R[:,3*k]))*100:10.0f}%")
# ---------- 2) latency sweep of her rule
print("\n2) Her rule: first time after min 2 (until min 13) that BTC is >= mny past the official strike -> buy leading side.")
print("   Entry = price of the first taker print on that side at or after signal + delay (within 3s). Hold to settlement, taker fee.")
LAT=[0,0.05,0.1,0.25,0.5,1.0,2.0]
for src,T,P in (("Coinbase",cbt,cbp),("Binance",bnt,bnp)):
  for mny in (0.0010,0.0015):
    sig=[]
    for w in WIN:
        i0,i1=np.searchsorted(T,[w["op"]+120,w["op"]+780])
        if i1<=i0: continue
        adj=np.median(cbp[np.searchsorted(cbt,w["op"]-600):np.searchsorted(cbt,w["op"])])/np.median(P[np.searchsorted(T,w["op"]-600):np.searchsorted(T,w["op"])]) if src=="Binance" else 1.0
        m=(P[i0:i1]*adj-w["K0"])/w["K0"]; hit=np.nonzero(np.abs(m)>=mny)[0]
        if len(hit)==0: continue
        t0=T[i0+hit[0]]; side="y" if m[hit[0]]>0 else "n"; sig.append((w,t0,side))
    print(f"\n   signal from {src}, >= {mny*100:.2f}% past strike: {len(sig)} signals in {len(WIN)} windows")
    print(f"   {'delay':>7s} {'filled':>6s} {'avg entry':>9s} {'win%':>5s} {'P&L/ct all':>14s} | {'65-75c band: n':>14s} {'entry':>5s} {'win%':>5s} {'P&L/ct':>14s}")
    for L in LAT:
        out=[]
        for w,t0,side in sig:
            m=(w["t"]>=t0+L)&(w["t"]<t0+L+3)&(w["side"]==side)
            if not m.any(): continue
            e=w["px"][m][0]
            if e>=0.99: continue
            win=w["y"] if side=="y" else not w["y"]
            out.append((e,win,(1 if win else 0)-e-fee(e)))
        o=np.array(out,dtype=float); b=o[(o[:,0]>=0.65)&(o[:,0]<=0.75)]
        f=lambda v:f"{v.mean()*100:+6.2f}c±{1.96*v.std()/math.sqrt(len(v))*100:5.2f}" if len(v)>5 else "     --"
        print(f"   {int(L*1000):>5d}ms {len(o):6d} {o[:,0].mean()*100:8.1f}c {o[:,1].mean()*100:5.1f} {f(o[:,2]):>14s} | {len(b):14d} {b[:,0].mean()*100 if len(b) else 0:4.0f}c {b[:,1].mean()*100 if len(b) else 0:5.1f} {f(b[:,2]):>14s}")
