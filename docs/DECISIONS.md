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
