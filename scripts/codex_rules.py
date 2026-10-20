# Backtest of the Codex bot's proposed rules on the 7-day second-level sample (run from data/).
import json,math,glob,zipfile,io,csv,os,datetime as dt,statistics as st
from math import erf,sqrt
P={}
for f in sorted(glob.glob("bn/*.zip")):
    if f[-14:-4]<"2026-10-06": continue
    z=zipfile.ZipFile(f)
    for r in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
        t=int(r[0]); t=t//1000000 if t>1e14 else t//1000; P[t]=float(r[4])
for t,c in json.load(open("bn/today.json")): P[t//1000]=float(c)
def px(t):
    for k in range(10):
        if t-k in P: return P[t-k]
def ts(s): return int(dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
def Phi(z): return 0.5*(1+erf(z/sqrt(2)))
def fee(p,n=5): return math.ceil(0.07*n*p*(1-p)*100-1e-9)/100/n   # Kalshi rounds fee up to the cent per order
meta={x["ticker"]:x for x in json.load(open("settled.json"))}
W=[]
for x in sorted(meta.values(),key=lambda x:x["close_time"])[-700:]:
    f=f"ktr/{x['ticker']}.json"
    if x["result"] not in("yes","no") or not os.path.exists(f): continue
    op=ts(x["close_time"])-900; path=[px(t) for t in range(op-360,op+900)]
    if None in path: continue
    W.append((x["result"]=="yes",op,path,{int(k):v for k,v in json.load(open(f)).items()}))
print("windows",len(W))
def fair(path,s,volwin,shrink):
    i=360+s; ref=sum(path[300:360])/60
    r=[math.log(path[j]/path[j-1]) for j in range(i-volwin,i)]
    sig=st.pstdev(r)*path[i] or 1e-9; tau=900-s; now=path[i]
    if tau>60: mu=now; var=sig**2*(tau-60)+sig**2*60/3
    else:
        k=tau; done=path[i-(60-k)+1:i+1]; A=sum(done)/len(done) if done else now
        mu=((60-k)*A+k*now)/60; var=(k/60)**2*sig**2*k/3
    p=Phi((mu-ref)/sqrt(max(var,1e-12)))
    return 0.5+shrink*(p-0.5)
def quote(K,t):   # executable asks from real taker prints in [t,t+3): yes-ask, no-ask
    q={"y":[0,0],"n":[0,0]}
    for u in range(t,t+3):
        a=K.get(u)
        if a:
            for s in "yn":
                if a[s][1]>0: q[s][0]+=a[s][1]; q[s][1]+=a[s][2]
    return tuple(q[s][1]/q[s][0] if q[s][0] else None for s in "yn")
def run(thr,volwin,shrink,lo=0.08,hi=0.82,lat=1):
    out=[]
    for y,op,path,K in W:
        for s in range(900-210,900-25):
            f=fair(path,s,volwin,shrink); ya,na=quote(K,op+s+lat)
            if ya is None or na is None or ya+na-1>0.08: continue      # spread filter
            eY=f-ya; eN=(1-f)-na
            side,e,p=("y",eY,ya) if eY>=eN else ("n",eN,na)
            if e>=thr and lo<=p<=hi:
                win=y if side=="y" else not y
                out.append((win-p-fee(p),p,win)); break
    return out
print("\nCodex rules: 210s..25s before close, edge>=thr, price 8-82c, spread<=8c, IOC 5 contracts, 1s latency, fees rounded per order")
print(f"{'vol':>5s} {'shrink':>6s} {'edge':>5s} | trades/day  win%  avg price  P&L/contract ±95%   $/day @5 contracts")
days=(W[-1][1]-W[0][1])/86400+1/96
for volwin in (120,300):
  for shrink in (1.0,0.8):
    for thr in (0.04,0.08,0.12,0.16):
        r=run(thr,volwin,shrink)
        if not r: continue
        v=[a for a,_,_ in r]; se=1.96*st.pstdev(v)/sqrt(len(v))
        print(f"{volwin:4d}s {shrink:6.1f} {thr:5.2f} | {len(v)/days:8.1f}  {sum(w for *_,w in r)/len(r)*100:5.1f}%   {st.mean(p for _,p,_ in r):.2f}     {st.mean(v)*100:+6.2f}c ±{se*100:.2f}   ${st.mean(v)*5*len(v)/days:+6.2f}")
