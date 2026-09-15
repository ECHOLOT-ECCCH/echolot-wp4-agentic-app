# Elexifier Agentic App

A web application where lexicographers convert legacy dictionaries (XML exports and PDFs)
into [DMLex](https://docs.oasis-open.org/lexidma/dmlex/v1.0/dmlex-v1.0.html), the OASIS
LEXIDMA data model, by instructing an LLM agent the way they used to instruct a human
conversion engineer.

Three panes: files and dictionary information on the left, a preview of XML, JSON or PDF in the
middle, and the agent chat on the right. Bring your own model key (OpenRouter, direct
providers, or a self-hosted OpenAI-compatible endpoint). Everything is open source and
self-hostable with Docker Compose.

## Status

Early development. See [docs/PLAN.md](docs/PLAN.md) for the architecture, contracts,
milestones and current status, and [docs/DECISIONS.md](docs/DECISIONS.md) for the decision log.

## Requirements

- Python 3.12 (managed by [uv](https://docs.astral.sh/uv/))
- Node.js 22+ and pnpm (via `corepack enable`)
- Docker with Compose (PostgreSQL, Keycloak, Garage)
- GNU Make

## Quick start (development)

```bash
make setup        # uv sync, pnpm install, playwright browsers
cp .env.example .env
make dev-infra    # postgres + keycloak in docker
make upgrade      # database migrations
make api          # FastAPI on :8000
make web          # Vite on :5173 (proxies /api, /auth, /events to :8000)
```

Log in with `dev@example.com` / `dev` (imported Keycloak realm).

## Run the whole stack with Docker

```bash
cp .env.example .env            # then set ELX_SECRET_KEY and ELX_SESSION_SECRET to real values
docker compose -f deploy/compose/docker-compose.yml up -d --build
```

Open http://localhost (sign in with `dev@example.com` / `dev`). See [docs/self-hosting.md](docs/self-hosting.md)
for domains, HTTPS, identity federation, storage and backups.

## Layout

| Path | Contents |
|---|---|
| `apps/api` | FastAPI application: auth (OIDC), projects, files, chat streaming, events |
| `apps/web` | React SPA: three-pane workspace |
| `packages/core` | Settings, database models and migrations, storage, jobs, crypto, events |
| `packages/agent` | Pydantic AI agent, toolsets, prompts, model registry |
| `packages/pipeline` | Conversion code behind stable protocols (XML, PDF, DMLex) |
| `packages/sandbox` | Sandbox runner service and image |
| `deploy/compose` | Docker Compose stacks, Caddy, Keycloak realm |

## License

To be decided by the project owner (see docs/DECISIONS.md).
