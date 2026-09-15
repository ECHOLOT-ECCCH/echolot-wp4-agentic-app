# Elexifier Agentic App

Web app where lexicographers convert legacy dictionaries (XML, PDF) to DMLex by instructing an LLM agent.
Plan and status: docs/PLAN.md. Decisions log: docs/DECISIONS.md. Ideas: docs/BACKLOG.md.

## Session start
1. Read docs/PLAN.md: Status block, current milestone checklist. 2. Read the tail of docs/DECISIONS.md.
3. `git status`, `git log --oneline -15`. 4. Do the first unchecked task. One task per commit. Update Status when done.

## Commands
make dev-infra    # postgres + keycloak (add PROFILE=s3 for garage)
make api          # FastAPI with reload on :8000
make web          # Vite on :5173, proxies /api /auth /events to :8000
make worker       # procrastinate worker
make check        # ruff, pyright, pytest, eslint, tsc, vitest. Must pass before every commit.
make e2e          # playwright smoke
make migration m="msg" / make upgrade

## Layout
apps/api (FastAPI) · apps/web (React) · packages/core (db, storage, jobs, crypto, events) · packages/agent (Pydantic AI agent, tools)
· packages/pipeline (conversion code behind protocols) · packages/sandbox (runner, image) · deploy/compose

## Conventions
- Python 3.12, uv workspace, src layout, ruff + pyright, async SQLAlchemy 2, pydantic v2, pytest-asyncio. Public functions typed.
- TypeScript strict, React 19, TanStack Router/Query, zustand, Tailwind + shadcn/ui, assistant-ui on AI SDK 7, CodeMirror 6, react-pdf.
- Contracts in docs/PLAN.md section 7 are fixed: ToolResult/UiAction, MappingSpec v1, pipeline protocols, PDF bbox convention
  (pdfplumber: x0, top, x1, bottom in points, origin top-left), storage key scheme, approval-gated tool list.
- Verify library APIs against the installed version, not memory. Docs: pydantic.dev/docs/ai (has llms.txt), assistant-ui.com/llms.txt, ai-sdk.dev/docs.

## Invariants
- Whole source files never go to the browser or to a model. Previews are entry-centric; samples are capped (PLAN 7.13).
- Provider keys: encrypted at rest, decrypted only in request scope, never logged.
- Approval-gated: full conversion runs, full PDF structuring, run_code, overwriting exports, deletes.
- Sandbox: no network, read-only inputs, limits; only the runner touches the Docker socket.
- Do not add Redis, Celery, brokers, Next.js or another agent framework. No auth or storage mocks in the running app.

## Git
Conventional commits, one task per commit, `make check` green first. Follow the session's attribution instructions.
