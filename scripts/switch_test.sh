# Exercise Kill -> Rearm -> Disarm -> Rearm(refused) -> Kill -> Rearm against the running paper bot.
T=$(cat ~/kalshibot-data/dashboard_token.txt); B=http://127.0.0.1:8787
st(){ curl -s $B/dashboard.json | python3 -c "import json,sys;print('   state:',json.load(sys.stdin)['meta'].get('state'))"; }
post(){ echo "$1:"; curl -s -X POST -H "Authorization: Bearer $T" $B/$2; echo; sleep 4; st; }
echo "start:"; st
post KILL kill; post REARM rearm; post DISARM disarm; post "REARM while disarming" rearm; post "KILL while disarming" kill; post REARM rearm
echo "switch files left:"; ls ~/kalshibot-data | grep -E "^(KILL|DISARM)$" || echo "   none"
