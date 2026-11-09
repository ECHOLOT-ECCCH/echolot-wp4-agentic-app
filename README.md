# ECHOLOT Agentic Workbench

A proof-of-concept web app for per-record tasks over datasets, such as entity reconciliation
against the ECHOLOT reconciliation service, and later enrichment:

1. Upload input files (CSV, TSV, JSON, JSONL) and preview them.
2. Agree with an LLM agent in the chat on what should happen to each record.
3. The agent writes the function as a Python script, which you can read and edit.
4. The script runs on a sample, then on the whole dataset, as a job on a Prefect deployment
   that sits next to the services.
5. The results come back as a table to review and export.

Three panes: files, task, scripts and runs on the left, a preview in the middle, the chat on
the right. Each tester signs in with an account an admin made, works in private workspaces and
chooses the agent's model: the server's, or one from Anthropic, OpenAI, Google, Mistral,
OpenRouter or DeepSeek with their own key.

## Status

Cut down from the Elexifier app (dictionary conversion; last commit `98999b1`). Built and
tested: workspaces, file upload with a paged records view, task notes, the agent chat with
approvals, versioned scripts, runs on a local executor or a Prefect deployment (the runner in
`runner/`), results with reconciliation candidates, per-row decisions and CSV/JSONL export, accounts with
private workspaces, and bring-your-own model keys (PLAN 7.11).
Still open: the checks with a real model and the owner's own data (M1.7, M3.1, M3.4). See
[docs/PLAN.md](docs/PLAN.md) for the architecture, contracts, milestones and current status,
[docs/DECISIONS.md](docs/DECISIONS.md) for the decision log, and
[docs/runner-host.md](docs/runner-host.md) for setting up the runner next to the services.

## Requirements

- Python 3.12 (managed by [uv](https://docs.astral.sh/uv/))
- Node.js 22+ and pnpm (via `corepack enable`)
- GNU Make
- For real runs: a Prefect 3 server and a runner host that can reach the services (PLAN M2)
- For reconciliation: the ECHOLOT service,
  [ECHOLOT-ECCCH/echolot-wp4-api-service](https://github.com/ECHOLOT-ECCCH/echolot-wp4-api-service)

## Quick start (development)

```bash
make setup             # uv sync, pnpm install, playwright browsers
cp .env.example .env   # the test model needs no key; set ECHO_MODEL_* for a real one
# to call services from scripts: list them in ./services.toml (runner/services.example.toml)
# and put their keys in ./runner.env, e.g. ECHOLOT_API_KEY=... (both gitignored)
uv run echolot-admin create-user you@example.org --name "You" --admin   # prints a password
make api               # FastAPI on 127.0.0.1:8000, SQLite and files under ./data
make web               # Vite on :5173, sign in with the account above
make check             # lint, types, unit tests
make e2e               # browser smoke tests (start their own servers)
```

## Deploying for testers

[deploy/README.md](deploy/README.md): Docker Compose on one host, with https (Caddy), the app, and
a Prefect server and runner that keep the scripts away from the app's data and secrets:

```bash
cd deploy && ./init.sh             # .env with new secrets; then set SITE_ADDRESS in it
docker compose up -d --build
docker compose exec app echolot-admin create-user you@example.org --admin
```

Testers sign in with accounts an admin makes (Settings → Users), bring their own model keys, or
use the server's model if `.env` provides one. `make deploy-smoke` builds the images and checks
the whole stack end to end.

## License

[MIT](LICENSE).
