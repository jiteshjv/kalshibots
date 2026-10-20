# BTC 1 s klines (Binance public archive) Oct 2024 - Nov 2025 for the synthetic (price-only) backtest -> data_synth/bn/
cd /Users/jvidhani/trading/research/kalshi_btc/data_synth/bn
for m in 2024-10 2024-11 2024-12 2025-01 2025-02 2025-03 2025-04 2025-05 2025-06 2025-07 2025-08 2025-09 2025-10 2025-11; do
  f=BTCUSDT-1s-$m.zip; [ -s $f ] && continue
  curl -s -f --retry 5 -o $f.tmp https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1s/$f && mv $f.tmp $f && echo "$(date -u +%T) $f $(du -h $f | cut -f1)"
done; echo FINISHED
