# Poll free live gold quotes (Swissquote mid, gold-api.com) every 2 s and append to data_goldlive/quotes.csv (ts,src,price).
import json,time,urllib.request,ssl,sys
try:
    import certifi; CTX=ssl.create_default_context(cafile=certifi.where())
except Exception: CTX=ssl.create_default_context()
def get(u):
    with urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"}),context=CTX,timeout=5) as r: return json.load(r)
def sq():
    d=get("https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD")
    p=[x for x in d[0]["spreadProfilePrices"] if x["spreadProfile"]=="elite"][0]; return (p["bid"]+p["ask"])/2
def ga(): return float(get("https://api.gold-api.com/price/XAU")["price"])
end=time.time()+float(sys.argv[1] if len(sys.argv)>1 else 7200)
with open("data_goldlive/quotes.csv","a") as f:
    while time.time()<end:
        t=time.time()
        for n,fn in (("swissquote",sq),("goldapi",ga)):
            try: f.write(f"{t:.1f},{n},{fn():.3f}\n")
            except Exception as e: f.write(f"{t:.1f},{n}_err,0\n")
        f.flush(); time.sleep(max(0,2-(time.time()-t)))
