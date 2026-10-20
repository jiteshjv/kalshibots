import json,math,datetime as dt
from math import erf,sqrt
c={int(k):v for k,v in json.load(open("cb1m.json")).items()}
kc=json.load(open("kc.json"))
m=[x for x in json.load(open("settled.json")) if x["result"] in("yes","no") and kc.get(x["ticker"])]
m.sort(key=lambda x:x["close_time"])
def ts(s): return int(dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
SD=36.0
def Phi(z): return 0.5*(1+erf(z/sqrt(2)))
def fee(p): return math.ceil(0.07*p*(1-p)*100-1e-9)/100   # Kalshi taker fee per contract (1-lot rounding: worst case)
def feeb(p): return 0.07*p*(1-p)                          # large-order fee
def f(v): return float(v) if v not in (None,"") else None
W=[]
for x in m:
    cl=ts(x["close_time"]); op=cl-900; y=x["result"]=="yes"
    ref=c.get(op-60)
    if ref is None: continue
    bym={}
    for k in kc[x["ticker"]]:
        t=(k["end_period_ts"]-op)//60
        ya=f(k["yes_ask"].get("close_dollars")); yb=f(k["yes_bid"].get("close_dollars"))
        yah=f(k["yes_ask"].get("high_dollars")); ybl=f(k["yes_bid"].get("low_dollars"))
        bym[t]=(ya,yb,yah,ybl)
    W.append((x["close_time"],y,ref,op,bym))
print("windows with Kalshi prices:",len(W),W[0][0][:10],"->",W[-1][0][:10])
days=(ts(W[-1][0])-ts(W[0][0]))/86400
def run(tmin,thr,pess=False):
    pn=[];
    for ct,y,ref,op,bym in W:
        p=c.get(op+60*(tmin-1)); q=bym.get(tmin); qn=bym.get(tmin+1)
        if p is None or not q or q[0] is None or q[1] is None: continue
        rem=15-tmin; z=(p-ref)/(SD*sqrt(rem-1+1/3)); pu=Phi(z)
        ya,yb=q[0],q[1]
        if pess:  # fill at worst ask seen during the NEXT minute
            if not qn or qn[2] is None or qn[3] is None: continue
            ya=qn[2]; yb=qn[3]
        na=1-yb
        eY=pu-ya-feeb(ya); eN=(1-pu)-na-feeb(na)
        if eY>thr and ya<0.99: pn.append((y*1.0-ya-feeb(ya)))
        elif eN>thr and na<0.99: pn.append(((not y)*1.0-na-feeb(na)))
    if not pn: return None
    w=sum(1 for v in pn if v>0)
    return len(pn),w/len(pn),sum(pn)/len(pn),sum(pn)
print("\nStrategy: at minute t, compare model P(Up) to Kalshi ask; buy the side if edge > threshold; hold to settlement. Fees incl.")
print(" t  thr | trades  win%   avg P&L/contract   total(1 contract/trade)  /month   || pessimistic fill (worst ask next min)")
for t in (3,5,7,9,10,11,12,13):
    for thr in (0.0,0.03,0.06):
        a=run(t,thr); b=run(t,thr,True)
        if a:
            s=f"{t:2d} {thr:.2f} | {a[0]:5d}  {a[1]*100:5.1f}%   {a[2]*100:+6.2f}c   ${a[3]:8.1f}  ${a[3]/days*30:7.1f}"
            if b: s+=f"  || {b[0]:5d} {b[1]*100:5.1f}% {b[2]*100:+6.2f}c ${b[3]/days*30:7.1f}/mo"
            print(s)
print("\nBaseline 'the 85% predictor': at minute 12 always buy the side BTC is currently on, at the ask")
pn=[]
for ct,y,ref,op,bym in W:
    p=c.get(op+60*11); q=bym.get(12)
    if p is None or not q or q[0] is None or q[1] is None: continue
    if p>=ref: pn.append(y-q[0]-feeb(q[0]))
    else: na=1-q[1]; pn.append((not y)-na-feeb(na))
print(f"trades {len(pn)} win {sum(1 for v in pn if v>0)/len(pn)*100:.1f}%  avg {sum(pn)/len(pn)*100:+.2f}c/contract  total ${sum(pn):.0f} per 1-contract")
