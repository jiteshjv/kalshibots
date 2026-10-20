# Friend-style strategy, conservative simulator (run from data/). Kalshi price is the reference; no fair-value model.
# Fills from real taker prints: our buy of side S pays what S-takers paid; our sell of S gets 1 - (opposite-side taker price).
import json,math,glob,zipfile,io,csv,os,sys,datetime as dt,statistics as st
P={}
DAYS=int(sys.argv[1]) if len(sys.argv)>1 else 8
cut=(dt.date(2026,10,15)-dt.timedelta(days=DAYS)).isoformat()
for f in sorted(glob.glob("bn/*.zip")):
    if f[-14:-4]<cut: continue
    z=zipfile.ZipFile(f)
    for r in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
        t=int(r[0]); t=t//1000000 if t>1e14 else t//1000; P[t]=float(r[4])
for t,c in json.load(open("bn/today.json")): P[t//1000]=float(c)
def px(t):
    for k in range(10):
        if t-k in P: return P[t-k]
def ts(s): return int(dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
def fee(p,n): return math.ceil(0.07*n*p*(1-p)*100-1e-9)/100          # per order, rounded up to the cent
meta=sorted(json.load(open("settled.json")),key=lambda x:x["close_time"])
W=[]
for x in meta:
    f=f"ktr/{x['ticker']}.json"
    if x["result"] not in("yes","no") or not os.path.exists(f) or x["close_time"][:10]<cut: continue
    op=ts(x["close_time"])-900; path=[px(t) for t in range(op-60,op+900)]
    if None in path: continue
    W.append((x["result"]=="yes",op,path,{int(k):v for k,v in json.load(open(f)).items()}))
days=(W[-1][1]-W[0][1])/86400+1/96
print(f"windows {len(W)} ({days:.1f} days)  | BTC proxy: Binance 1s (stand-in for BRTI)")
def buy_px(K,t,side):          # VWAP paid by takers buying `side` in [t,t+2]
    q=n=0
    for u in range(t,t+3):
        a=K.get(u)
        if a and a[side][1]>0: q+=a[side][1]; n+=a[side][2]
    return n/q if q else None
def sell_px(K,t,side):         # selling `side` = matching an opposite-side taker: get 1 - their price
    o="n" if side=="y" else "y"; q=n=0
    for u in range(t,t+3):
        a=K.get(u)
        if a and a[o][1]>0: q+=a[o][1]; n+=a[o][2]
    return 1-n/q if q else None
def run(mny,persist,lo,hi,exitrule,fill95,N=5,lat=1):
    out=[]
    for y,op,path,K in W:
        ref=sum(path[:60])/60; streak=0; prev=None
        for s in range(120,870):
            p=path[60+s]; m=(p-ref)/ref
            side="y" if m>=mny else ("n" if -m>=mny else None)
            streak=streak+1 if side and side==prev else (1 if side else 0); prev=side
            if not side or streak<persist+1: continue
            e=buy_px(K,op+s+lat,side)
            if e is None or not (lo<=e<=hi): continue
            win=y if side=="y" else not y; cost=e*N+fee(e,N); res=None
            for u in range(s+lat+3,900):
                a=K.get(op+u)
                if a and a[side][1]>0:                       # someone bought our side at u
                    paid=a[side][2]/a[side][1]; top=a[side][0] if fill95=="opt" else None
                    # opt: any print >=95c fills us; cons: VWAP must trade THROUGH 95c (>=96c)
                    if (fill95=="opt" and paid>=0.95) or (fill95=="cons" and paid>=0.96):
                        res=0.95*N-cost-fee(0.95,N); kind="tp"; break
                pn=path[60+u]
                if exitrule=="cross" and (((pn-ref) if side=="y" else (ref-pn))<0):
                    b=sell_px(K,op+u+lat,side)
                    if b is not None: res=b*N-cost-fee(b,N); kind="cross"; break
                if exitrule=="t30" and u>=870:
                    b=sell_px(K,op+u+lat,side)
                    if b is not None: res=b*N-cost-fee(b,N); kind="t30"; break
            if res is None: res=(N if win else 0)-cost; kind="settle"
            out.append((res/N,e,kind)); break
    return out
print("Rules: after min 2, BRTI-proxy >= mny past target for `persist` extra secs, buy leading side if fill in [lo,hi],")
print("1s latency, 5 contracts, per-order rounded fees, resting 95c sell (opt = any print >=95c, cons = must trade >=96c).\n")
print(f"{'mny':>5s} {'pers':>4s} {'band':>9s} {'exit':6s} {'95c':4s} | tr/day  win%  entry  avg win  avg loss  P&L/ct ±95%     tp%  | 1st/2nd half")
for mny in (0.0015,0.0020):
  for persist in (0,2,4):
    for lo,hi in ((0.76,0.85),(0.70,0.90)):
      for exitrule in ("hold","t30","cross"):
        for fill95 in ("opt","cons"):
          r=run(mny,persist,lo,hi,exitrule,fill95)
          if len(r)<20: continue
          v=[a for a,_,_ in r]; w=[a for a in v if a>0]; l=[a for a in v if a<=0]; h=len(v)//2
          se=1.96*st.pstdev(v)/math.sqrt(len(v))
          print(f"{mny*100:4.2f}% {persist:4d} {lo:.2f}-{hi:.2f} {exitrule:6s} {fill95:4s} | {len(v)/days:5.1f} {len(w)/len(v)*100:5.1f}%  {st.mean(e for _,e,_ in r):.3f}  {st.mean(w)*100 if w else 0:+6.1f}c  {st.mean(l)*100 if l else 0:+6.1f}c  {st.mean(v)*100:+5.2f}c ±{se*100:4.2f}  {sum(k=='tp' for *_,k in r)/len(r)*100:4.0f}% | {st.mean(v[:h])*100:+.2f}/{st.mean(v[h:])*100:+.2f}")
