# The workbench's images, one target each; the build context is the repository root.
#   web     Caddy with the built single-page app: TLS, static files, proxy to the app
#   app     the FastAPI app (agent, runs, SQLite under /data) and echolot-admin
#   runner  the Prefect flow that runs scripts, next to the services (docs/runner-host.md)
# deploy/docker-compose.yml builds all three.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ghcr.io/astral-sh/uv:0.9.5 AS uv

# --- web ---------------------------------------------------------------------------------------

FROM node:22-alpine AS web-build
# pnpm comes in the version package.json names; no prompt before corepack downloads it
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable
WORKDIR /src
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
RUN pnpm install --frozen-lockfile
COPY apps/web apps/web
RUN pnpm -C apps/web build

FROM caddy:2-alpine AS web
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=web-build /src/apps/web/dist /srv

# --- python images: dependencies first (cached), then the workspace's own packages --------------

FROM ${PYTHON_IMAGE} AS python-base
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=uv /uv /bin/uv
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin echolot
WORKDIR /src
COPY pyproject.toml uv.lock .python-version ./
COPY apps/server/pyproject.toml apps/server/
COPY runner/pyproject.toml runner/

FROM python-base AS app
# the app and the runner package (for ECHO_EXECUTOR=local); no Prefect, no dev tools
RUN uv sync --frozen --no-dev --no-install-workspace
COPY apps/server apps/server
COPY runner runner
RUN uv sync --frozen --no-dev --no-editable
COPY services /app/services
# a new named volume on /data takes this ownership
RUN mkdir /data && chown echolot:echolot /data
ENV PATH=/opt/venv/bin:$PATH \
    ECHO_DATA_DIR=/data \
    ECHO_SERVICES_DIR=/app/services
USER echolot
WORKDIR /app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
# one process: sign-in throttling, the run poller and the agent's streams live in it (PLAN 3)
CMD ["uvicorn", "echolot_app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python-base AS runner
# the runner and the Prefect version of uv.lock, which must match the Prefect server's
RUN uv sync --frozen --no-dev --no-install-workspace --package echolot-runner --extra flow
COPY runner runner
RUN uv sync --frozen --no-dev --no-editable --package echolot-runner --extra flow
ENV PATH=/opt/venv/bin:$PATH
USER echolot
WORKDIR /home/echolot
CMD ["python", "-m", "echolot_runner.serve"]
