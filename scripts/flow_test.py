# Pre-registered check of the features that were consistent in train AND val (rule_search.py section A):
# flow60/flow15 (Kalshi taker flow into our side over last 60s/15s: low = others were SELLING our side), kvol60/kvol_all (low volume), m (bigger lead).
# Thresholds come from train quantiles only. Test period is evaluated here for the first time.
import math,numpy as np,pandas as pd
src=open("../scripts/rule_search.py").read(); exec(src[:src.index("print(\"\\n== Baseline")])
tr=D[D.part=="train"]
q=lambda f,p: float(np.quantile(tr[f].dropna(),p))
rules={
 "her rule (>=0.10%, 65-75c)":lambda d:base(d),
 "+ flow60 in lowest 40%":lambda d:base(d)&(d.flow60<=q("flow60",.4)),
 "+ flow60 in lowest 20%":lambda d:base(d)&(d.flow60<=q("flow60",.2)),
 "+ flow15 in lowest 40%":lambda d:base(d)&(d.flow15<=q("flow15",.4)),
 "+ kvol60 below median":lambda d:base(d)&(d.kvol60<=q("kvol60",.5)),
 "+ lead >= 0.15%":lambda d:(d.m>=0.0015)&(d.e>=0.65)&(d.e<=0.75),
 "+ flow60 low40% & kvol60 < median":lambda d:base(d)&(d.flow60<=q("flow60",.4))&(d.kvol60<=q("kvol60",.5)),
 "+ flow60 low40% & lead >= 0.15%":lambda d:(d.m>=0.0015)&(d.e>=0.65)&(d.e<=0.75)&(d.flow60<=q("flow60",.4)),
 "wider 65-80c + flow60 low40%":lambda d:(d.m>=0.0010)&(d.e>=0.65)&(d.e<=0.80)&(d.flow60<=q("flow60",.4)),
}
for col in ("pnl_opt","pnl_cons","pnl_hold"):
    print(f"\n--- exit: {col} ---")
    for nm,fn in rules.items(): show(nm,evalrule(fn,col))
print(f"\nthresholds (train): flow60 40%={q('flow60',.4):+.3f} 20%={q('flow60',.2):+.3f}  flow15 40%={q('flow15',.4):+.3f}  kvol60 median={q('kvol60',.5):.2f} (log $)")
