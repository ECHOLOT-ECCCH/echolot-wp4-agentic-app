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
the right.

## Status

Cut down from the Elexifier app (dictionary conversion; last commit `98999b1`). Milestone M0
is built: workspaces, file upload with a paged records view, task notes, and the agent chat
with approvals. Scripts and runs (M1) and Prefect (M2) are next. See
[docs/PLAN.md](docs/PLAN.md) for the architecture, contracts, milestones and current status,
and [docs/DECISIONS.md](docs/DECISIONS.md) for the decision log.

## Requirements

- Python 3.12 (managed by [uv](https://docs.astral.sh/uv/))
- Node.js 22+ and pnpm (via `corepack enable`)
- GNU Make
- For real runs: a Prefect 3 server and a runner host that can reach the services (PLAN M2)

## Quick start (development)

```bash
make setup             # uv sync, pnpm install, playwright browsers
cp .env.example .env   # the test model needs no key; set ECHO_MODEL_* for a real one
make api               # FastAPI on 127.0.0.1:8000, SQLite and files under ./data
make web               # Vite on :5173
make check             # lint, types, unit tests
make e2e               # browser smoke tests (start their own servers)
```

## License

[MIT](LICENSE).
