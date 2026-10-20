# Gold sheet data: Version A (mixed*) / C (equal*) x no filters / gold filters (_f) / + news windows (_n) from results/<prefix>_{A,C}.
# python3 -P scripts/build_gold_sheet.py <out_dir> [prefix=gn2]
import pandas as pd,json,sys,math,datetime as dt
from zoneinfo import ZoneInfo
OUT=sys.argv[1]; PRE=sys.argv[2] if len(sys.argv)>2 else "gn2"; PT=ZoneInfo("America/Los_Angeles"); ET=ZoneInfo("America/New_York")
sec=lambda s:int(s.split(":")[0])*60+int(s.split(":")[1])
def clean(o):
    if isinstance(o,float): return None if math.isnan(o) else round(o,4)
    if isinstance(o,list): return [clean(x) for x in o]
    if isinstance(o,dict): return {k:clean(v) for k,v in o.items()}
    return o
C=pd.read_csv("data_news/calendar.csv"); NEWS=set()
for d,e,t in zip(C.date,C.event,C.time_et):
    rel=int(dt.datetime.fromisoformat(f"{d}T{t}").replace(tzinfo=ET).timestamp())
    if e in ("jobs","cpi","claims"): NEWS|={rel+900*k for k in (-1,0,1,2,3)}       # 5:15 - 6:30 AM PT
    if e=="fomc": NEWS|={rel+900*k for k in (0,1,2,3)}                               # 11:00 AM - 12:00 PM PT
for out,v in (("mixed","A"),("equal","C")):
    src=f"{PRE}_{v}"; M=pd.read_csv(f"results/{src}_markets.csv"); E=pd.read_csv(f"results/{src}_entries.csv")
    F=pd.read_csv(f"results/gold_fail_features_{src}.csv").set_index("ticker")
    M["o"]=pd.to_datetime(M.day+" 2026 "+M.window_pt,format="%a %b %d %Y %I:%M %p"); M=M.sort_values("o")
    M["ts"]=M.o.dt.tz_localize(PT).astype("int64")//10**9
    M["d"]=pd.to_datetime(M.day+" 2026",format="%a %b %d %Y").dt.strftime("%Y-%m-%d"); E["side_s"]=E.side.str[0]
    caps={};held={};peak={}
    for k,g in E.groupby("ticker"):
        ev=sorted([(sec(a),n*b,n) for a,n,b in zip(g.entry_time,g.n,g.buy)]+[(sec(a),-n*b,-n) for a,n,b in zip(g.exit_time,g.n,g.buy)],key=lambda z:(z[0],z[1]))
        run=mx=0.0; rn=pk=0
        for _,c,q in ev: run+=c; mx=max(mx,run); rn+=q; pk=max(pk,rn)
        caps[k]=round(mx,2); peak[k]=int(pk); held[k]=max(map(sec,g.exit_time))-min(map(sec,g.entry_time))
    M["cap"]=M.ticker.map(caps).fillna(0); M["held_s"]=M.ticker.map(held).fillna(0); M["peak_n"]=M.ticker.map(peak).fillna(0).astype(int)
    ent={k:g[["entry_no","entry_time","side_s","n","moneyness_pct","threshold_pct","buy","stop","exit","exit_time","exit_px","pnl"]].values.tolist() for k,g in E.groupby("ticker")}
    def why(r,mode):
        if mode=="": return None
        t=r["ticker"]
        if t in F.index and F.loc[t,"sw2d"]==1: return "2-day+ sweep"
        if 7<=r["o"].hour<9: return "7–9 AM PT"
        if mode=="_n" and r["ts"] in NEWS: return "news window"
        return None
    for mode in ("","_f","_n"):
        m=[];e={}
        for r in M.to_dict("records"):
            w=why(r,mode); r=dict(r); r.pop("o"); r.pop("ts"); r["skip"]=0
            if w: r.update(skip=1,side="skipped: "+w,pnl=0,entries=0,contracts=0,stopped=0,sold995=0,settled=0,cap=0,held_s=0,peak_n=0)
            else: e[r["ticker"]]=ent.get(r["ticker"],[])
            m.append(r)
        json.dump(clean({"m":m,"e":e}),open(f"{OUT}/{out}{mode}.json","w"),separators=(",",":"),allow_nan=False)
        T=[r for r in m if not r["skip"]]; import numpy as np; c=np.cumsum([r["pnl"] for r in T])
        print(f"{out}{mode:3s} traded {len(T)}, skipped {len(m)-len(T)}, total ${c[-1]:+,.0f}, max DD ${(c-np.maximum.accumulate(c)).min():,.0f}")
