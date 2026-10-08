# Elexifier Agentic App. Compatible with GNU Make 3.81 (macOS default).
SHELL := /bin/bash
# A stale VIRTUAL_ENV from pyenv or similar only produces uv warnings; uv always uses .venv here.
unexport VIRTUAL_ENV
.DEFAULT_GOAL := help

COMPOSE_DEV := docker compose -f deploy/compose/docker-compose.dev.yml
COMPOSE_PROD := docker compose -f deploy/compose/docker-compose.yml
PROFILE ?=
WEB := apps/web
NODE_MIN := 22

.PHONY: help setup check-node dev-infra dev-infra-down api web worker runner sandbox-image check test e2e migration upgrade test-db images up down logs garage-init secrets

help:
	@echo "Targets:"
	@echo "  setup           uv sync, pnpm install, playwright browsers"
	@echo "  dev-infra       start postgres + keycloak (PROFILE=s3 adds garage)"
	@echo "  dev-infra-down  stop dev infrastructure"
	@echo "  api             run FastAPI with reload on :8000"
	@echo "  web             run Vite on :5173"
	@echo "  worker          run the procrastinate worker"
	@echo "  runner          run the sandbox runner on :8010 (needs the sandbox image and Docker)"
	@echo "  sandbox-image   build the container image snippets run in"
	@echo "  check           lint, types, unit tests (python + web)"
	@echo "  test            python tests only"
	@echo "  e2e             playwright smoke tests"
	@echo "  migration m=..  create an alembic migration"
	@echo "  upgrade         apply migrations"
	@echo "  test-db         create the elexifier_test database"
	@echo "  images          build docker images"
	@echo "  up / down       start or stop the production compose stack"
	@echo "  logs            follow production stack logs"
	@echo "  garage-init     bootstrap the dev Garage node (PROFILE=s3)"
	@echo "  secrets         print freshly generated secrets for a deployment .env"

check-node:
	@v=$$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0); \
	if [ "$$v" -lt $(NODE_MIN) ]; then \
	  echo "Node $(NODE_MIN)+ required (found $$(node --version 2>/dev/null || echo none))."; \
	  echo "Homebrew: brew install node@22 && brew link --overwrite node@22"; \
	  exit 1; \
	fi

setup: check-node
	uv sync
	corepack enable
	pnpm install
	pnpm -C $(WEB) exec playwright install chromium

dev-infra:
	$(COMPOSE_DEV) $(if $(PROFILE),--profile $(PROFILE),) up -d

dev-infra-down:
	$(COMPOSE_DEV) --profile s3 down

api:
	uv run uvicorn elexifier_api.main:app --reload --port 8000

web: check-node
	pnpm -C $(WEB) dev

worker:
	uv run python -m elexifier_core.worker worker

runner:
	uv run uvicorn elexifier_sandbox.runner.main:app --port 8010

sandbox-image:
	docker build -t $${SANDBOX_IMAGE:-elexifier-sandbox:local} packages/sandbox/image

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

migration:
	@test -n "$(m)" || (echo 'usage: make migration m="message"' && exit 1)
	uv run alembic -c packages/core/alembic.ini revision --autogenerate -m "$(m)"

upgrade:
	uv run alembic -c packages/core/alembic.ini upgrade head
	uv run python -m elexifier_core.worker schema

test-db:
	$(COMPOSE_DEV) exec -T postgres psql -U elexifier -d postgres -c "CREATE DATABASE elexifier_test" || true

images:
	$(COMPOSE_PROD) build

up:
	$(COMPOSE_PROD) up -d

down:
	$(COMPOSE_PROD) down

logs:
	$(COMPOSE_PROD) logs -f --tail=100

garage-init:
	GARAGE_ADMIN_URL=http://localhost:3903 GARAGE_ADMIN_TOKEN=dev-garage-admin-token S3_BUCKET=elexifier \
	  S3_ACCESS_KEY=GK0000000000000000000000dev \
	  S3_SECRET_KEY=0000000000000000000000000000000000000000000000000000000000000dev \
	  uv run python -m elexifier_core.garage_bootstrap

secrets:
	@python3 deploy/compose/gen_secrets.py
