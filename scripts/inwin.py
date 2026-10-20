import json,math,datetime as dt,statistics as st
c={int(k):v for k,v in json.load(open("cb1m.json")).items()}
m=json.load(open("settled.json"))
m=[x for x in m if x["result"] in("yes","no")]; m.sort(key=lambda x:x["close_time"])
def ts(s): return int(dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
rows=[]
for x in m:
    cl=ts(x["close_time"]); op=cl-900
    ref=c.get(op-60)   # close of minute before open ~ strike proxy
    path=[c.get(op+60*(t-1)) for t in range(1,15)]  # price at minute t (t=1..14)
    hist=[c.get(op-60*k) for k in range(1,241)]
    if ref is None or None in path or None in hist: continue
    rows.append((x["result"]=="yes",ref,path,hist[::-1]))
n=len(rows); print("usable markets",n)
# vol per minute
d=[];[d.extend([b-a for a,b in zip(r[3],r[3][1:])][-60:]) for r in rows[::50]]
sd=st.pstdev(d); print("1m sd $%.0f"%sd)
print("\n== In-window: predict sign(price_now - open) ==")
print("min  hit_all | confident-only (|z|>1):  hit   %bets | (|z|>1.5): hit  %bets")
for t in (1,3,5,7,9,10,11,12,13,14):
    hits=[];c1=[];c2=[]
    for y,ref,p,h in rows:
        diff=p[t-1]-ref; rem=15-t
        z=diff/(sd*math.sqrt(max(rem-1+1/3,0.33)))
        ok=(diff>=0)==y; hits.append(ok)
        if abs(z)>1: c1.append(ok)
        if abs(z)>1.5: c2.append(ok)
    print(f"{t:3d}  {sum(hits)/n:.3f}   |   {sum(c1)/max(len(c1),1):.3f}  {len(c1)/n*100:5.1f}%  |  {sum(c2)/max(len(c2),1):.3f} {len(c2)/n*100:5.1f}%")
print("\n== Moving average / trend signals (decided at window open) ==")
def sma(a,k): return sum(a[-k:])/k
def ema(a,k):
    e=a[0];al=2/(k+1)
    for v in a: e=al*v+(1-al)*e
    return e
def rsi(a,k=14):
    g=[max(b-a_,0) for a_,b in zip(a,a[1:])][-k:]; l=[max(a_-b,0) for a_,b in zip(a,a[1:])][-k:]
    return 100 if sum(l)==0 else 100-100/(1+sum(g)/sum(l))
sig={}
for y,ref,p,h in rows:
    h15=h[::-15][::-1]  # 15m closes
    tests={
     "price > SMA20 (1m)":h[-1]>sma(h,20),
     "price > SMA60 (1m)":h[-1]>sma(h,60),
     "price > SMA240 (4h)":h[-1]>sma(h,240),
     "EMA9 > EMA21 (15m bars)":ema(h15,9)>ema(h15,21),
     "SMA20>SMA60 (1m) golden cross":sma(h,20)>sma(h,60),
    }
    for k,v in tests.items(): sig.setdefault(k,[]).append(v==y)
    r=rsi(h15)
    if r>70: sig.setdefault("RSI15m>70 -> bet Down",[]).append(not y)
    if r<30: sig.setdefault("RSI15m<30 -> bet Up",[]).append(y)
for k,v in sig.items():
    se=1.96*math.sqrt(.25/len(v)); print(f"{k:34s} n={len(v):5d} hit={sum(v)/len(v):.3f} ±{se:.3f}")
