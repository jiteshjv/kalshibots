import json,subprocess,time
out=[];cur=""
for i in range(40):
    u="https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXBTC15M&status=settled&limit=1000"+(f"&cursor={cur}" if cur else "")
    d=json.loads(subprocess.run(["curl","-s",u],capture_output=True,text=True).stdout)
    out+=[{k:m.get(k) for k in ("ticker","close_time","floor_strike","expiration_value","result","volume_fp")} for m in d["markets"]]
    cur=d.get("cursor")
    if not cur or not d["markets"]: break
    time.sleep(0.2)
json.dump(out,open("settled.json","w"));print(len(out),out[-1]["close_time"],out[0]["close_time"])
