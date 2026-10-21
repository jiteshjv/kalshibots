# Build kalshibot-install.zip: code + installer + instructions for a Windows/Mac/Linux machine (no keys, no data).
cd /Users/jvidhani/trading/research/kalshi_btc
T=$(mktemp -d); B=$T/kalshibot-install; mkdir -p $B/app/src
cp -R src/kalshibot $B/app/src/; find $B -name __pycache__ -prune -exec rm -rf {} +
cp deploy/run.py $B/app/run.py; cp deploy/kb.py $B/app/kb.py; cp deploy/install.py deploy/INSTALL.md deploy/CLAUDE.md $B/
cp data_news/calendar.csv $B/news_calendar.csv
(cd $T && rm -f /Users/jvidhani/trading/research/kalshi_btc/kalshibot-install.zip && zip -qr /Users/jvidhani/trading/research/kalshi_btc/kalshibot-install.zip kalshibot-install)
echo "built $(pwd)/kalshibot-install.zip ($(du -h kalshibot-install.zip | cut -f1))"
unzip -l kalshibot-install.zip | grep -E "\.pem|\.env|journal\.sqlite|history_bars|kalshi\.key" && echo "WARNING: secrets/data in bundle" || echo "no secrets or data in the bundle"
