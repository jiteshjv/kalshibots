# Poll Hyperliquid xyz:GOLD oracle + mark price every 2 s -> data_goldlive/quotes.csv (ts,src,price). Free, no key.
import json,time,urllib.request,ssl,sys
try:
    import certifi; CTX=ssl.create_default_context(cafile=certifi.where())
except Exception: CTX=ssl.create_default_context()
B=json.dumps({"type":"metaAndAssetCtxs","dex":"xyz"}).encode()
end=time.time()+float(sys.argv[1] if len(sys.argv)>1 else 7200)
with open("data_goldlive/quotes.csv","a") as f:
    while time.time()<end:
        t=time.time()
        try:
            r=urllib.request.Request("https://api.hyperliquid.xyz/info",data=B,headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(r,context=CTX,timeout=5) as h: m,c=json.load(h)
            x=c[[a["name"] for a in m["universe"]].index("xyz:GOLD")]
            f.write(f"{t:.1f},hl_oracle,{float(x['oraclePx']):.3f}\n{t:.1f},hl_mark,{float(x['markPx']):.3f}\n")
        except Exception: f.write(f"{t:.1f},hl_err,0\n")
        f.flush(); time.sleep(max(0,2-(time.time()-t)))
