import json,subprocess,time,datetime as dt
start=dt.datetime(2026,8,9,23,0,tzinfo=dt.timezone.utc); end=dt.datetime(2026,10,15,17,0,tzinfo=dt.timezone.utc)
c={}; t=start
while t<end:
    t2=min(t+dt.timedelta(minutes=300),end)
    u=f"https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=60&start={t.isoformat()}&end={t2.isoformat()}"
    for _ in range(3):
        try:
            d=json.loads(subprocess.run(["curl","-s",u],capture_output=True,text=True).stdout); break
        except Exception: time.sleep(1)
    if isinstance(d,list):
        for r in d: c[r[0]]=r[4]
    t=t2; time.sleep(0.12)
json.dump(c,open("cb1m.json","w")); print(len(c))
