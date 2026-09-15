# Decision log

Append one dated entry per decision, deviation from docs/PLAN.md, or library substitution:
what was decided, why, alternatives considered, consequences. Newest entries at the bottom.
Verification results per milestone go under a "Verification" heading with the date.

## 2026-09-15 Initial architecture decisions (from planning discussion)

| Topic | Decision | Why |
|---|---|---|
| Shell | Custom React web app, three panes | Middle pane needs domain views (entries, overlays, diffs), not an editor. VS Code/code-server and Theia rejected: coding-agent UX, developer chrome, one backend per user. |
| Backend | Python 3.12, FastAPI | The conversion code is Python. |
| Agent core | Pydantic AI, behind an `AgentRuntime` interface | Model-agnostic, OpenRouter and OpenAI-compatible providers, tool approval, native Vercel AI data-stream adapter. DeepSeek Harness rejected as core (developer preview, single-user local coding agent). |
| Agent scope | Fixed domain tools, plus one approval-gated sandboxed Python tool whose snippets become replayable custom steps | Structured UI and reproducibility for the normal path, flexibility for odd sources. |
| Chat frontend | assistant-ui on AI SDK 7 | Tool UIs, approval cards, streaming, markdown out of the box. |
| PDF pipeline | Deterministic layout extraction, rule segmentation first, trainable ML segmenter from corrections, LLM structuring with cost estimate | Must accommodate rules, trained models and multimodal extraction. |
| PDF UI | Overlays with accept/reject, draw and correct boxes, edit extracted result, select region to chat | Chosen by the project owner. |
| Identity | Keycloak; app speaks OIDC only (BFF pattern, session cookie) | Local accounts, ORCID/GitHub, SAML federation become Keycloak configuration. |
| Deployment | One Docker Compose stack for hosted and self-hosted | Institutions must be able to run it themselves. |
| Storage | PostgreSQL + S3-compatible object store (Garage), local disk in dev | MinIO CE is archived (April 2026). |
| Jobs | Procrastinate (Postgres-backed) | No extra broker. |
| Package managers | `uv` workspace (Python), `pnpm` (web) | |

## 2026-09-15 License

Open. The project owner chooses the license. Until then no LICENSE file is committed and package
metadata carries no license classifier. Note for the choice: PyMuPDF is AGPL, so the default PDF
stack is pdfplumber + pypdfium2 to keep the license options open.

## 2026-09-15 Development machine notes (not architecture)

- Node 22 is required by AI SDK 7. On the initial development machine node@22 is installed via
  Homebrew but node@20 is linked; the Makefile checks the node major version and prints how to fix it.
- pnpm is provided through `corepack enable` (bundled with Node), pinned in the root `package.json`
  `packageManager` field.

## 2026-09-15 Installed library versions at M0.2

`uv sync` resolved Pydantic AI 2.43 (the plan's snippets were written against 1.x docs), FastAPI 0.141,
SQLAlchemy 2.0.53, Alembic 1.20, ruff 0.16, pyright 1.1.414, pytest 9.1. Consequences: every Pydantic AI
API named in PLAN section 7 (VercelAIAdapter parameters, DeferredToolRequests, OpenRouterModel) must be
checked against the installed 2.x version in M0.9 and M0.10; contracts stay the same. ruff 0.16 formats
Python code blocks inside Markdown, so `docs` and `*.md` are excluded from ruff. pytest runs with
`--import-mode=importlib` so test modules in different packages may share names.

## 2026-09-15 Web toolchain deviations at M0.3

- The Vite 8 `react-ts` template ships oxlint (not ESLint) and TypeScript 6. Kept oxlint with the
  react, typescript, import and jsx-a11y plugins plus Prettier instead of adding ESLint: same rule
  coverage for this codebase, no second linter to maintain. `pnpm lint` runs both.
- TypeScript 6 deprecates `baseUrl`; the `@/*` alias uses `paths` relative to the tsconfig.
- `src/routeTree.gen.ts` (TanStack Router) is committed because `tsc -b` runs before Vite regenerates
  it; the plugin overwrites it on every dev/build.
- shadcn/ui now generates Base UI based components (style `base-nova`) and a `cn` package; components
  under `src/components/ui` are vendored and may be edited (excluded from lint and format).
- Installed: ai 7.0, @ai-sdk/react 4.0, @assistant-ui/react 0.15, @assistant-ui/react-ai-sdk 1.4,
  @tanstack/react-router 1.170, react-pdf 11, vitest 5, playwright 1.63.

## 2026-09-15 Core package choices at M0.5

- S3 client: aioboto3 (well-known API, streaming multipart uploads, presigned URLs, works with Garage).
- Local storage stores the content type in a `.content-type` sidecar file next to the object; the
  `etag` for local objects is the sha256 of the content computed during upload.
- Events: one asyncpg LISTEN connection per API process (`EventBus`), publish with `pg_notify` inside
  the caller's transaction so events are delivered on commit. Payloads above ~7.9 kB are truncated to
  identifiers; consumers fetch details through the API.
- Procrastinate app lives in `elexifier_core.jobs`; the CLI is `elexifier_core.worker` so the app
  module is never `__main__`. `make upgrade` applies Alembic migrations and then creates the
  Procrastinate tables if missing (idempotent). Procrastinate schema upgrades use its own migrations.
- Migrations generated with Alembic autogenerate; the migration test upgrades, downgrades and
  re-upgrades the test database and `alembic check` must report no drift.
- Development note: Docker Desktop blocked on an admin prompt during this task, so migrations and
  DB tests were verified against a temporary Homebrew PostgreSQL 14 on port 5433 (scratch data
  directory, not a system service). The compose stack targets PostgreSQL 16; re-run `make test`
  against it once Docker is available.

## 2026-09-15 Authentication implementation at M0.6

- The OIDC relying party is implemented directly (httpx + joserfc) instead of authlib's Starlette
  integration: authlib's JOSE module is deprecated in favour of joserfc, and the manual flow keeps
  PKCE, state, nonce and JWKS rotation handling explicit and testable with a fake identity provider.
- Session cookie `elx_session` holds the session UUID (random, HttpOnly, SameSite=Lax, Secure when
  PUBLIC_URL is https); `elx_csrf` is readable by the SPA and must be echoed in `X-CSRF-Token` on
  every non-GET request. The transient OIDC state (state, nonce, PKCE verifier, next path) travels in
  a signed, 10-minute `elx_oidc` cookie.
- Sessions slide: expiry is extended to 7 days whenever a request arrives more than an hour after the
  last one. The ID token is stored on the session so logout can pass `id_token_hint` to Keycloak.
- Realm roles are read from `realm_access.roles` in the ID token (mapper in the realm import) with a
  fallback to the access token; `admin` maps to the app role `admin`, everything else to `user`.
- Test database helpers live in `elexifier_core.testing`; API tests truncate all tables per test and
  run the app through `asgi-lifespan` so the real lifespan (event bus, engine) is exercised.
- The workspace root lists all members as dependencies so `uv sync` and `uv add` never drop a member
  from the environment.

## 2026-09-15 File upload design at M0.7

- `POST /api/projects/{id}/files` accepts a raw body (file name in `?name=` or `X-File-Name`) as the
  primary path: it streams straight from the request into storage while hashing, so API memory stays
  flat (verified: 100 MB upload and download at a constant ~34 MB RSS). `multipart/form-data` with a
  `file` part is also accepted for forms and tools; Starlette spools it to disk.
- Size limits: `MAX_UPLOAD_MB` (default 2048) enforced while streaming, partial objects removed;
  `MAX_PREVIEW_BYTES` (default 5 MB) caps the whole-file content endpoint per PLAN 7.13.
- Instance administrators (`users.role = admin`) get owner rights on every project; non-members get
  404 so project existence is not revealed.

## 2026-09-15 Model catalog at M0.8

- Model lists come live from each provider (OpenRouter public catalog; OpenAI, DeepSeek and any
  OpenAI-compatible endpoint via `GET /models`; Anthropic `GET /v1/models`; Google
  `GET /v1beta/models`) and are merged with a small curated `model_registry.json` that only adds
  labels, capability flags and a `recommended` marker. Curated ids missing from the live list are not
  shown, so stale curated entries are harmless. Capabilities unknown to a provider are `null`.
- Catalog responses are cached in-process for an hour per provider, endpoint and key hash.
- Pydantic AI 2.43 verified: `OpenRouterModel`/`OpenRouterProvider`, `OpenAIResponsesModel` for
  OpenAI, `OpenAIChatModel` + `DeepSeekProvider`, `AnthropicModel`, `GoogleModel`, `TestModel`;
  `AgentRunResult.usage` is a property. `DataChunk` lives in
  `pydantic_ai.ui.vercel_ai.response_types`; `VercelAIAdapter.dispatch_request` accepts
  `sdk_version=7`, `deps`, `message_history`, `deferred_tool_results`, `on_complete`.
- The credential test endpoint runs a five-token completion through the real model class and records
  a `usage_records` row with purpose `probe`. Provider errors are returned with any key-like text
  masked.

## 2026-09-15 Agent package at M0.9

- Tools return `ToolReturn(return_value={"summary", "data", "artifacts", "cost"}, metadata=[DataChunk
  "data-ui-action", ...])` via `to_tool_return`; data above ~24k characters is replaced by a truncated
  preview and a note so the model asks for narrower output or an artifact.
- Tool errors are returned as results (`error_result`) rather than raised, so the model can recover.
- The system prompt is an instructions function rendered per run from `prompts/system.md` with a
  project block and the DMLex cheat sheet; toolsets are selected by project type in `toolsets_for`.
- `PydanticAIRuntime.dispatch_chat` wraps `VercelAIAdapter.dispatch_request(sdk_version=7)` and maps
  the adapter's `on_complete(AgentRunResult)` to a `RunSummary` (messages JSON, usage, model name).
- Agent tests use `FunctionModel` scripts against the migrated test database; `TestModel` covers the
  smoke path. Both need no API keys.

## 2026-09-15 Chat endpoint at M0.10

- The chat request body is the AI SDK `useChat` payload (`trigger`, `id`, `messages`) plus an
  optional `model` object (`provider`, `model_id`, `credential_id`) that overrides the project's
  default model for that turn. The key always comes from a credential owned by the calling user
  (the referenced one, else their first credential for that provider).
- Persistence: the request's messages replace the stored conversation before the run (source of
  truth for reload); after the run `on_complete` appends the assistant-side messages converted with
  `VercelAIAdapter.dump_messages(..., sdk_version=7)`, stores the Pydantic AI message history and
  usage on `agent_runs`, and writes a `usage_records` row. `PUT .../messages` remains as a client
  fallback.
- The agent runs in a background task feeding a queue that the streaming response consumes. A
  browser disconnect therefore does not abort the turn: the run finishes, is persisted and shows up
  on reload. Runs whose `on_complete` never fires are marked `cancelled` (or `failed` when an error
  chunk or exception occurred). `runtime.drain()` waits for in-flight runs at shutdown.
- Bug fixed on the way: `server_default="now()"` (a string) freezes the timestamp at table creation
  in PostgreSQL; use `func.now()`. Migration 0001 regenerated (nothing deployed yet).
- The SSE generator (`elexifier_api.sse.project_event_stream`) waits on a per-subscriber queue with a
  heartbeat timeout; `asyncio.wait_for` on an async generator's `__anext__` would cancel and kill
  the generator. httpx's in-process ASGI transport buffers streaming responses, so the SSE generator
  is unit-tested directly and the endpoint test covers authorization only.

## 2026-09-15 Web shell at M0.11

- Chat: assistant-ui's registry `Thread` (Base UI variant) with `useChatRuntime` +
  `AssistantChatTransport` from `@assistant-ui/react-ai-sdk`, `sendAutomaticallyWhen:
  lastAssistantMessageIsCompleteWithApprovalResponses`, and `onData` applying `data-ui-action` parts
  to the preview store. Every tool renders through `GenericToolCard`, which composes the registry
  `ToolFallback` parts (trigger, args, approval) with a result body that understands the
  `{summary, data, artifacts}` contract. Consecutive tool calls are grouped and collapsed by the
  thread; the user expands them.
- Conversation history is loaded from `GET .../messages` and passed as the runtime's initial
  messages; the thread remounts per conversation.
- Preview pane: CodeMirror (read-only) for text-like files up to 5 MB; react-pdf with lazily
  mounted pages (IntersectionObserver), current page derived from scroll position, bbox overlay in
  pdfplumber coordinates scaled by zoom. `pdfjs-dist` is pinned to react-pdf's version and the worker
  is loaded with Vite's `?url` import (bare-specifier `new URL()` does not resolve in dev).
- Uploads go through XHR raw-body POSTs for progress; the file list, model picker and settings page
  use TanStack Query hooks in `lib/queries.ts`.
- Vendored registry code (`src/components/ui`, `src/components/assistant-ui`, `src/hooks`) is
  excluded from lint and format; `jsx-a11y/no-autofocus` is off (dialogs focus their first field).
- Verified with a Playwright walk-through against the running API and Vite (session cookie injected
  because Keycloak is not running yet): create project, upload XML and PDF, preview both, add a test
  credential, select the model, chat "What files do I have?" (tool card + answer), reload restores
  the conversation. The same script becomes the e2e smoke test in M0.13.

## 2026-09-15 Approval flow verified at M0.12 (primary path works, no fallback)

- Pydantic AI 2.43 + AI SDK 7: a tool with `requires_approval=True` ends the run with a
  `tool-approval-request` chunk; the browser's next request carries the tool part in state
  `approval-responded` with `approval: {id, approved, reason?}`; `VercelAIAdapter.from_request`
  turns it into deferred tool results. Approve executes the tool (`tool-output-available`), deny
  emits `tool-output-denied` and the model sees the reason. Non-boolean `approved` values fail the
  adapter's strict validation; the API answers 422 and marks the run failed.
- Deviation from the plan text: instead of deleting the demo tool, a dev-only toolset
  (`elexifier_agent.tools.dev`, registered only when `ELX_ENABLE_TEST_MODEL=1`) keeps
  `demo_requires_approval` so the API tests and the browser smoke test can exercise approvals
  without a real conversion tool. Production deployments never load it.
- UI: consecutive tool calls are collapsed into a group; `ApprovalAwareToolGroup` forces the group
  open while any call inside is in `requires-action`, so an approval is never hidden. The registry
  approval card labels the buttons "Allow" and "Deny".
- The router's `on_cancel` wiring from M0.10 had not been applied (edit anchored on formatted text);
  it is in place now. Lesson recorded: after `ruff format`, re-read before anchoring edits.

## 2026-09-15 Quality gates at M0.13

- `make check` = ruff check + ruff format --check + pyright + pytest + oxlint/prettier + tsc + vitest.
  Playwright (`make e2e` / `pnpm -C apps/web e2e`) starts the API and Vite itself (`webServer`),
  reusing running servers outside CI.
- Browser tests authenticate with a session written directly to the database by
  `python -m elexifier_api.devtools session` (a global setup project stores it as Playwright storage
  state). This keeps auth unmocked in the running app while not depending on Keycloak. The real OIDC
  round trip is covered by `e2e/keycloak-login.spec.ts`, which runs when `E2E_KEYCLOAK=1` (CI's
  e2e job starts Postgres and Keycloak with the dev compose file).
- Sample fixtures live in `packages/pipeline/src/elexifier_pipeline/sample/` (tiny XML and a
  hand-built two-page PDF) and are shared by tests, smoke runs and later pipeline tests.
- CI (`.github/workflows/ci.yml`): job `check` with a Postgres service, job `e2e` with docker
  compose; Playwright report uploaded on failure. First green run is pending a GitHub remote.
