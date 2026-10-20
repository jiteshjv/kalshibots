# Polymarket "BTC Up or Down 15m" markets (slug btc-updown-15m-<start_ts>) into data_pm/: market metadata + every trade.
# Public APIs, no key: gamma-api (market by slug), data-api (trades by conditionId). Resumes per market.
# Resolution: Chainlink BTC/USD 60 s TWAP of the window >= price at its start -> "Up".
# Run from data_pm/: python3 -P -u ../scripts/pm_dl.py 2026-08-10 2026-10-20
import json,os,sys,ssl,time,datetime as dt,threading,urllib.request,urllib.parse
from concurrent.futures import ThreadPoolExecutor
CTX=ssl.create_default_context(cafile="/etc/ssl/cert.pem")
A=int(dt.datetime.fromisoformat(sys.argv[1]).replace(tzinfo=dt.timezone.utc).timestamp()); B=int(dt.datetime.fromisoformat(sys.argv[2]).replace(tzinfo=dt.timezone.utc).timestamp())
os.makedirs("mk",exist_ok=True); os.makedirs("tr",exist_ok=True)
lk=threading.Lock(); last=[0.0]
def log(*a): print(dt.datetime.now(dt.timezone.utc).strftime("%FT%TZ"),*a,flush=True)
def get(url,gap=0.12):
    for i in range(8):
        with lk:
            w=last[0]+gap-time.time()
            if w>0: time.sleep(w)
            last[0]=time.time()
        try: return json.load(urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"research"}),context=CTX,timeout=30))
        except urllib.error.HTTPError as e:
            if e.code==404: return None
            time.sleep(1+2*i)
        except Exception: time.sleep(1+2*i)
    return None
def one(t0):
    fm,ft=f"mk/{t0}.json",f"tr/{t0}.json"
    if os.path.exists(ft): return "have"
    ev=get(f"https://gamma-api.polymarket.com/events?slug=btc-updown-15m-{t0}")
    if not ev: json.dump({"missing":True},open(fm,"w")); json.dump([],open(ft,"w")); return "none"
    e=ev[0]; m=e["markets"][0]
    meta=dict(t0=t0,slug=e.get("slug"),title=e.get("title"),conditionId=m.get("conditionId"),tokens=json.loads(m.get("clobTokenIds") or "[]"),
              outcomes=json.loads(m.get("outcomes") or "[]"),outcomePrices=json.loads(m.get("outcomePrices") or "[]"),volume=float(m.get("volume") or 0),
              closed=m.get("closed"),umaStatus=m.get("umaResolutionStatuses"),endDate=m.get("endDate"),eventStart=e.get("startTime") or m.get("eventStartTime"))
    rows=[]; off=0
    while True:
        r=get(f"https://data-api.polymarket.com/trades?market={meta['conditionId']}&limit=500&offset={off}&takerOnly=false")
        if not r: break
        rows+= [[x.get("timestamp"),x.get("outcome"),x.get("side"),float(x.get("price") or 0),float(x.get("size") or 0),x.get("transactionHash")] for x in r]
        if len(r)<500 or off>=10000: break
        off+=500
    json.dump(meta,open(fm,"w")); json.dump(rows,open(ft,"w")); return "ok"
ts=list(range(A-A%900,B,900)); done=0
with ThreadPoolExecutor(4) as ex:
    for r in ex.map(one,ts):
        done+=1
        if done%200==0: log("polymarket markets",done,"/",len(ts))
log("FINISHED",done,"/",len(ts))
