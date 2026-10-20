# Paper trader for the calm-market version of the friend's rule. Sends NO orders: reads public data, logs what it would do.
# Start/stop/status: bash scripts/paper_start.sh | paper_stop.sh | paper_status.sh   Report: python3 -P scripts/paper_report.py
#
# Algo v1 (variant "v1", chosen 2026-10-19 from the 66-day backtests, RESEARCH_RUNBOOK): window's 2h BTC range <= 3.1%; after min 2,
# BTC proxy >= 0.175% past Kalshi's strike -> buy that side if the best ask is 80-95c; stop = sell at the bid when BTC is back at
# the strike; otherwise hold to settlement. At entry and at the stop it records the order book on the side we'd sell into
# (her check: is there enough to get out?). Other variants are logged on the same live data for comparison, incl. the original
# 65-75c / 95c-exit rules (old variants).
# BTC proxy = research composite weights (Coinbase/Kraken; Binance is geo-blocked here, its 10% weight is spread over the other two),
# plus a live bias correction: median of (official settlement - our 60s average) over the last 96 settled windows.
# Output in logs/paper/: trades.csv (one row per variant entry, written at settlement), windows.csv (one row per window),
# ticks-YYYY-MM-DD.csv (per-second prices + top of book, for re-testing other rules later), paper.log.
import asyncio,json,ssl,time,math,csv,os,datetime as dt,urllib.request,traceback
import websockets
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT=os.environ.get("PAPER_OUT") or os.path.join(ROOT,"logs","paper"); os.makedirs(OUT,exist_ok=True)
CTX=ssl.create_default_context(cafile="/etc/ssl/cert.pem")
KAL="https://api.elections.kalshi.com/trade-api/v2"
CW=json.load(open(os.path.join(ROOT,"data","composite_weights.json")))
WCB,WKR=CW["w_cb"]/(CW["w_cb"]+CW["w_kr"]),CW["w_kr"]/(CW["w_cb"]+CW["w_kr"]); OFFSET=CW["offset"]
SIZE=1500; BIG=6300; TP=0.95; CALM=0.006; MAXR=0.031
# band = buy only if the best ask is in it; stop: "cross" = BTC back at the strike, float = our best bid <= entry - x; tp: sell at 95c or None = hold
OLD=dict(band=(0.65,0.75),stop=None,tp=TP)
NEW=dict(band=(0.80,0.95),stop="cross",tp=None,vol="max")
VARIANTS=[dict(name="v1",mny=0.00175,**NEW),
          dict(NEW,name="v1_calm",mny=0.00175,vol="calm"),
          dict(NEW,name="v1_mny15",mny=0.0015),
          dict(NEW,name="v1_drop25",mny=0.00175,stop=0.25),
          dict(NEW,name="v1_tp95",mny=0.00175,tp=TP),
          dict(NEW,name="v1_bookcheck",mny=0.00175,book=True),       # v1, but only if bids within 20c below the ask cover SIZE
          dict(OLD,name="main",mny=0.0010,vol="calm"),dict(OLD,name="mny15_calm",mny=0.0015,vol="calm"),
          dict(OLD,name="mny10_any",mny=0.0010,vol="any"),dict(OLD,name="mny10_volatile",mny=0.0010,vol="volatile")]
STATE=os.path.join(OUT,"state.json")
def log(*a):
    with open(os.path.join(OUT,"paper.log"),"a") as f: print(dt.datetime.now(dt.timezone.utc).strftime("%FT%TZ"),*a,file=f,flush=True)
def iso(s): return dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()
def fee(p,n): return math.ceil(0.07*n*p*(1-p)*100-1e-9)/100
def http(url):
    with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"paper-research"}),timeout=5,context=CTX) as r: return json.load(r)
async def get(url):
    try: return await asyncio.to_thread(http,url)
    except Exception as e: log("http fail",url.split("?")[0][-60:],type(e).__name__); return None
def append(fn,row,header):
    p=os.path.join(OUT,fn); new=not os.path.exists(p)
    with open(p,"a",newline="") as f:
        w=csv.writer(f)
        if new: w.writerow(header)
        w.writerow(row)
px={"cb":(None,0.0),"kr":(None,0.0)}           # last trade price, local receive time
async def feed(name,url,sub,parse):
    back=1
    while True:
        try:
            async with websockets.connect(url,open_timeout=10,ping_interval=20,ssl=CTX,max_size=2**22) as ws:
                await ws.send(json.dumps(sub)); log(name,"connected"); back=1
                async for m in ws:
                    p=parse(json.loads(m))
                    if p: px[name]=(p,time.time())
        except Exception as e: log(name,"disconnected",type(e).__name__,str(e)[:80])
        await asyncio.sleep(back); back=min(back*2,30)
def p_cb(d): return float(d["price"]) if d.get("type") in ("match","last_match") else None
def p_kr(d): return float(d["data"][-1]["price"]) if d.get("channel")=="trade" and d.get("data") else None
def composite(now):
    cb,tcb=px["cb"]; kr,tkr=px["kr"]
    ok_cb=cb is not None and now-tcb<15; ok_kr=kr is not None and now-tkr<30
    if ok_cb and ok_kr: return WCB*cb+WKR*kr+OFFSET
    if ok_cb: return cb+OFFSET
    if ok_kr: return kr+OFFSET
    return None
minutes={}                                      # minute -> [hi, lo, sum, n] of BTC, for the 2h range
def add_minute(t,v):
    k=int(t//60); m=minutes.setdefault(k,[v,v,0.0,0]); m[0]=max(m[0],v); m[1]=min(m[1],v); m[2]+=v; m[3]+=1
def range2h(t):
    ks=[k for k in range(int(t//60)-120,int(t//60)) if k in minutes]
    if len(ks)<100: return None
    hi=max(minutes[k][0] for k in ks); lo=min(minutes[k][1] for k in ks)
    mean=sum(minutes[k][2] for k in ks)/sum(minutes[k][3] for k in ks)
    return (hi-lo)/mean
async def seed_minutes():
    d=await get("https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=60")
    for t,lo,hi,o,c,v in (d or []):
        if int(t//60) not in minutes: minutes[int(t//60)]=[hi+OFFSET,lo+OFFSET,(o+c)/2+OFFSET,1]
    log("seeded",len(d or []),"minutes of Coinbase candles")
def book_side(ob,side):
    """asks to BUY `side` (ascending price, qty) and bids to SELL it (descending). Kalshi books hold bids only."""
    yb=[(float(p),float(q)) for p,q in ob.get("yes_dollars") or []]; nb=[(float(p),float(q)) for p,q in ob.get("no_dollars") or []]
    own,opp=(yb,nb) if side=="y" else (nb,yb)
    asks=sorted(((round(1-p,4),q) for p,q in opp)); bids=sorted(own,reverse=True)
    return asks,bids
def walk(asks,n,cap,sell=False):
    got=cost=0.0
    for p,q in asks:
        if (p<cap) if sell else (p>cap): break
        take=min(q,n-got); got+=take; cost+=take*p
        if got>=n: break
    return (cost/got if got else None),got
W=None                                          # current window state
pending=[]                                      # closed windows awaiting settlement
errs=[]                                         # official settlement minus our raw 60s average, recent windows
def bias():                                     # live correction of the proxy: median error over the last 96 windows
    e=sorted(errs[-96:]); return e[len(e)//2] if len(e)>=4 else 0.0
def save_state():                               # current window + unsettled windows survive a restart
    json.dump({"W":W,"pending":pending,"errs":errs},open(STATE+".tmp","w")); os.replace(STATE+".tmp",STATE)
def load_state():
    global W
    try: d=json.load(open(STATE)); W=d.get("W"); pending.extend(d.get("pending") or []); errs.extend(d.get("errs") or []); log("restored state",W and W["ticker"],"pending",len(pending))
    except Exception: pass
async def new_window(now):
    global W
    d=await get(f"{KAL}/markets?series_ticker=KXBTC15M&status=open&limit=10")
    if not d: return
    cur=[m for m in d["markets"] if iso(m["open_time"])<=now<iso(m["close_time"])]
    if not cur or cur[0].get("floor_strike") is None: return
    m=cur[0]
    if W and W["ticker"]==m["ticker"]: return
    if W: pending.append(W)
    op=iso(m["open_time"]); r=range2h(op)
    W=dict(ticker=m["ticker"],op=op,cl=iso(m["close_time"]),K0=float(m["floor_strike"]),range2h=r,pos={},comp_last60=[],ticks=0)
    save_state()
    log("window",W["ticker"],"strike",W["K0"],"2h range",f"{r*100:.3f}%" if r is not None else "n/a")
def vol_ok(v,r):
    if v["vol"]=="any": return True
    if r is None: return False
    if v["vol"]=="max": return r<=MAXR
    return r<=CALM if v["vol"]=="calm" else r>CALM
def depth(bids,floor): return sum(q for p,q in bids if p>=floor-1e-9)
async def tick(now,comp):
    if W is None or now>=W["cl"]:
        await new_window(now)
    if W is None or now>=W["cl"]: return
    ob=await get(f"{KAL}/markets/{W['ticker']}/orderbook")
    ob=(ob or {}).get("orderbook_fp")
    s=now-W["op"]; W["ticks"]+=1
    if comp is not None and s>=840: W["comp_last60"].append(comp)
    ya=yb=None
    if ob is not None:
        a,b=book_side(ob,"y"); ya=a[0][0] if a else None; yb=b[0][0] if b else None
    day=dt.datetime.fromtimestamp(now,dt.timezone.utc).strftime("%F")
    append(f"ticks-{day}.csv",[f"{now:.3f}",W["ticker"],int(s),px["cb"][0],px["kr"][0],None if comp is None else round(comp,2),ya,yb],
           ["t","ticker","s","cb","kr","composite","yes_ask","yes_bid"])
    if ob is None or comp is None: return
    changed=False
    for v in VARIANTS:
        p=W["pos"].get(v["name"])
        if p is None and 120<=s<=870 and vol_ok(v,W["range2h"]):
            m=(comp+bias()-W["K0"])/W["K0"]; side="y" if m>=v["mny"] else ("n" if -m>=v["mny"] else None)
            if not side: continue
            asks,bids=book_side(ob,side); band=v["band"]
            if not asks or not (band[0]<=asks[0][0]<=band[1]): continue
            a0=asks[0][0]; dep=depth(bids,a0-0.20)
            if v.get("book") and dep<SIZE: continue
            avg,got=walk(asks,SIZE,band[1]); avgall,gotall=walk(asks,SIZE,0.99); avgbig,_=walk(asks,BIG,0.99)
            W["pos"][v["name"]]=dict(side=side,s=int(s),m=abs(m),ask=a0,avg_size=avgall,filled_in_band=got,tp_s=None,
                                     avg_big=avgbig,bid0=bids[0][0] if bids else None,bid_depth20=dep,ask_depth=sum(q for p_,q in asks if p_<=band[1]),
                                     stop_s=None)
            log("ENTER",v["name"],W["ticker"],side,f"s={int(s)} lead={abs(m)*100:.3f}% ask={a0:.2f} {SIZE}ct avg={avgall} bids<=20c below={dep:.0f}")
            changed=True
        elif p is not None and p["tp_s"] is None and p.get("stop_s") is None:
            _,bids=book_side(ob,p["side"]); tp=v.get("tp",TP); st=v.get("stop")
            if tp and bids and bids[0][0]>=tp:
                p["tp_s"]=int(s); log("TP",v["name"],W["ticker"],f"s={int(s)} bid={bids[0][0]:.2f}"); changed=True; continue
            if st is None: continue
            lead=(comp+bias()-W["K0"])*(1 if p["side"]=="y" else -1)
            hit=(lead<=0) if st=="cross" else (bool(bids) and bids[0][0]<=p["ask"]-st)
            if hit:
                a1,_=walk(bids,SIZE,0.0,sell=True); a2,g2=walk(bids,BIG,0.0,sell=True)
                p.update(stop_s=int(s),stop_bid=bids[0][0] if bids else 0.0,stop_avg=a1,stop_avg_big=a2 if g2>=BIG else None,
                         stop_depth20=depth(bids,(bids[0][0] if bids else 0)-0.20))
                log("STOP",v["name"],W["ticker"],f"s={int(s)} bid={p['stop_bid']:.2f} avg {SIZE}ct={a1} avg {BIG}ct={p['stop_avg_big']} depth20={p['stop_depth20']:.0f}")
                changed=True
    if changed: save_state()
async def settle_loop():
    while True:
        await asyncio.sleep(30)
        for w in list(pending):
            if time.time()<w["cl"]+60: continue
            d=await get(f"{KAL}/markets/{w['ticker']}")
            m=(d or {}).get("market") or {}
            if m.get("result") not in ("yes","no"):
                if time.time()>w["cl"]+3*3600: pending.remove(w); save_state(); log("gave up settling",w["ticker"])
                continue
            y=m["result"]=="yes"; ev=m.get("expiration_value")
            raw=sum(w["comp_last60"])/len(w["comp_last60"]) if len(w["comp_last60"])>=50 else None
            b=bias(); ours=None if raw is None else raw+b
            append("windows.csv",[w["ticker"],int(w["op"]),w["K0"],w["range2h"],ev,m["result"],None if ours is None else round(ours,2),
                    None if ours is None else int((ours>=w["K0"])==y),w["ticks"],None if raw is None else round(raw,2),round(b,2)],
                   ["ticker","open","strike","range2h","settle_value","result","our_settle","our_outcome_match","ticks","our_raw","bias_used"])
            if raw is not None and ev is not None: errs.append(float(ev)-raw); del errs[:-200]
            for name,p in w["pos"].items():
                win=y if p["side"]=="y" else not y; e=p["ask"]
                hold=(1 if win else 0)-e-fee(e,5)/5
                stopped=p.get("stop_s") is not None; tph=p["tp_s"] is not None
                def usd_at(n,E,ex):                  # $ for n contracts bought at avg E; ex = avg exit price or None = settle
                    if E is None: return None
                    c=n*E+fee(E,n)
                    return (n*ex-c-fee(ex,n)) if ex is not None else ((n if win else 0)-c)
                if tph: pct=TP-e-fee(e,5)/5-fee(TP,5)/5; ex1=exb=TP
                elif stopped:
                    sb=p["stop_bid"]; pct=sb-e-fee(e,5)/5-fee(sb,5)/5; ex1=p["stop_avg"] or 0.0; exb=p.get("stop_avg_big") or 0.0
                else: pct=hold; ex1=exb=None
                outcome="tp" if tph else ("stop" if stopped else "settle")
                append("trades_v2.csv",[dt.datetime.fromtimestamp(w["op"],dt.timezone.utc).strftime("%F %H:%M"),w["ticker"],name,p["side"],p["s"],
                        round(p["m"],5),w["range2h"],e,p["avg_size"],p.get("avg_big"),p["filled_in_band"],p.get("ask_depth"),p.get("bid0"),p.get("bid_depth20"),
                        outcome,p["tp_s"],p.get("stop_s"),p.get("stop_bid"),p.get("stop_avg"),p.get("stop_avg_big"),p.get("stop_depth20"),int(win),
                        round(hold,4),round(pct,4),usd_at(SIZE,p["avg_size"],ex1),usd_at(BIG,p.get("avg_big"),exb)],
                       ["open_utc","ticker","variant","side","entry_s","lead","range2h","best_ask","avg_px_1500","avg_px_6300","filled_in_band",
                        "ask_depth_band","bid0","bid_depth_20c","outcome","tp_s","stop_s","stop_bid","stop_avg_1500","stop_avg_6300","stop_depth_20c",
                        "win","pnl_hold_per_ct","pnl_per_ct","usd_1500","usd_6300"])
            log("settled",w["ticker"],m["result"],"value",ev,"ours",None if ours is None else round(ours,2),"bias now",round(bias(),2),"positions",len(w["pos"]))
            pending.remove(w); save_state()
async def main():
    log("==== start ====")
    load_state()
    await seed_minutes()
    asyncio.create_task(feed("cb","wss://ws-feed.exchange.coinbase.com",{"type":"subscribe","product_ids":["BTC-USD"],"channels":["matches"]},p_cb))
    asyncio.create_task(feed("kr","wss://ws.kraken.com/v2",{"method":"subscribe","params":{"channel":"trade","symbol":["BTC/USD"]}},p_kr))
    asyncio.create_task(settle_loop())
    nxt=math.floor(time.time())+1; last_hb=0
    while True:
        await asyncio.sleep(max(0,nxt-time.time())); now=time.time(); nxt=math.floor(now)+1
        comp=composite(now)
        if comp is not None: add_minute(now,comp)
        try: await tick(now,comp)
        except Exception: log("tick error",traceback.format_exc()[-300:])
        if now-last_hb>=300:
            last_hb=now; log("heartbeat comp",None if comp is None else round(comp,2),"window",W and W["ticker"],"pending",len(pending),"bias",round(bias(),2),
                             "range2h",W and W["range2h"])
if __name__=="__main__": asyncio.run(main())
