# Uma imagem com o hub inteiro: a interface vira arquivos estáticos e a API em
# Python serve os dois no mesmo endereço. Roda em qualquer hospedagem de
# contêiner (Fly.io, Cloud Run, Render, uma VM...).
#
#   podman build -t datahub .
#   podman run --rm -p 8080:8080 datahub      →  http://localhost:8080

# ---- 1. a interface ----
FROM docker.io/library/node:24-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# Endereço vazio: a interface chama /api/... no mesmo servidor que a entregou.
ENV NEXT_PUBLIC_API_URL="" DATAHUB_EXPORTAR=1 NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---- 2. a API, servindo a interface ----
FROM docker.io/library/python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATAHUB_MODO=publico DATAHUB_SESSOES=/tmp/sessoes DATAHUB_WEB=/app/web PORT=8080
WORKDIR /app
COPY requirements-servidor.txt ./
RUN pip install -r requirements-servidor.txt && useradd --create-home --uid 1000 datahub
COPY api ./api
COPY engine ./engine
COPY samples ./samples
COPY --from=web /web/out ./web
USER datahub
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/saude', timeout=4)"
# --proxy-headers: atrás do proxy da hospedagem, o IP do visitante vem nos cabeçalhos.
CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port \"$PORT\" --proxy-headers --forwarded-allow-ips '*' --timeout-keep-alive 30"]
