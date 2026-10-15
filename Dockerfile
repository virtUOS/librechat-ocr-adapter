FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /uploads \
    && chown app:app /uploads

WORKDIR /app
# Dependencies are pinned from uv.lock (regenerate with `uv export --frozen --no-dev
# --no-hashes --no-emit-project -o requirements.txt` after `uv lock` changes).
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps .

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "--factory", "ocr_adapter.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
