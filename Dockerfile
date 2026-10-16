# uv comes from its own image; a FROM line so Dependabot's docker ecosystem bumps it.
FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /uploads \
    && chown app:app /uploads

WORKDIR /app
COPY --from=uv /uv /bin/uv
# Dependencies first (this layer is cached while uv.lock is unchanged), the
# project after its source. Versions are exactly those in uv.lock.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-editable --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable \
    && rm /bin/uv

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "--factory", "ocr_adapter.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
