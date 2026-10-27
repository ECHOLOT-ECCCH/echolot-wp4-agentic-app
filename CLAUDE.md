# ECHOLOT Agentic Workbench

POC web app: upload a dataset, agree with an LLM agent what to do with each record, the agent writes the
function as a Python script, and the script runs per record on a Prefect deployment that calls services
(ECHOLOT reconciliation first; enrichment and others later).
Plan and status: docs/PLAN.md. Decisions log: docs/DECISIONS.md. Ideas: docs/BACKLOG.md.
The Elexifier app this repo was cut from: commit 98999b1, docs in docs/archive/elexifier/.

## Session start
1. Read docs/PLAN.md: Status block, current milestone checklist. 2. Read the tail of docs/DECISIONS.md.
3. `git status`, `git log --oneline -15`. 4. Do the first unchecked task. One task per commit. Update Status when done.

## Commands (the Makefile matches these from M0.2 on)
make api          # FastAPI with reload on :8000 (SQLite and files under ECHO_DATA_DIR, default ./data)
make web          # Vite on :5173, proxies /api and /runner to :8000
make check        # ruff, pyright, pytest (server + runner), web lint, tsc, vitest. Must pass before every commit.
make e2e          # playwright smoke (test model, LocalExecutor, fake service)

## Layout
apps/server (FastAPI, agent, runs, SQLite) · apps/web (React) · runner (harness + Prefect flow, deployed to the
runner host, no dependency on the app) · services/*.md (agent-facing service docs)

## Conventions
- Python 3.12, uv workspace, src layout, ruff + pyright, async SQLAlchemy 2 (aiosqlite), pydantic v2, pytest-asyncio. Public functions typed.
- TypeScript strict, React 19, TanStack Router/Query, zustand, Tailwind + shadcn/ui, assistant-ui on AI SDK 7, CodeMirror 6.
- Contracts in docs/PLAN.md section 7 are fixed: script contract, runner params and I/O, results format, run status
  mapping, Executor, services, ToolResult/UiAction, agent tools and the approval-gated list.
- Verify library APIs against the installed version, not memory. Docs: ai.pydantic.dev (llms.txt), assistant-ui.com/llms.txt,
  ai-sdk.dev/docs, docs.prefect.io.

## Invariants
- Whole files never go to a model; the model sees at most 20 records per call (PLAN 7.10). The browser gets paged records.
- The model key is a SecretStr in app settings, never logged. Service keys exist only on the runner host, never in the
  app, the browser or the model's context.
- Approval-gated: submit_run, retry_failed, and any delete tool. Runs started by the user in the UI need no card.
- Scripts reach services only through ctx.service(name). The app never executes scripts in its own process.
- The app has no Prefect dependency; it talks to Prefect over REST. Prefect lives in runner/ only.
- Do not add Postgres, Redis, Celery, brokers, Procrastinate, Next.js or another agent framework. No auth in the POC:
  the app binds to 127.0.0.1.

## Git
Conventional commits, one task per commit, `make check` green first. Follow the session's attribution instructions.
