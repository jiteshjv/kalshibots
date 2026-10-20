import json,subprocess,time,datetime as dt
from concurrent.futures import ThreadPoolExecutor
m=json.load(open("settled.json"))
def ts(s): return int(dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
def get(x):
    cl=ts(x["close_time"]); op=cl-900
    u=f"https://api.elections.kalshi.com/trade-api/v2/series/KXBTC15M/markets/{x['ticker']}/candlesticks?start_ts={op}&end_ts={cl}&period_interval=1"
    for i in range(4):
        try:
            d=json.loads(subprocess.run(["curl","-s",u],capture_output=True,text=True,timeout=20).stdout)
            if "candlesticks" in d: return x["ticker"],d["candlesticks"]
        except Exception: pass
        time.sleep(1+i)
    return x["ticker"],None
with ThreadPoolExecutor(8) as ex: res=dict(ex.map(get,m))
json.dump(res,open("kc.json","w"))
print(len(res),"ok",sum(1 for v in res.values() if v),"empty",sum(1 for v in res.values() if v==[]),"fail",sum(1 for v in res.values() if v is None))
