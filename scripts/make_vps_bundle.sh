# Build kalshibot-vps.tar.gz: everything a VPS needs (no keys, no data). Copy it to the server with scp.
cd /Users/jvidhani/trading/research/kalshi_btc
B=$(mktemp -d)/kalshibot; mkdir -p $B/src $B/data $B/secrets $B/docs
cp -R src/kalshibot $B/src/; find $B/src -name __pycache__ -prune -exec rm -rf {} +
cp Dockerfile docker-compose.yml docs/SETUP.md $B/; cp docs/SETUP.md $B/docs/
cp deploy/config.vps.yaml $B/config.yaml; cp data_news/calendar.csv $B/data/news_calendar.csv
printf 'KALSHI_API_KEY_ID=\nKALSHIBOT_DASH_TOKEN=\nTELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=\n' > $B/.env.example
tar -C $(dirname $B) -czf kalshibot-vps.tar.gz kalshibot && echo "built $(pwd)/kalshibot-vps.tar.gz ($(du -h kalshibot-vps.tar.gz | cut -f1))"
tar -tzf kalshibot-vps.tar.gz | grep -E "\.pem$|/\.env$|journal\.sqlite|history_bars" && echo "WARNING: secrets/data in bundle" || echo "no secrets or data in the bundle"
