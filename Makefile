# ECHOLOT agentic workbench. Compatible with GNU Make 3.81 (macOS default).
SHELL := /bin/bash
# A stale VIRTUAL_ENV from pyenv or similar only produces uv warnings; uv always uses .venv here.
unexport VIRTUAL_ENV
.DEFAULT_GOAL := help

WEB := apps/web
NODE_MIN := 22

.PHONY: help setup check-node api web check test e2e images deploy-smoke

help:
	@echo "Targets:"
	@echo "  setup     uv sync, pnpm install, playwright browsers"
	@echo "  api       run FastAPI with reload on 127.0.0.1:8000 (SQLite and files under ECHO_DATA_DIR)"
	@echo "  web       run Vite on :5173 (proxies /api and /runner to :8000)"
	@echo "  check     lint, types, unit tests (python + web)"
	@echo "  test      python tests only"
	@echo "  e2e       playwright smoke tests"
	@echo "  images    build the docker images (web, app, runner) with deploy/docker-compose.yml"
	@echo "  deploy-smoke  build and start the compose stack, check it end to end, remove it"

check-node:
	@v=$$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0); \
	if [ "$$v" -lt $(NODE_MIN) ]; then \
	  echo "Node $(NODE_MIN)+ required (found $$(node --version 2>/dev/null || echo none))."; \
	  echo "nvm: nvm install 22 && nvm use 22    Homebrew: brew install node@22"; \
	  exit 1; \
	fi

setup: check-node
	uv sync
	corepack enable
	pnpm install
	pnpm -C $(WEB) exec playwright install chromium

api:
	uv run uvicorn echolot_app.main:app --reload --host 127.0.0.1 --port 8000

web: check-node
	pnpm -C $(WEB) dev

test:
	uv run pytest

check: check-node
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright
	uv run pytest
	pnpm -C $(WEB) lint
	pnpm -C $(WEB) typecheck
	pnpm -C $(WEB) test

e2e:
	pnpm -C $(WEB) e2e

# placeholders for the required settings: building needs none of them, and none is baked in
images:
	ECHO_SECRET_KEY=unused PREFECT_AUTH_STRING=unused APP_ENV_FILE=/dev/null RUNNER_ENV_FILE=/dev/null \
	  docker compose -f deploy/docker-compose.yml --env-file /dev/null --profile runner build

deploy-smoke:
	deploy/smoke.sh
