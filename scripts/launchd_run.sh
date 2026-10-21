#!/bin/bash
# launchd entry point (foreground): bash scripts/launchd_run.sh btc|gold|compare
cd /Users/jvidhani/trading/research/kalshi_btc
PY=/Library/Frameworks/Python.framework/Versions/3.12/bin/python3
set -a; [ -f ~/kalshibot-data/.env ] && . ~/kalshibot-data/.env; set +a           # Kalshi key (both bots)
case "$1" in
  btc)  D=~/kalshibot-data; CFG=config.paper.yaml; LOG=logs/kalshibot_paper.log ;;
  demo) D=~/kalshibot-demo-data; CFG=config.demo.yaml; LOG=logs/kalshibot_demo.log ;;
  gold) D=~/kalshibot-gold-data; CFG=config.gold.paper.yaml; LOG=logs/kalshibot_gold_paper.log
        [ -f $D/news_calendar.csv ] || cp data_news/calendar.csv $D/news_calendar.csv ;;
  compare)                                                                         # nightly: yesterday's paper vs backtest
        DAY=$(TZ=America/Los_Angeles date -v-1d +%F)
        exec $PY -P -u scripts/paper_vs_backtest.py "$DAY" --download >> logs/nightly_compare.log 2>&1 ;;
  *) echo "usage: $0 btc|gold|compare"; exit 2 ;;
esac
export KALSHIBOT_DASH_TOKEN=$(cat $D/dashboard_token.txt) PYTHONPATH=src
echo $$ > $D/bot.pid
exec $PY -P -u -m kalshibot run --config $CFG >> $LOG 2>&1
