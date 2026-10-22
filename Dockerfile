# Build the web workspace. Node is only needed here, not in the runtime image.
FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# PDF/DOCX renderer (ADR 0007): the same package on the official Playwright image, which
# ships Chromium and the Liberation fonts. Its version must match the pinned playwright.
# Not the default target: build it with --target renderer (Compose does).
FROM mcr.microsoft.com/playwright/python:v1.55.0-noble AS renderer

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    NEXT_ROLE_PRINT_BUNDLE_DIR=/app/print \
    HOME=/tmp

COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --group renderer --no-install-project --python /usr/bin/python3
COPY src ./src
RUN uv sync --frozen --no-dev --group renderer --python /usr/bin/python3
COPY --from=frontend /frontend/dist ./print

# Same uid as the API, which reads the files (mode 0640) from the shared volume.
RUN useradd --system --uid 10001 app \
    && mkdir -p /var/lib/next-role/artifacts \
    && chown 10001 /var/lib/next-role/artifacts
USER app
CMD ["python", "-m", "next_role.renderer"]

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev
COPY --from=frontend /frontend/dist ./static
ENV NEXT_ROLE_STATIC_DIR=/app/static

RUN useradd --system --uid 10001 app \
    && mkdir -p /var/lib/next-role/artifacts \
    && chown 10001 /var/lib/next-role/artifacts
USER app

EXPOSE 8000
CMD ["uvicorn", "next_role.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
