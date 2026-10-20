# Start kalshibot in paper mode on this Mac (dashboard token from ~/kalshibot-data/dashboard_token.txt); pid in ~/kalshibot-data/bot.pid
cd /Users/jvidhani/trading/research/kalshi_btc
f=~/kalshibot-data/dashboard_token.txt; [ -s $f ] || (umask 077; python3 -c "import secrets;print(secrets.token_urlsafe(18))" > $f); chmod 600 $f
KALSHIBOT_DASH_TOKEN=$(cat $f) PYTHONPATH=src nohup python3 -P -u -m kalshibot run --config config.paper.yaml >> logs/kalshibot_paper.log 2>&1 &
echo $! > ~/kalshibot-data/bot.pid; echo "started pid $(cat ~/kalshibot-data/bot.pid)"
