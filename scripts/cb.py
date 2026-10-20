import json,subprocess,time,datetime as dt,os,threading
from concurrent.futures import ThreadPoolExecutor
START=int(dt.datetime(2026,8,9,23,0,tzinfo=dt.timezone.utc).timestamp())
lk=threading.Lock(); last=[0.0]
def get(u):
    for i in range(10):
        with lk:
            w=last[0]+0.16-time.time()
            if w>0: time.sleep(w)
            last[0]=time.time()
        try:
            r=subprocess.run(["curl","-s","-w","\n%{http_code}",u],capture_output=True,text=True,timeout=30).stdout
            b,c=r.rsplit("\n",1)
            if c=="200": return json.loads(b)
        except Exception: pass
        time.sleep(1+i)
def tsof(s): return dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()
PLAN="cb/_plan.json"
if os.path.exists(PLAN):
    lo,top=json.load(open(PLAN)); print("resuming plan",lo,top,flush=True)
else:
  top=get("https://api.exchange.coinbase.com/products/BTC-USD/trades?limit=1")[0]["trade_id"]+1
  lo,hi=top-40_000_000,top
  while hi-lo>1000:   # find first trade id at/after START
      mid=(lo+hi)//2; t=get(f"https://api.exchange.coinbase.com/products/BTC-USD/trades?limit=1&after={mid}")[0]
      if tsof(t["time"])<START: lo=mid
      else: hi=mid
  json.dump([lo,top],open(PLAN,"w"))
print("start id",lo,"top",top,"trades",top-lo,flush=True)
N=48; step=(top-lo)//N+1
def chunk(k):
    a=lo+k*step; b=min(lo+(k+1)*step,top); fn=f"cb/{k:03d}.json"
    if os.path.exists(fn): return
    agg={}; cur=b
    while cur>a:
        d=get(f"https://api.exchange.coinbase.com/products/BTC-USD/trades?limit=1000&after={cur}")
        if not d: break
        for t in d:
            if t["trade_id"]<a: continue
            s=int(tsof(t["time"])); p=float(t["price"]); q=float(t["size"])
            e=agg.setdefault(s,[0.0,0.0,p]); e[0]+=p*q; e[1]+=q
        cur=d[-1]["trade_id"]
    json.dump(agg,open(fn+".tmp","w")); os.replace(fn+".tmp",fn); print("chunk",k,"done",flush=True)
with ThreadPoolExecutor(6) as ex: list(ex.map(chunk,range(N)))
print("FINISHED",flush=True)
