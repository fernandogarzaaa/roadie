# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    FORWARDED_ALLOW_IPS=127.0.0.1

WORKDIR /srv
COPY pyproject.toml README.md LICENSE ./
COPY app ./app
RUN pip install ".[tracing]" && useradd --create-home --uid 10001 roadie
USER roadie

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD sh -c 'python -c "import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=4)" "http://127.0.0.1:${PORT:-8000}/health"'

# Railway and Render inject $PORT; it defaults to 8000 locally.
# X-Forwarded-For is only trusted from the addresses in FORWARDED_ALLOW_IPS
# (default 127.0.0.1). The per-IP rate limiter keys on the client IP, so
# trusting the header from everyone would let clients spoof it. Behind a
# platform proxy where the container is NOT directly reachable (Render,
# Railway), set FORWARDED_ALLOW_IPS to the proxy range, or to '*' only if
# nothing else can reach the port.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
