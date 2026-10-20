# Today's BTC paper stops vs the recorded order book (~/kalshibot-data/rec). For each stop event (lots stopped in one market):
# our side's best bid + size and bids within 5c at the trigger second, the bot's average fill, our side's bid 10/30/60 s later,
# low/high afterwards, and what holding to settlement would have paid.  python3 -P scripts/today_stops.py [YYYY-MM-DD]
import gzip,json,glob,sqlite3,sys,datetime as dt
from zoneinfo import ZoneInfo
PT=ZoneInfo("America/Los_Angeles"); D=sys.argv[1] if len(sys.argv)>1 else dt.date.today().isoformat()
db=sqlite3.connect("/Users/jvidhani/kalshibot-data/journal.sqlite")
day0=int(dt.datetime.fromisoformat(D).replace(tzinfo=PT).timestamp())
ev=db.execute("""select l.ticker,m.open_ts,m.result,l.side,sum(l.qty),sum(l.exit_px*l.qty)/sum(l.qty),min(l.exit_ts),max(l.exit_ts),
  sum(l.buy*l.qty)/sum(l.qty),min(l.stop),max(l.stop) from lots l join markets m using(ticker)
  where l.exit='stop' and m.open_ts>=? and m.open_ts<? group by l.ticker, cast(l.exit_ts as int) order by min(l.exit_ts)""",(day0,day0+86400)).fetchall()
want={e[0] for e in ev}; book={}
for f in sorted(glob.glob(f"/Users/jvidhani/kalshibot-data/rec/{D}/*.jsonl.gz")+glob.glob(f"/Users/jvidhani/kalshibot-data/rec/{(dt.date.fromisoformat(D)+dt.timedelta(1)).isoformat()}/*.jsonl.gz")):
    try:
        with gzip.open(f,"rt") as h:
            for l in h:
                try: r=json.loads(l)
                except Exception: break
                if r["kind"]=="book" and r["d"]["t"] in want: book.setdefault(r["d"]["t"],[]).append((r["ts"],r["d"]))
    except EOFError: pass
for v in book.values(): v.sort(key=lambda x:x[0])
def at(tk,t,s):                          # first snapshot at or after t (the book the stop actually sold into)
    x=None
    for ts,d in book.get(tk,[]):
        if ts>=t: x=d; break
    if x is None or not x[s]: return None,0,0
    b=x[s][0]; return b[0],b[1],sum(q for p,q in x[s] if p>=b[0]-0.05)
def path(tk,a,b,s):
    v=[d[s][0][0] for ts,d in book.get(tk,[]) if a<=ts<b and d[s]]
    return (min(v),max(v)) if v else (None,None)
c=lambda x:f"{x*100:3.0f}c" if x is not None else "  - "
print(f"{'market (PT)':10s} {'side':4s} {'qty':>5s} {'stops':>9s} {'stop at':>7s} | {'bid 1s before->after x size':>23s} {'<=5c':>6s} | {'fill':>5s} | {'+10s':>4s} {'+30s':>4s} {'+60s':>4s} | {'low':>4s} {'high':>4s} after | result -> held vs sold")
tot_s=tot_h=0
for tk,o,res,side,q,fill,t0,t1,buy,s0,s1 in ev:
    s="y" if side=="y" else "n"; b,sz,dep=at(tk,t0,s)
    after=[at(tk,t0+k,s)[0] for k in (10,30,60)]; lo,hi=path(tk,t0+1,o+900,s)
    pre=None
    for ts,d in book.get(tk,[]):
        if ts>=t0-1: break
        pre=d[s][0][0] if d[s] else None
    won=(res=="YES")==(s=="y"); held=q*(1.0 if won else 0.0); sold=q*fill
    tot_s+=sold; tot_h+=held; m=int(t0-o)
    print(f"{dt.datetime.fromtimestamp(o,PT):%I:%M %p} {('YES' if s=='y' else 'NO'):4s} {q:5.0f} {s0*100:3.0f}-{s1*100:3.0f}c {m//60:4d}:{m%60:02d} | {c(pre)}->{c(b)} x{sz:6,.0f} {dep:6,.0f} | {fill*100:4.1f}c | "
          f"{' '.join(c(x) for x in after)} | {c(lo)} {c(hi)}       | {res:3s} {'won ' if won else 'lost'} -> {'held' if won else 'sold'} better by ${abs(held-sold):,.0f}")
print(f"\nAll stops today: sold for ${tot_s:,.0f}; holding to settlement would have paid ${tot_h:,.0f} -> stops {'saved' if tot_s>tot_h else 'cost'} ${abs(tot_s-tot_h):,.0f}")
