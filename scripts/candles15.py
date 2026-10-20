import json,math,datetime as dt,statistics as st
c={int(k):v for k,v in json.load(open("cb1m.json")).items()}
# need OHLCV: refetch not needed for close; rebuild O/H/L from 1m closes (approx)
m=[x for x in json.load(open("settled.json")) if x["result"] in("yes","no")]; m.sort(key=lambda x:x["close_time"])
def ts(s): return int(dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
def bar(op):
    p=[c.get(op+60*i) for i in range(-1,15)]
    if None in p: return None
    return (p[0],max(p),min(p),p[-1])   # O,H,L,C from 1m closes
res=[]
for x in m:
    op=ts(x["close_time"])-900
    bars=[bar(op-900*k) for k in range(1,21)][::-1]  # last 20 completed 15m candles
    if None in bars: continue
    res.append((x["result"]=="yes",bars))
n=len(res); print("windows",n)
sig={}
def add(k,pred,y): sig.setdefault(k,[]).append(pred==y)
for y,b in res:
    O,H,L,C=b[-1]; O2,H2,L2,C2=b[-2]
    rng=H-L or 1; body=abs(C-O)
    green=C>O
    add("Last candle green -> Up",green,y)
    add("Last candle green -> Down (fade)",not green,y)
    if body<0.15*rng: add("Doji -> fade candle before it",not (C2>O2),y)
    if green and not (C2>O2) and C>O2 and O<C2: add("Bullish engulfing -> Up",True,y)
    if (not green) and C2>O2 and C<O2 and O>C2: add("Bearish engulfing -> Down",False,y)
    lw=min(O,C)-L; uw=H-max(O,C)
    if lw>2*body and lw>0.6*rng: add("Hammer (long lower wick) -> Up",True,y)
    if uw>2*body and uw>0.6*rng: add("Shooting star (long upper wick) -> Down",False,y)
    hi=max(z[1] for z in b[:-1]); lo=min(z[2] for z in b[:-1])
    if C>hi: add("Breakout above 19-bar high -> Up",True,y)
    if C<lo: add("Breakdown below 19-bar low -> Down",False,y)
    closes=[z[3] for z in b]; mu=sum(closes)/20; sd=st.pstdev(closes) or 1
    if C>mu+2*sd: add("Above upper Bollinger -> Down",False,y)
    if C<mu-2*sd: add("Below lower Bollinger -> Up",True,y)
    g3=all(z[3]>z[0] for z in b[-3:]); r3=all(z[3]<z[0] for z in b[-3:])
    if g3: add("3 green candles -> Up",True,y)
    if r3: add("3 red candles -> Down",False,y)
    big=[abs(z[3]-z[0]) for z in b[:-1]]
    if body>2.5*(sum(big)/len(big)): add("Huge candle -> continue",green,y)
H=n//2
print(f"{'pattern':42s} {'n':>5s}  hit    ±95%   | 1st half  2nd half")
for k,v in sig.items():
    se=1.96*math.sqrt(.25/len(v)); idx=[i for i in range(len(v))]
    h1=v[:len(v)//2]; h2=v[len(v)//2:]
    flag=" <-- " if abs(sum(v)/len(v)-.5)>se else ""
    print(f"{k:42s} {len(v):5d}  {sum(v)/len(v):.3f}  ±{se:.3f} |  {sum(h1)/len(h1):.3f}    {sum(h2)/len(h2):.3f}{flag}")
