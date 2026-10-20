# kalshibot: Kalshi KXBTC15M paper/live trader.
#   docker compose up -d --build        (see docs/SETUP.md)
# Volumes: /config (config.yaml, read-only), /data (journal, logs, state, dashboard.json), /secrets (Kalshi key).
FROM python:3.12-slim

# openssl: signs Kalshi API requests; tzdata: Pacific-time display; ca-certificates: HTTPS.
RUN apt-get update \
 && apt-get install -y --no-install-recommends openssl tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Build with `--build-arg WITH_S3=1` to include boto3 (only needed for the research recorder's S3 upload).
ARG WITH_S3=0
RUN pip install --no-cache-dir websockets requests numpy pyyaml \
 && if [ "$WITH_S3" = "1" ]; then pip install --no-cache-dir boto3; fi

WORKDIR /app
COPY src/kalshibot /app/src/kalshibot
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    KALSHIBOT_DATA_DIR=/data \
    KALSHIBOT_DASH_HOST=0.0.0.0 \
    KALSHI_PRIVATE_KEY_PATH=/secrets/kalshi.pem

VOLUME ["/data"]
EXPOSE 8787

HEALTHCHECK --interval=60s --timeout=10s --start-period=180s --retries=3 \
  CMD python -c "import json,urllib.request,sys; sys.exit(0 if json.load(urllib.request.urlopen('http://127.0.0.1:8787/health',timeout=5))['ok'] else 1)"

CMD ["python", "-m", "kalshibot", "run", "--config", "/config/config.yaml"]
