FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    FACE_ALGO_CONFIG=/app/config/algorithm.yaml

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 facealgo \
    && useradd --system --uid 10001 --gid facealgo --home-dir /nonexistent --shell /usr/sbin/nologin facealgo

WORKDIR /app

COPY requirements-cpu.txt ./requirements-cpu.txt
RUN python -m pip install --requirement requirements-cpu.txt

COPY --chown=facealgo:facealgo . /app
RUN mkdir -p /app/tmp && chown facealgo:facealgo /app/tmp
ENV TMPDIR=/app/tmp

USER facealgo
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3).read()"]

CMD ["python", "-m", "uvicorn", "api.app:create_default_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--no-access-log"]
