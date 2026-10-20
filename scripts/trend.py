import json,math,datetime as dt
c={int(k):v for k,v in json.load(open("cb1m.json")).items()}
m=[x for x in json.load(open("settled.json")) if x["result"] in("yes","no")]; m.sort(key=lambda x:x["close_time"])
def ts(s): return int(dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
def px(t):
    for k in range(5):
        v=c.get(t-60*k)
        if v: return v
rows=[]
for x in m:
    op=ts(x["close_time"])-900; p0=px(op-60)
    if not p0: continue
    r={h:(p0/px(op-60-3600*h)-1) if px(op-60-3600*h) else None for h in (1,4,12,24,72,168)}
    # trend consistency: fraction of last 8 15m candles green
    g=[px(op-60-900*k)>px(op-60-900*(k+1)) for k in range(8) if px(op-60-900*(k+1))]
    rows.append((x["result"]=="yes",r,sum(g)/len(g) if g else .5))
def rep(name,v):
    if not v: return
    h1,h2=v[:len(v)//2],v[len(v)//2:]; se=1.96*math.sqrt(.25/len(v))
    print(f"{name:46s} n={len(v):5d} hit={sum(v)/len(v):.3f} ±{se:.3f} | halves {sum(h1)/len(h1):.3f} {sum(h2)/len(h2):.3f}")
print("== Follow the trend: bet next 15m goes same way as trend over last H hours ==")
for h in (1,4,12,24,72,168):
    rep(f"{h}h trend",[ (r[h]>0)==y for y,r,_ in rows if r[h] is not None])
print("\n== Only STRONG trends (top 20% biggest moves) ==")
for h in (4,24,72,168):
    v=sorted(abs(r[h]) for _,r,_ in rows if r[h] is not None); cut=v[int(.8*len(v))]
    rep(f"{h}h strong trend (>{cut*100:.1f}%) -> follow",[ (r[h]>0)==y for y,r,_ in rows if r[h] is not None and abs(r[h])>cut])
print("\n== Steady trend: 6+ of last 8 candles same color ==")
rep("6+/8 green -> Up",[y for y,_,g in rows if g>=.75])
rep("6+/8 red -> Down",[not y for y,_,g in rows if g<=.25])
print("\n== Trend + pullback: 24h uptrend but last 1h down -> Up (and mirror) ==")
rep("24h up & 1h down -> Up",[y for y,r,_ in rows if r[24] and r[1] and r[24]>0 and r[1]<0])
rep("24h down & 1h up -> Down",[not y for y,r,_ in rows if r[24] and r[1] and r[24]<0 and r[1]>0])
print("\n== Up-rate in up-days vs down-days (24h trend) ==")
up=[y for y,r,_ in rows if r[24] and r[24]>0.02]; dn=[y for y,r,_ in rows if r[24] and r[24]<-0.02]
print(f"BTC up >2% over 24h: next 15m Up {sum(up)/len(up):.3f} (n={len(up)}) | BTC down >2%: Up {sum(dn)/len(dn):.3f} (n={len(dn)})")
