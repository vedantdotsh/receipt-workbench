FROM ghcr.io/astral-sh/uv:0.12.17 AS uv
FROM python:3.13-slim-bookworm AS build

COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project --no-editable --no-build
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13-slim-bookworm

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    WEB_CONCURRENCY=1 HOME=/tmp TMPDIR=/tmp
WORKDIR /app
RUN groupadd --gid 10001 workbench && useradd --uid 10001 --gid workbench --no-create-home workbench
COPY --from=build /app/.venv /app/.venv
USER 10001:10001
EXPOSE 8321
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; response = urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8321/static/index.html', method='HEAD'), timeout=3); raise SystemExit(response.status != 200)"]
CMD ["receipt-workbench", "serve", "--public", "--host", "0.0.0.0", "--port", "8321"]
