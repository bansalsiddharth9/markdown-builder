# One image for the `web` and `worker` services (design: Deployment).
# Build from the repository root:  docker compose -f deploy/docker-compose.yml build
FROM python:3.12.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PLAYWRIGHT_BROWSERS_PATH=/app/.playwright

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# Pandoc 3.14 (GPL-2.0, run as a separate program).
RUN curl -fsSL -o /tmp/pandoc.deb \
      https://github.com/jgm/pandoc/releases/download/3.14/pandoc-3.14-1-amd64.deb \
 && apt-get install -y --no-install-recommends /tmp/pandoc.deb \
 && rm -f /tmp/pandoc.deb

# uv, the same version as in development; all Python versions come from uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
RUN uv sync --frozen --all-packages --no-dev --no-editable
ENV PATH=/app/.venv/bin:$PATH

# Chromium for render: browser pages, with its system libraries.
RUN playwright install --with-deps chromium

COPY config ./config
COPY deploy/specweb.yaml ./deploy/specweb.yaml
# The pinned Docling models (config: pdf.models_path = ../models -> /app/models).
RUN specconv download-models --config config/specconv.yaml

RUN useradd --create-home --uid 1000 specweb && mkdir -p /data && chown specweb /data
USER specweb
ENV SPECWEB_CONFIG=/app/deploy/specweb.yaml \
    SPECWEB_DATA=/data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1
CMD ["specweb", "serve", "--host", "0.0.0.0", "--port", "8000", "--forwarded-allow-ips", "*"]
