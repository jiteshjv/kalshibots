# Millisecond stop-out test, Oct 8-15 (run from data/: python3 -P ../scripts/stop_ms.py). Inputs from ms_dl.py.
# Entry = her rule: Coinbase >= 0.10% past the official strike after min 2 (until min 13), buy the leading side,
#   entry price = first taker print on that side >= 100 ms after the signal, kept only if 65-75c.
# While held, a stop fires on a BTC trade (Coinbase or Binance) when:
#   lead<X   : BTC is less than X% past the strike in our direction
#   trail D  : BTC is $D below its best level since entry (in our direction)
# Exit = sell our side = buy the other side as taker: proceeds 1 - (first other-side print >= trigger + delay).
# Optional 95c take-profit: first other-side print <= 5c. Otherwise hold to settlement. Taker fees both ways.
import json,glob,math,zipfile,datetime as dt
import numpy as np,pandas as pd
def ts(s): return dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()
def fee(p): return math.ceil(0.07*5*p*(1-p)*100-1e-9)/100/5
cb=[r[:2] for f in sorted(glob.glob("cb_ms/[0-9]*.json")) for r in json.load(open(f))]
cb=np.array(cb,dtype=float); cb=cb[np.argsort(cb[:,0],kind="stable")]; cbt,cbp=cb[:,0],cb[:,1]
bt=[];bp=[]
for f in sorted(glob.glob("bn_agg/*.zip")):
    z=zipfile.ZipFile(f); d=pd.read_csv(z.open(z.namelist()[0]),header=None,usecols=[1,5])
    t=d[5].to_numpy(dtype=float); t=t/1e6 if t[0]>1e14 else t/1e3
    bt.append(t); bp.append(d[1].to_numpy(dtype=float))
bnt=np.concatenate(bt); bnp=np.concatenate(bp); o=np.argsort(bnt,kind="stable"); bnt,bnp=bnt[o],bnp[o]
meta={x["ticker"]:x for x in json.load(open("settled.json"))}
ENTRY_L=0.10; MNY=0.0010
LAT=[0,0.025,0.05,0.1,0.25,0.5,1.0,2.0]
RULES=[("hold",None,None)]+[("lead<",x,None) for x in (0.0006,0.0004,0.0002,0.0,-0.0002)]+[("trail",None,d) for d in (60,100,150)]
trades=[]
for f in sorted(glob.glob("ktr_ms/*.json")):
    tk=f.split("/")[-1][:-5]; x=meta.get(tk)
    if not x or x["result"] not in ("yes","no") or x.get("floor_strike") is None: continue
    k=json.load(open(f))
    if not k: continue
    a=np.array(k,dtype=object); kt=a[:,0].astype(float); ks=a[:,1]; kp=a[:,2].astype(float)
    cl=ts(x["close_time"]); op=cl-900; K0=float(x["floor_strike"]); yes=x["result"]=="yes"
    i0,i1=np.searchsorted(cbt,[op+120,op+780])
    if i1<=i0: continue
    m=(cbp[i0:i1]-K0)/K0; hit=np.nonzero(np.abs(m)>=MNY)[0]
    if not len(hit): continue
    t0=cbt[i0+hit[0]]; d=1 if m[hit[0]]>0 else -1; side="y" if d>0 else "n"; oth="n" if d>0 else "y"
    em=(kt>=t0+ENTRY_L)&(kt<t0+ENTRY_L+3)&(ks==side)
    if not em.any(): continue
    j=np.argmax(em); e=kp[j]; te=kt[j]
    if not (0.65<=e<=0.75): continue
    win=yes if d>0 else not yes
    ot=kt[ks==oth]; opx=kp[ks==oth]                      # other-side prints: our exit prices are 1 - these
    def exit_at(t,ot=ot,opx=opx,cl=cl):
        i=np.searchsorted(ot,t)
        return (ot[i],1-opx[i]) if i<len(ot) and ot[i]<cl else None
    tp=None; mt=(ot>te)&(opx<=0.05)
    if mt.any(): i=np.argmax(mt); tp=(ot[i],1-opx[i])
    trig={}
    for src,T,P in (("cb",cbt,cbp),("bn",bnt,bnp)):
        adj=1.0
        if src=="bn":
            a0,a1=np.searchsorted(cbt,[op-600,op]); b0,b1=np.searchsorted(bnt,[op-600,op])
            if a1<=a0 or b1<=b0: continue
            adj=np.median(cbp[a0:a1])/np.median(bnp[b0:b1])
        s0,s1=np.searchsorted(T,[te,cl]); PP=P[s0:s1]*adj; TT=T[s0:s1]
        if not len(PP): continue
        lead=d*(PP-K0)/K0; best=np.maximum.accumulate(d*PP)
        for r,X,D in RULES[1:]:
            c=lead<X if r=="lead<" else (best-d*PP)>=D
            trig[(src,r,X,D)]=TT[np.argmax(c)] if c.any() else None
    trades.append(dict(tk=tk,op=op,e=e,win=win,tp=tp,trig=trig,exit_at=exit_at))
print(f"{len(trades)} entries in 65-75c (her rule, Coinbase signal, 100 ms entry delay), Oct 8-15")
half=np.median([t["op"] for t in trades])
def pnl(t,key,L,use_tp):
    e=t["e"]; cost=e+fee(e); hold=(1.0 if t["win"] else 0.0)-cost
    st=None
    if key is not None and t["trig"].get(key) is not None: st=t["exit_at"](t["trig"][key]+L)
    tp=t["tp"] if use_tp else None
    ev=[z for z in (st,tp) if z is not None]
    if not ev: return hold,0
    tx,px=min(ev); return px-fee(1-px)-cost,(1 if (st is not None and tx==st[0]) else 0)
for use_tp in (False,True):
    print(f"\n==== {'with 95c take-profit' if use_tp else 'no take-profit'} ====")
    print(f"{'stop rule':16s} {'src':3s} {'delay':>6s} | {'stopped':>7s} {'win%':>5s} {'P&L/ct all':>15s} | {'1st half':>8s} {'2nd half':>8s}")
    for r,X,D in RULES:
        for src in (("cb","bn") if r!="hold" else ("--",)):
            for L in (LAT if r!="hold" else [0]):
                key=None if r=="hold" else (src,r,X,D)
                v=[(pnl(t,key,L,use_tp),t["op"]<half) for t in trades]
                p=np.array([a[0][0] for a in v]); s=np.array([a[0][1] for a in v]); h=np.array([a[1] for a in v])
                lab=r if r=="hold" else (f"lead<{X*100:+.2f}%" if r=="lead<" else f"trail ${D}")
                print(f"{lab:16s} {src:3s} {int(L*1000):5d}ms | {s.mean()*100:6.0f}% {(p>0).mean()*100:5.1f} {p.mean()*100:+7.2f}c±{1.96*p.std()/math.sqrt(len(p))*100:5.2f} | "
                      f"{p[h].mean()*100:+7.2f}c {p[~h].mean()*100:+7.2f}c")
