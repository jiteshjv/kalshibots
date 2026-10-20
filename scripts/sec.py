import json,math,glob,zipfile,io,csv,os,datetime as dt,statistics as st
from math import erf,sqrt
# ---- BTC 1s (Binance) ----
P={}
for f in sorted(glob.glob("bn/*.zip")):
    z=zipfile.ZipFile(f); 
    for r in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
        t=int(r[0]); t=t//1000000 if t>1e14 else t//1000   # us or ms
        P[t]=float(r[4])
for t,c in json.load(open("bn/today.json")): P[t//1000]=float(c)
def px(t):
    for k in range(10):
        if t-k in P: return P[t-k]
def ts(s): return int(dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
def Phi(z): return 0.5*(1+erf(z/sqrt(2)))
def fee(p): return 0.07*p*(1-p)
m={x["ticker"]:x for x in json.load(open("settled.json"))}
W=[]
for f in glob.glob("ktr/*.json"):
    tk=os.path.basename(f)[:-5]; x=m[tk]
    if x["result"] not in("yes","no"): continue
    cl=ts(x["close_time"]); op=cl-900
    path=[px(t) for t in range(op-360,cl)]
    if None in path: continue
    K={int(k):v for k,v in json.load(open(f)).items()}
    W.append((tk,x["result"]=="yes",op,cl,path,K))
W.sort(key=lambda w:w[2])
print("windows:",len(W))
# ---- proxy check: does Binance-based settlement match Kalshi result? ----
agree=0
for tk,y,op,cl,path,K in W:
    ref=sum(path[300:360])/60; fin=sum(path[-60:])/60
    agree+=((fin>=ref)==y)
print(f"Binance proxy reproduces Kalshi result: {agree/len(W)*100:.1f}%")
def fair(path,s):  # s = seconds since open (0..899); path index 360+s is price at op+s
    i=360+s; ref=sum(path[300:360])/60
    r=[math.log(path[j]/path[j-1]) for j in range(i-300,i)]
    sig=st.pstdev(r)*path[i] or 1e-9
    tau=900-s; now=path[i]
    if tau>60: mu=now; var=sig**2*(tau-60)+sig**2*60/3
    else:
        k=tau; done=path[i-(60-k)+1:i+1] if 60-k>0 else []
        A=sum(done)/len(done) if done else now
        mu=((60-k)*A+k*now)/60; var=(k/60)**2*sig**2*k/3
    return Phi((mu-ref)/sqrt(max(var,1e-9)))
def fills(K,s0,w):  # executable prices from actual taker trades in [s0, s0+w)
    out={}
    for side in "yn":
        q=n=0
        for s in range(s0,s0+w):
            a=K.get(s)
            if a and a[side][1]>0: q+=a[side][1]; n+=a[side][2]
        out[side]=(n/q,q) if q else (None,0)
    return out
print("\nStrategy: each second, fair P(Up) from Binance 1s + live vol (models 60s settlement avg).")
print("If fair - executable price - fee > threshold, buy (first signal per window, hold to settlement).")
print("Executable price = VWAP of real taker trades on that side in the 3s AFTER signal+latency.\n")
FIRST=True
res={}
for tk,y,op,cl,path,K in W:
    F=[fair(path,s) for s in range(0,898)]
    for lat in (0,1,2,5):
        for thr in (0.02,0.05,0.10):
            for tmin,tmax in ((60,600),(600,840),(840,897)):
                key=(lat,thr,tmin)
                for s in range(tmin,tmax):
                    f=F[s]; fl=fills(K,op+s+lat,3)
                    yp,yq=fl["y"]; npx,nq=fl["n"]
                    pick=None
                    if yp and f-yp-fee(yp)>thr: pick=(1.0*y-yp-fee(yp),yq,yp)
                    elif npx and (1-f)-npx-fee(npx)>thr: pick=(1.0*(not y)-npx-fee(npx),nq,npx)
                    if pick:
                        res.setdefault(key,[]).append(pick); break
days=(W[-1][2]-W[0][2])/86400+1/96
lab={60:"min 1-10",600:"min 10-14",840:"last 60s"}
print(" window    lat thr | trades win%  avg price  P&L/contract  $/mo @100/trade  med size avail")
for key in sorted(res):
    lat,thr,tmin=key; v=res[key]; pn=[a for a,_,_ in v]
    mean=sum(pn)/len(pn); se=st.pstdev(pn)/sqrt(len(pn))
    print(f"{lab[tmin]:10s} {lat:2d}s {thr:.2f} | {len(v):5d} {sum(1 for a in pn if a>0)/len(pn)*100:5.1f}%  {st.mean(p for _,_,p in v):.2f}   {mean*100:+6.2f}c ±{1.96*se*100:.2f}  ${mean*100*len(v)/days*30:8.0f}   {st.median(q for _,q,_ in v):8.0f}")

# ---- Calibration: model vs Kalshi traded price, vs actual outcome ----
print("\nCalibration (sampled every 15s): when model/market says X%, how often does Yes actually win?")
B=lambda p:min(int(p*10),9)
cm={};ck={};head={}
for tk,y,op,cl,path,K in W:
    for s in range(30,897,15):
        f=fair(path,s); a=K.get(op+s) or K.get(op+s+1)
        if a and a["y"][1]>0:
            kp=a["y"][2]/a["y"][1]
            cm.setdefault(B(f),[]).append(y); ck.setdefault(B(kp),[]).append(y)
            # who's closer to truth?
            ph=("early" if s<600 else "late")
            e=head.setdefault(ph,[0,0,0]); e[0]+=(f-y)**2; e[1]+=(kp-y)**2; e[2]+=1
print(" bucket  | model: n  actual Yes | Kalshi price: n  actual Yes")
for b in range(10):
    a=cm.get(b,[]);k=ck.get(b,[])
    print(f" {b*10:2d}-{b*10+10:3d}% | {len(a):6d}  {sum(a)/len(a)*100 if a else 0:5.1f}%   | {len(k):6d}  {sum(k)/len(k)*100 if k else 0:5.1f}%")
for ph,(em,ek,n) in head.items():
    print(f"Brier score ({ph}, lower=better): model {em/n:.4f} vs Kalshi {ek/n:.4f}")

# ---- Lag: does Kalshi's price react to BTC moves with a delay? ----
print("\nLag test: correlation of BTC-implied fair-value change (1s) with Kalshi traded-price change k seconds later")
import itertools
acc={k:[0,0,0,0,0] for k in range(-3,11)}  # sum xy, x2, y2, n per lag (on 1s diffs, min 2-13 of window)
for tk,y,op,cl,path,K in W:
    F=[fair(path,s) for s in range(0,898)]
    kp=[];last=None
    for s in range(0,898):
        a=K.get(op+s); v=[]
        if a:
            if a["y"][1]>0: v.append(a["y"][2]/a["y"][1])
            if a["n"][1]>0: v.append(1-a["n"][2]/a["n"][1])
        if v: last=sum(v)/len(v)
        kp.append(last)
    for s in range(120,780):
        dx=F[s]-F[s-1]
        for k in acc:
            j=s+k
            if kp[j] is None or kp[j-1] is None: continue
            dy=kp[j]-kp[j-1]; e=acc[k]; e[0]+=dx*dy; e[1]+=dx*dx; e[2]+=dy*dy; e[3]+=1
for k,(xy,x2,y2,n,_) in acc.items():
    r=xy/math.sqrt(x2*y2) if x2 and y2 else 0
    print(f"  Kalshi move {k:+3d}s after BTC move: corr {r:+.3f} " + "#"*int(max(r,0)*100))
