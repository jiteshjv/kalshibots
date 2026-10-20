# Millisecond-resolution data for the last 7 days of KXBTC15M (run from data/; resumable, every file written atomically).
#   Kalshi trades  -> ktr_ms/<TICKER>.json   [[unix_ts_float, "y"/"n" taker side, taker price, count], ...]
#   Binance aggTrades daily zips -> bn_agg/BTCUSDT-aggTrades-YYYY-MM-DD.zip   (ms timestamps)
#   Coinbase trades -> cb_ms/NNN.json  [[unix_ts_float, price, size, maker_side], ...]  (plan in cb_ms/_plan.json)
import json,subprocess,time,datetime as dt,os,threading,sys
from concurrent.futures import ThreadPoolExecutor
DAY0=dt.date(2026,10,8); DAY1=dt.date(2026,10,15)          # inclusive
START=int(dt.datetime(DAY0.year,DAY0.month,DAY0.day,tzinfo=dt.timezone.utc).timestamp())-3600
END=int(dt.datetime(DAY1.year,DAY1.month,DAY1.day,tzinfo=dt.timezone.utc).timestamp())+86400
def tsof(s): return dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()
def limiter(gap):
    lk=threading.Lock(); last=[0.0]
    def get(u):
        for i in range(10):
            with lk:
                w=last[0]+gap-time.time()
                if w>0: time.sleep(w)
                last[0]=time.time()
            try:
                r=subprocess.run(["curl","-s","-w","\n%{http_code}",u],capture_output=True,text=True,timeout=30).stdout
                b,c=r.rsplit("\n",1)
                if c=="200": return json.loads(b)
            except Exception: pass
            time.sleep(1+i)
        return None
    return get
def save(fn,obj): json.dump(obj,open(fn+".tmp","w"),separators=(",",":")); os.replace(fn+".tmp",fn)
def kalshi():
    os.makedirs("ktr_ms",exist_ok=True); get=limiter(0.11)
    m=[x for x in json.load(open("settled.json")) if START+3600<=tsof(x["close_time"])-900 and tsof(x["close_time"])<=END]
    def one(x):
        fn=f"ktr_ms/{x['ticker']}.json"
        if os.path.exists(fn): return 1
        out=[]; cur=""
        while True:
            d=get(f"https://api.elections.kalshi.com/trade-api/v2/markets/trades?ticker={x['ticker']}&limit=1000"+(f"&cursor={cur}" if cur else ""))
            if d is None: return 0
            for t in d["trades"]:
                side=t["taker_side"]; px=float(t["yes_price_dollars"]) if side=="yes" else float(t["no_price_dollars"])
                out.append([tsof(t["created_time"]),side[0],px,float(t["count_fp"])])
            cur=d.get("cursor")
            if not cur or not d["trades"]: break
        out.sort(); save(fn,out); return 1
    done=0
    with ThreadPoolExecutor(3) as ex:
        for r in ex.map(one,m):
            done+=r
            if done%25==0: print("kalshi",done,"/",len(m),flush=True)
    print("kalshi FINISHED",done,"/",len(m),flush=True)
def binance():
    os.makedirs("bn_agg",exist_ok=True); d=DAY0
    while d<=DAY1:
        f=f"bn_agg/BTCUSDT-aggTrades-{d}.zip"
        if not os.path.exists(f):
            r=subprocess.run(["curl","-s","-f","-o",f+".tmp",f"https://data.binance.vision/data/spot/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-{d}.zip"])
            if r.returncode==0: os.replace(f+".tmp",f); print("binance got",d,flush=True)
            else: print("binance MISSING",d,flush=True)
        d+=dt.timedelta(days=1)
    print("binance FINISHED",flush=True)
def coinbase():
    os.makedirs("cb_ms",exist_ok=True); get=limiter(0.17); PLAN="cb_ms/_plan.json"
    U="https://api.exchange.coinbase.com/products/BTC-USD/trades"
    def first_at(T):
        top=get(U+"?limit=1")[0]["trade_id"]+1; lo,hi=top-8_000_000,top
        while hi-lo>1000:
            mid=(lo+hi)//2; t=get(f"{U}?limit=1&after={mid}")[0]
            if tsof(t["time"])<T: lo=mid
            else: hi=mid
        return lo
    if os.path.exists(PLAN): lo,top=json.load(open(PLAN))
    else: lo,top=first_at(START),first_at(END)+1000; save(PLAN,[lo,top])
    print("coinbase ids",lo,top,"trades",top-lo,flush=True)
    N=32; step=(top-lo)//N+1
    def chunk(k):
        a=lo+k*step; b=min(lo+(k+1)*step,top); fn=f"cb_ms/{k:03d}.json"
        if os.path.exists(fn): return
        out=[]; cur=b
        while cur>a:
            d=get(f"{U}?limit=1000&after={cur}")
            if not d: break
            for t in d:
                if t["trade_id"]>=a: out.append([tsof(t["time"]),float(t["price"]),float(t["size"]),t["side"][0]])
            cur=d[-1]["trade_id"]
        out.sort(); save(fn,out); print("coinbase chunk",k,"done",flush=True)
    with ThreadPoolExecutor(5) as ex: list(ex.map(chunk,range(N)))
    print("coinbase FINISHED",flush=True)
if __name__=="__main__":
    th=[threading.Thread(target=f) for f in (binance,coinbase,kalshi)]
    for t in th: t.start()
    for t in th: t.join()
    print("ALL FINISHED",flush=True)
