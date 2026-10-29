# Rota in a container, for platforms that run images (Fly.io, Render, Railway, a VPS with Docker).
# The platform terminates TLS (for your domain and *.your-domain) and proxies to port 8780.
# Keep /data on a persistent volume: it holds every studio's records (one SQLite file each).
#
#   docker build -t rota .
#   docker run -d -p 127.0.0.1:8780:8780 -v rota-data:/data \
#     -e ROTA_PUBLIC_URL=https://rota.am -e ROTA_SECRET=... rota
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    ROTA_HOST=0.0.0.0 ROTA_PORT=8780 ROTA_DATA=/data ROTA_TRUST_PROXY=1

RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/* \
 && useradd --system --home /app --shell /usr/sbin/nologin rota && mkdir -p /data && chown rota:rota /data

WORKDIR /app
COPY --chown=root:rota server.py manage.py /app/
COPY --chown=root:rota rota /app/rota
COPY --chown=root:rota web /app/web
USER rota
VOLUME ["/data"]
EXPOSE 8780

HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request as u, sys; sys.exit(0 if u.build_opener(u.ProxyHandler({})).open('http://127.0.0.1:8780/api/health', timeout=2).status == 200 else 1)"
CMD ["python", "server.py"]
