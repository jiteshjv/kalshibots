import json,subprocess,time,datetime as dt,os,sys
from concurrent.futures import ThreadPoolExecutor
m=json.load(open("settled.json")); m.sort(key=lambda x:x["close_time"])
cut=m[-1]["close_time"]
last=dt.datetime.fromisoformat(cut.replace("Z","+00:00"))-dt.timedelta(days=70)
m=[x for x in m[::-1] if dt.datetime.fromisoformat(x["close_time"].replace("Z","+00:00"))>last]
def ts(s): return dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()
import threading
_lk=threading.Lock();_last=[0.0]
def call(u):
    for i in range(8):
        with _lk:
            w=_last[0]+0.11-time.time()
            if w>0: time.sleep(w)
            _last[0]=time.time()
        try:
            p=subprocess.run(["curl","-s","-w","\n%{http_code}",u],capture_output=True,text=True,timeout=30).stdout
            body,code=p.rsplit("\n",1)
            if code=="200": return json.loads(body)
        except Exception: pass
        time.sleep(1.0)
    return None
def get(x):
    fn=f"ktr/{x['ticker']}.json"
    if os.path.exists(fn): return 1
    agg={}; cur=""
    while True:
        d=call(f"https://api.elections.kalshi.com/trade-api/v2/markets/trades?ticker={x['ticker']}&limit=1000"+(f"&cursor={cur}" if cur else ""))
        if d is None: return 0
        for t in d["trades"]:
            s=int(ts(t["created_time"])); side=t["taker_side"]; q=float(t["count_fp"])
            px=float(t["yes_price_dollars"]) if side=="yes" else float(t["no_price_dollars"])
            a=agg.setdefault(s,{"y":[9,0,0],"n":[9,0,0]})[side[0]]
            a[0]=min(a[0],px); a[1]+=q; a[2]+=q*px
        cur=d.get("cursor")
        if not cur or not d["trades"]: break
    json.dump(agg,open(fn+".tmp","w")); os.replace(fn+".tmp",fn); return 1
with ThreadPoolExecutor(3) as ex:
    done=0
    for r in ex.map(get,m):
        done+=r
        if done%10==0: print("done",done,"/",len(m),flush=True)
print("FINISHED",done,"/",len(m),flush=True)
