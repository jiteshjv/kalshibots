import json,glob,os,math,statistics as st,datetime as dt
m={x["ticker"]:x for x in json.load(open("settled.json"))}
def ts(s): return int(dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
def fee(p): return 0.07*p*(1-p)
W=[]
for f in glob.glob("ktr/*.json"):
    tk=os.path.basename(f)[:-5]; x=m[tk]
    if x["result"] in("yes","no"):
        W.append((ts(x["close_time"])-900,x["result"]=="yes",{int(k):v for k,v in json.load(open(f)).items()}))
W.sort(key=lambda w:w[0])
print("Favorite-buying: first time a real taker trade on the favorite side prints in [lo,hi], buy at that VWAP, hold.\n")
print(" price band  from-min | trades  win%   avg paid  P&L/contract (fees in)   1st half  2nd half")
for lo,hi in ((0.80,0.90),(0.90,0.95),(0.95,0.98),(0.98,0.995)):
  for tmin in (0,5,10,13):
    pn=[]
    for op,y,K in W:
        for s in range(op+60*tmin,op+897):
            a=K.get(s)
            if not a: continue
            hit=None
            for side,win in (("y",y),("n",not y)):
                if a[side][1]>0:
                    p=a[side][2]/a[side][1]
                    if lo<=p<=hi: hit=(1.0*win-p-fee(p),p)
            if hit: pn.append(hit); break
    if not pn: continue
    v=[a for a,_ in pn]; se=1.96*st.pstdev(v)/math.sqrt(len(v)); h=len(v)//2
    print(f" {lo:.2f}-{hi:.3f}  min{tmin:2d}+ | {len(v):5d}  {sum(1 for a in v if a>0)/len(v)*100:5.1f}%  {st.mean(p for _,p in pn):.3f}   {st.mean(v)*100:+6.2f}c ±{se*100:.2f}        {st.mean(v[:h])*100:+.2f}c   {st.mean(v[h:])*100:+.2f}c")
