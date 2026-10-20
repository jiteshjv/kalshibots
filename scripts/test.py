import json,math
from collections import defaultdict
m=json.load(open("settled.json"))
m=[x for x in m if x["floor_strike"] and x["expiration_value"] and x["result"] in("yes","no")]
m.sort(key=lambda x:x["close_time"])
r=[float(str(x["expiration_value"]).replace(",",""))/x["floor_strike"]-1 for x in m]
y=[x["result"]=="yes" for x in m]
n=len(y); print("markets",n,m[0]["close_time"][:10],"->",m[-1]["close_time"][:10])
print("base rate Yes %.3f"%(sum(y)/n))
def rep(name,preds,lo=0,hi=None):
    hi=hi or n
    s=[(p==y[i]) for i,p in preds if p is not None and lo<=i<hi]
    k=len(s); h=sum(s)/k if k else 0; se=math.sqrt(.25/k) if k else 0
    return f"{name:38s} n={k:5d} hit={h:.3f} (±{1.96*se:.3f})"
H=n//2
sigs={}
sigs["momentum: same as last"]=[(i,y[i-1]) for i in range(1,n)]
sigs["momentum: sign of last 4 (1h)"]=[(i,sum(r[i-4:i])>0) for i in range(4,n)]
sigs["momentum: sign of last 16 (4h)"]=[(i,sum(r[i-16:i])>0) for i in range(16,n)]
sigs["streak>=3 continues"]=[(i,y[i-1]) for i in range(3,n) if y[i-1]==y[i-2]==y[i-3]]
sigs["streak>=3 reverses"]=[(i,not y[i-1]) for i in range(3,n) if y[i-1]==y[i-2]==y[i-3]]
import statistics as st
sd=st.pstdev(r)
sigs["big last move (>2sd) reverses"]=[(i,r[i-1]<0) for i in range(1,n) if abs(r[i-1])>2*sd]
sigs["big last move (>2sd) continues"]=[(i,r[i-1]>0) for i in range(1,n) if abs(r[i-1])>2*sd]
for k,v in sigs.items():
    print(rep(k,v)); print("   1st half:",rep("",v,0,H)[39:],"| 2nd half:",rep("",v,H,n)[39:])
# hour of day
hb=defaultdict(list)
for i,x in enumerate(m): hb[int(x["close_time"][11:13])].append(y[i])
print("\nYes rate by UTC hour (close):")
print(" ".join(f"{h:02d}:{sum(v)/len(v):.2f}" for h,v in sorted(hb.items())))
print("15m move sd: %.3f%%  (~$%.0f at 82k)"%(sd*100,sd*82000))
