import json,subprocess,time,datetime as dt,os
START=int(dt.datetime(2026,8,9,23,0,tzinfo=dt.timezone.utc).timestamp())
END=int(time.time())
import glob
done=sorted(f for f in glob.glob("kr/*.json"))
if done:
    last=done[-1][3:13]; START=int(dt.datetime.fromisoformat(last).replace(tzinfo=dt.timezone.utc).timestamp())+86400; print("resuming after",last,flush=True)
since=START*10**9; agg={}; day=None; n=0
def flush(d):
    if d: json.dump(agg,open(f"kr/{d}.json.tmp","w")); os.replace(f"kr/{d}.json.tmp",f"kr/{d}.json")
while True:
    try:
        r=json.loads(subprocess.run(["curl","-s",f"https://api.kraken.com/0/public/Trades?pair=XBTUSD&count=1000&since={since}"],capture_output=True,text=True,timeout=30).stdout)
    except Exception: time.sleep(3); continue
    if r.get("error"): time.sleep(5); continue
    res=r["result"]; k=[x for x in res if x!="last"][0]; T=res[k]
    for p,q,t,*_ in T:
        s=int(t); d=dt.datetime.fromtimestamp(s,dt.timezone.utc).strftime("%Y-%m-%d")
        if d!=day: flush(day); day=d; agg={}; print("kraken day",d,flush=True)
        p=float(p);q=float(q); e=agg.setdefault(s,[0.0,0.0,p]); e[0]+=p*q; e[1]+=q
    since=int(res["last"]); n+=1
    if not T or since//10**9>=END: break
    time.sleep(1.05)
flush(day); print("FINISHED",n,"calls",flush=True)
