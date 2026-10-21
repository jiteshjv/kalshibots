# Start the GOLD kalshibot in paper mode (dashboard http://127.0.0.1:8788); PYTH_API_KEY from ~/kalshibot-gold-data/.env if present
cd /Users/jvidhani/trading/research/kalshi_btc
D=~/kalshibot-gold-data; mkdir -p $D; [ -f $D/news_calendar.csv ] || cp data_news/calendar.csv $D/news_calendar.csv
f=$D/dashboard_token.txt; [ -s $f ] || (umask 077; python3 -c "import secrets;print(secrets.token_urlsafe(18))" > $f)
# stop the running copy first (pid file), so a restart never leaves two bots fighting over the port
if [ -s $D/bot.pid ] && kill -0 $(cat $D/bot.pid) 2>/dev/null; then echo "stopping old bot $(cat $D/bot.pid) (already running)"; kill -TERM $(cat $D/bot.pid); for i in $(seq 30); do kill -0 $(cat $D/bot.pid) 2>/dev/null || break; sleep 1; done; fi
[ -f ~/kalshibot-data/.env ] && set -a && . ~/kalshibot-data/.env && set +a    # Kalshi key (same account as BTC): real-time order book
[ -f $D/.env ] && set -a && . $D/.env && set +a
KALSHIBOT_DASH_TOKEN=$(cat $f) PYTHONPATH=src nohup python3 -P -u -m kalshibot run --config config.gold.paper.yaml >> logs/kalshibot_gold_paper.log 2>&1 &
echo $! > $D/bot.pid; echo "gold bot started pid $(cat $D/bot.pid)"
