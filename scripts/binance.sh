cd "$(dirname "$0")"; mkdir -p bn
for i in 0 1 2 3 4 5 6 7 8; do d=$(date -u -v-${i}d +%Y-%m-%d); f=bn/BTCUSDT-1s-$d.zip
 curl -s -f -o $f https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1s/BTCUSDT-1s-$d.zip && echo "got $d" || { rm -f $f; echo "missing $d"; }; done
