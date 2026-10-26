# Rota in a container, for platforms that run images (Fly.io, Render, Railway, a VPS with Docker).
# The platform terminates TLS and proxies to port 8780. Keep /data on a persistent volume: it holds
# the whole studio (SQLite) and the secret that signs clients' booking links.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    ROTA_HOST=0.0.0.0 ROTA_PORT=8780 ROTA_DB=/data/rota.db ROTA_TRUST_PROXY=1

RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/* \
 && useradd --system --home /app --shell /usr/sbin/nologin rota && mkdir -p /data && chown rota:rota /data

WORKDIR /app
COPY --chown=rota:rota . /app
USER rota
VOLUME ["/data"]
EXPOSE 8780

HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8780/api/health', timeout=2).status == 200 else 1)"
CMD ["python", "server.py"]
