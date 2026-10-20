import json,glob,zipfile,io,csv,os,math,statistics as st,datetime as dt
P={}
for f in sorted(glob.glob("bn/*.zip")):
    z=zipfile.ZipFile(f)
    for r in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
        t=int(r[0]); t=t//1000000 if t>1e14 else t//1000; P[t]=(float(r[2]),float(r[3]),float(r[4]))  # high,low,close
for t,c in json.load(open("bn/today.json")): P[t//1000]=(float(c),float(c),float(c))
def px(t):
    for k in range(10):
        if t-k in P: return P[t-k][2]
def ts(s): return int(dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
def tfee(p): return math.ceil(0.07*p*(1-p)*100-1e-9)/100 if False else 0.07*p*(1-p)
def mfee(p): return 0.0175*p*(1-p)
meta={x["ticker"]:x for x in json.load(open("settled.json"))}
W=[]
for f in glob.glob("ktr/*.json"):
    tk=os.path.basename(f)[:-5]; x=meta[tk]
    if x["result"] not in("yes","no"): continue
    op=ts(x["close_time"])-900
    hs=[P[t] for t in range(op-7200,op) if t in P]
    if len(hs)<6000: continue
    hi=max(h for h,_,_ in hs); lo=min(l for _,l,_ in hs); avg=sum(c for _,_,c in hs)/len(hs)
    path=[px(t) for t in range(op-60,op+900)]
    if None in path: continue
    W.append(dict(op=op,y=x["result"]=="yes",vol=(hi-lo)/avg,ref=sum(path[:60])/60,path=path,
                  K={int(k):v for k,v in json.load(open(f)).items()}))
W.sort(key=lambda w:w["op"]); days=(W[-1]["op"]-W[0]["op"])/86400+1/96
vols=sorted(w["vol"] for w in W)
print(f"windows {len(W)} over {days:.1f} days | 2h range: median {st.median(vols)*100:.2f}%  share <=0.6%: {sum(v<=0.006 for v in vols)/len(vols)*100:.0f}%")
def buyfill(K,s,side):         # VWAP of taker buys on our side over next 3s
    q=n=0
    for u in range(s,s+3):
        a=K.get(u)
        if a and a[side][1]>0: q+=a[side][1]; n+=a[side][2]
    return (n/q,q) if q else (None,0)
def run(w,mny,volrule,stop,exit95,LAT=1):
    vok={"none":True,"<=0.6%":w["vol"]<=0.006,">=0.6%":w["vol"]>=0.006}[volrule]
    if not vok: return None
    path,ref,K,op=w["path"],w["ref"],w["K"],w["op"]
    for s in range(120,840,4):                     # no entries first 2 min; check every 4s; stop entering at min 14
        p=path[60+s]; up=(p-ref)/ref
        side="y" if up>=mny else ("n" if -up>=mny else None)
        if not side: continue
        e,q=buyfill(K,op+s+LAT,side)
        if not e or e>=0.95: continue
        win=w["y"] if side=="y" else not w["y"]; opp="n" if side=="y" else "y"
        for u in range(s+LAT+3,900):
            a=K.get(op+u)
            if exit95 and a and a[side][1]>0 and a[side][2]/a[side][1]>=0.95:   # someone lifts our resting 95c offer
                return (0.95-e-tfee(e)-mfee(0.95),e,"tp")
            if stop and a and a[opp][1]>0:
                bid=1-a[opp][2]/a[opp][1]        # what a taker sell of our side gets
                pnow=path[60+u]; back=((pnow-ref) if side=="y" else (ref-pnow))<0
                if (stop=="bid<0.40" and bid<0.40) or (stop=="BTC crosses target" and back):
                    return (bid-e-tfee(e)-tfee(bid),e,"stop")
        return ((1.0 if win else 0.0)-e-tfee(e),e,"settle")
    return None
print("\nRules: enter after min 2, check every 4s, BTC beyond target by >= moneyness, 1s latency, real taker fills (VWAP next 3s).")
print("Fees: taker 7%·p(1-p) on entry/stops, maker 1.75%·p(1-p) on the 95c exit.\n")
print(f"{'vol filter':8s} {'mny':>5s} {'exit':6s} {'stop':19s}| trades/day win%  avg entry  avg win  avg loss  EV/contract ±95%   1st/2nd half  | EV at $1,200 risk")
out=[]
for volrule in ("none","<=0.6%",">=0.6%"):
  for mny in (0.0010,0.0015,0.0020):
    for exit95 in (True,False):
      for stop in (None,"bid<0.40","BTC crosses target"):
        r=[x for x in (run(w,mny,volrule,stop,exit95) for w in W) if x]
        if len(r)<30: continue
        v=[a for a,_,_ in r]; wins=[a for a in v if a>0]; los=[a for a in v if a<=0]
        se=1.96*st.pstdev(v)/math.sqrt(len(v)); h=len(v)//2; ent=st.mean(e for _,e,_ in r)
        ev=st.mean(v); cts=1200/ent   # contracts if a full loss = $1,200 risk
        print(f"{volrule:8s} {mny*100:4.2f}% {'95c' if exit95 else 'hold':6s} {str(stop):19s}| {len(v)/days:5.1f}   {len(wins)/len(v)*100:5.1f}%  {ent:.3f}    {st.mean(wins)*100 if wins else 0:+5.1f}c  {st.mean(los)*100 if los else 0:+6.1f}c   {ev*100:+5.2f}c ±{se*100:.2f}   {st.mean(v[:h])*100:+.2f}/{st.mean(v[h:])*100:+.2f}  | ${ev*cts:+6.0f}/trade")
