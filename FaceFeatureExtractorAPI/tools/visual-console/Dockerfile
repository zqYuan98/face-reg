FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VISUAL_CONSOLE_HOST=0.0.0.0 \
    VISUAL_CONSOLE_PORT=8080

RUN groupadd --system --gid 10001 visualconsole \
    && useradd --system --uid 10001 --gid visualconsole \
        --home-dir /nonexistent --shell /usr/sbin/nologin visualconsole

WORKDIR /app

COPY --chown=visualconsole:visualconsole \
    server.py index.html styles.css core.js app.js ./

USER 10001:10001
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=2).read()"]

STOPSIGNAL SIGTERM
CMD ["python", "/app/server.py"]
