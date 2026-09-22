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

MIT, chosen by the project owner ("for now"). LICENSE file at the root; `license = "MIT"` in every
pyproject and package.json. Note kept for future changes: PyMuPDF is AGPL, so the default PDF stack
is pdfplumber + pypdfium2 to keep the license options open.

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

## 2026-09-15 Production compose written at M0.14 (unverified: Docker unavailable in this session)

- One `deploy/compose/docker-compose.yml`: caddy (serves the built SPA and proxies the API, automatic
  HTTPS via `SITE_ADDRESS`), api (runs Alembic + Procrastinate schema on start), worker (same image),
  sandbox-runner (stub answering 501 until M3, contract already enforced), postgres, garage + a
  one-shot garage-init, keycloak (`start --import-realm`, backchannel dynamic hostname).
- Inter-service URLs are fixed in the compose file; secrets and public URLs come from `.env`
  (`SITE_URL`, `SITE_ADDRESS`, `KEYCLOAK_URL`, `POSTGRES_PASSWORD`, keys). `.env.example` therefore
  serves both host development (top) and the compose stack (bottom section).
- New setting `OIDC_INTERNAL_ISSUER`: the API fetches discovery, tokens and JWKS from
  `http://keycloak:8080/...` while `iss` and the browser-facing endpoints stay on the public
  `KEYCLOAK_URL`. Keycloak needs `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true` for this.
- Known unverified pieces to check on the first `docker compose up`: the Garage bootstrap script
  (CLI reachability via `rpc_public_addr = garage:3901`, `layout`/`key import` flags), the uv-based
  Dockerfiles, Keycloak 26.4 `start --optimized` with a runtime DB config (drop `--optimized` if it
  complains), Caddy `flush_interval -1` for the chat stream.

## 2026-09-15 Compose stack verified; M0 acceptance complete

- Keycloak realm import needs `offline_access` and `uma_authorization` declared in the file when the
  default-role composite references them; `start --optimized` cannot be used for a first start with a
  runtime database configuration, so the stack uses `start --import-realm`.
- The Garage image has no shell, so the bootstrap runs from the API image as
  `python -m elexifier_core.garage_bootstrap` (admin API v2: UpdateClusterLayout / ApplyClusterLayout,
  ImportKey, CreateBucket, AllowBucketKey). `make garage-init` does the same for the dev node.
- Bug found only against real S3: aiobotocore's `StreamingBody` context manager unwraps to the raw
  aiohttp response, whose `read()` takes no size. `S3Storage.open` now iterates `iter_chunks`. The
  S3 tests run against the dev Garage (`S3_ENDPOINT=http://localhost:3900 ...`) and pass, including
  multipart upload.
- Worker health check: `procrastinate ... healthchecks` instead of the API's curl.
- Verified: `docker compose -f deploy/compose/docker-compose.yml up -d --build` with the example env
  serves http://localhost; the Playwright suite passed against it with a real Keycloak sign-in
  (`E2E_LOGIN_VIA_KEYCLOAK=1 E2E_NO_SERVERS=1 E2E_BASE_URL=http://localhost`), covering upload to
  Garage, previews, chat, approve and deny. Real deployments must set `ELX_ENABLE_TEST_MODEL=0`.

## Verification (M0, 2026-09-15)

- `make check`: green (72 pytest, 5 vitest, lint, types).
- `pnpm -C apps/web e2e` with `E2E_KEYCLOAK=1` against the dev stack: 4 passed.
- Same suite against the production compose stack: 4 passed.
- Models exercised: built-in test model only. Live provider checks (OpenRouter, direct, self-hosted)
  are still to be done with real keys, per PLAN section 14.

## 2026-09-15 Owner's converter adopted; mapping format changed (supersedes PLAN 7.8)

- The owner supplied `legacy/dmlexifier.py`, a converter driven by a nested JSON mapping (element
  descriptors with XPath selectors, text extraction from inner text/attributes/constants/autogenerated
  ids, regex, `xlat` value maps, span markers), plus the DMLex XSD 1.1, a validator and a sample export
  (`oxford.xml`). This format becomes the project's canonical mapping format (PLAN 7.8b); the simpler
  MappingSpec v1 is dropped. Reason: it is more expressive, and existing mappings use it.
- Findings from a trial run on the sample: the converter works, but its XML output fails the XSD
  (no DMLex namespace, children not in schema order, missing sense ids, `encoding='utf8'` in the
  declaration). M1 vendors the converter with those fixed and adds a schema-aware writer; the mapping
  must auto-generate sense ids. Two code bugs: an `eventlet` import from an old web service and a
  lowercase `inselector` attribute in the marker code (line 139).
- The `legacy/` folder stays in the repository as reference input; the package vendors what it needs.

## 2026-09-16 Converter vendored (M1.1); KKS inputs

- `elexifier_pipeline.xml.dmlexifier` is the owner's converter with: no web-service imports, typed
  public functions, the marker `inSelector` fix, hierarchical generated ids (`entry.id.3.sense.id.2`,
  as in the owner's KKS output), safe parsing (no DTD/network/entities), and two mapping extensions
  (`excludeSelector`, `normalizeWhitespace`, see PLAN 7.8b) needed because definitions in the Oxford
  sample contain nested label elements. The original script's keep-alive hash was essential: lxml
  recreates element proxies, so marker positions keyed by `id()` need the proxies kept alive.
- `elexifier_pipeline.xml.streaming` converts entry by entry with an ancestor-chain replica so XPath
  evaluates as in the whole document; equality with the whole-document run is tested on both samples
  and a namespaced TEI-like case. On the real 149 MB KKS export: 20k entries in 1.2 s, 23 MB peak RSS.
- KKS inputs (`legacy/kks/`, 400 MB, CC BY 4.0) stay untracked except the mapping and a README; a
  300-entry excerpt is the test fixture. The owner's mapping relies on default plurals (`entrys`,
  `partOfSpeechs`); the writer must map plurals back to DMLex element names.
- Open question for the owner: `kks_out.xml` was produced by a newer converter than the script given
  (namespace, schema order, `homographNumber`); if that version exists it should be compared.

## 2026-09-16 DMLex writer and validator (M1.3)

- XML writer: the DMLex namespace is declared once as the default namespace on
  `lexicographicResource`; entries are serialised with plain tags and streamed into the document so
  they inherit it. Children follow the XSD sequences derived from the vendored schema; attributes
  and text placement (mixed `definition`, `example/text`, `headword`) come from the same map. Markers
  are rebuilt inline from character offsets with the original nesting algorithm. Identical duplicate
  siblings are dropped and counted in the writer report because DMLex requires siblings to be unique
  and identical repeats carry no information.
- Validator: `xmlschema.XMLSchema11`; errors carry the entry index parsed from the path. Uniqueness
  constraints the library cannot evaluate ("field selects multiple values" when an entry has two
  parts of speech) are reported as limitations, not as document errors.
- Results on the fixtures: the Oxford mapping validates with zero errors after skipping empty
  pronunciations. The owner's KKS mapping needs `langCode` instead of `language`, generated sense
  ids, non-empty example fragments and `PartOfSpeech[@value]`; after that only duplicate headwords
  remain, which need homograph numbering (planned for the conversion run).

## 2026-09-16 Document parse job and entry index (M1.4)

- Profiling and indexing use Python's expat parser in two streaming passes: byte offsets from
  `CurrentByteIndex` give every entry an exact source range, so entries are fetched later with a
  storage range read (8 ms for entry 88,000 of the KKS export) instead of re-parsing. Empty-element
  tags need the end resolved from the bytes (expat's end event points past `/>`).
- Entry candidates are ranked by count, concentration under one parent and depth (root children
  doubled); the top candidate is used unless the document has an explicit `entry_xpath`. Headwords
  come from an optional `headword_xpath`, else a headword-like element or attribute, else the first
  short text.
- Jobs live in `elexifier_api.jobs` (they need models, storage and the pipeline); the worker loads
  them through the `task_modules` setting. The Procrastinate app is created at import time, so the
  API binds it to its runtime database with `replace_connector` for the lifespan (`with_connector` is
  deprecated and breaks task deferral). Test databases now get the Procrastinate schema too.
- XML sources uploaded to XML projects are parsed automatically; `POST .../documents` and
  `.../reparse` cover explicit parsing with other XPaths. Measured on the real KKS file through the
  API and worker: upload 0.3 s (local storage), parse job 13 s, worker RSS 34 MB after the job,
  88,575 entries; headword/snippet search 0.34 s (ILIKE; a trigram index is a later optimisation).

## 2026-09-16 Entry browser (M1.5)

- Middle pane: `DocumentPanel` = `EntryNavigator` (search with debounce, 50 per page, ordinal
  numbers, snippets) + `EntryView` (source XML in CodeMirror). Preview targets gained
  `document` and `entry` (with the document id); ui-action `open_document` was added to the
  contract next to `open_entry`.
- Clicking an analysed XML file opens the entry browser rather than the raw file; small files that
  have no document still preview as text. The left pane lists documents with status and counts,
  refreshed by SSE (`document.ready`, `document.error`, parse progress) and by polling while parsing.
- The browser smoke test now depends on a running worker (document must become ready); CI starts
  one before Playwright, developers run `make worker`.

## 2026-09-16 XML toolset and the first real mapping session (M1.6)

- Ten tools in `elexifier_agent.tools.xml`, all reading entries through the entry index and
  storage range reads (`tools/_entries.py`), never the whole file: `xml_profile`,
  `xml_sample_entries`, `xml_search`, `xml_test_xpath`, `xml_distinct_values` (scans up to 5,000
  entries; above 40 entries it downloads the file once to a temp file instead of one range read
  per entry), `mapping_get`, `mapping_write`, `mapping_explain`, `spec_read`, `spec_update`.
  `mapping_write` validates with `load_mapping_spec` + `check_mapping` and stores a new `mappings`
  version only when there are no errors; warnings and infos are returned to the model either way.
  `document_id` is optional everywhere: the project's single ready document is the default.
- The agent's output cap is set to 16,384 tokens (`ModelSettings(max_tokens=...)` on the Agent).
  The Anthropic default of 4,096 is too small for one `mapping_write` call carrying a complete
  mapping (the first real run failed with "token limit exceeded before any response").
- Entry selectors may carry predicates (`se[not(@hidden='y')]`): the streaming planner matches
  the element name and the converter applies the predicate on the wrapped entry. Selectors that do
  not end in an element name (unions, `*`, attribute steps) are rejected by `check_mapping` so the
  agent learns it at `mapping_write` time rather than at the first conversion.
- `check_mapping` also enforces the XSD assertion that `headwordTranslation`,
  `headwordExplanation` and `exampleTranslation` need `langCode` unless the resource declares
  exactly one `translationLanguage`; the model missed it in its first proposal (38 of 40 schema
  errors).
- Verification with the owner's key, `claude-opus-5`, Oxford sample (102 entries), through the
  chat endpoint: turn 1 (22 tool calls, 257k input / 24k output tokens, 4 min 50 s) profiled the
  file, sampled entries, checked distinct values and XPaths, corrected two wrong element meanings
  given in the prompt, stored mapping v1 and a spec; turn 2 with corrections stored v2 and updated
  the spec (1 min 37 s). Offline check of v2: 87 entries, schema-valid except 2 duplicate sense
  indicators inside one entry (source data; the XSD requires distinct indicators) and the known
  multi-part-of-speech validator limitation; 4 empty senses flagged by `check_consistency`.
  M1.7 gives the agent the sample conversion and validation so it can see this itself.
- REST routes for mappings and the spec are deferred to M1.9, where the editors need them.

## 2026-09-16 Sample conversion and side-by-side view (M1.7)

- Samples convert indexed entries from their byte ranges, not by re-streaming the file: the bytes
  before the first entry give the ancestor chain (`head_ancestors`), the entry fragment is
  serialised back inside a replica of that chain (`wrap_fragment`) so namespaces and prefixes
  resolve exactly as in the document, and `transform_wrapped` applies the mapping. Entries at any
  position cost one range read. Entries nested under differing ancestors (an entry inside a
  `superEntry` next to top-level entries) get the first entry's chain; not seen in the samples.
- Mappings may hold several `entry` descriptors (the agent's Oxford v2 makes phrasal verbs,
  fixed phrases and idioms separate entries). The first descriptor decides the streamed element;
  the others run on the same wrapped element, so they only find what sits inside a primary entry.
  `EntryPlan.warnings` names descriptors whose selector starts elsewhere. `stream_transform`
  now yields normalized entries (DMLex JSON names) by default because normalization needs the
  producing descriptor.
- Generated ids in samples follow the source ordinal (`entry.id.<ordinal+1>`) only for
  single-descriptor mappings; with secondary descriptors the counter is run-local, since seeding
  per source entry collided (`entry.id.23` twice in the first real sample).
- Per-entry validation writes the sample as a temporary resource (placeholders for missing
  `uri`/`langCode`, reported as resource issues), validates it with the XSD and attributes errors
  by entry index; `check_consistency` findings are added as errors (duplicates, empty headword) or
  warnings (empty sense, empty part of speech). Stored per entry in `converted_entries.issues`.
- Migration 0004: `conversion_runs`, `converted_entries` (normalized DMLex JSON; XML is rendered
  on request by the writer). The sample service lives in `elexifier_agent.conversion` because
  both the tool and the API use it and the agent package cannot import the API.
- Tools: `run_conversion(kind="sample", n | ordinals, strategy, query, mapping_version_number)`
  and `diff_runs(run_a, run_b)`. Full runs are M1.8. A `run.finished` event is published so the
  runs list refreshes.
- UI: `open_entry` carries `run_id`; `EntryView` shows the source next to the DMLex output
  (rendered, XML, JSON) with issues; `RunPanel` lists a run's entries with issue badges; the left
  pane lists runs; tool cards for profile, mapping_write, run_conversion and diff_runs.
- Verification with `claude-opus-5` on the Oxford project: the agent picked 10 entries
  (`ordinals` of baby, back, b and a spread), the sample produced 38 output entries (multiword
  units) with 2 hidden entries skipped, 3 errors and 7 warnings; it then started checking the
  sources behind the warnings. The turn was cut short by the owner's exhausted API credits; the
  API stored the failed agent run with the provider's message and kept the conversation intact.

## 2026-09-16 Full runs as jobs, export and report (M1.8)

- `elexifier_pipeline.xml.convert.convert_document` runs three passes with flat memory: stream and
  convert into a temporary JSONL spool (one line per output entry with its source ordinal), write
  DMLex XML and JSON from the spool, then validate the XML and run the consistency checks. The
  spool makes homograph numbering possible in one job: duplicate (headword, parts of speech)
  groups without any source number get `homographNumber` 1..n (the XSD's `entryUnique` key),
  on by default and switchable per run (`number_homographs`).
- The job `conversions.run` (`elexifier_api.jobs.conversions`) stores the XML and JSON as
  `output` files, the full validation report (up to 10,000 errors) as an `artifact`, and
  `converted_entries` rows only for entries with findings (capped at 5,000, without the DMLex
  body): storing every converted entry of a large dictionary in Postgres would double the data
  for no gain, the files are the output. Progress is published every 2 s (`job.progress`, kind
  `conversions.run`, phase converting/writing/validating) and copied into `run.stats` so the UI
  can poll while a run is active; `run.finished` closes it.
- `run_conversion(kind="full")` raises `ApprovalRequired` unless the call is approved (Pydantic AI
  conditional approval); after approval it creates a queued run and defers the job through
  `AgentDeps.defer_job`, which the API binds to the Procrastinate app (tests pass a fake). One
  full run per project at a time. New tools `run_stats`, `export` (points at the output file of a
  finished run) and `validate_dmlex` (a run's XML or any uploaded XML file).
- Scale check on the KKS export (149 MB, 88,575 entries, owner's mapping as is): upload 0.3 s,
  parse job 14 s, full run 219 s of which convert 6.1 s, write 2.4 s, validate 207 s (xmlschema
  with XSD 1.1 on the 28 MB output); worker RSS 397 MB during validation. Output: 88,575 entries,
  2,585 homograph numbers, 40,361 schema errors (the as-is mapping lacks sense ids and langCode,
  which `check_mapping` flags before a run). Oxford (102 entries, agent's mapping v2): 0.4 s.
- The validation phase has no per-entry progress; the UI shows it as indeterminate with a note.
  Faster validation (a first pass with lxml against a generated XSD 1.0 subset, or validating
  in the conversion pass per entry and keeping only identity constraints for the end) is on the
  backlog.

## 2026-09-16 Mapping and spec editors (M1.9)

- Routes: `GET/POST /mappings`, `POST /mappings/check`, `GET /mappings/{version|latest}`,
  `GET/PUT /spec`. Saving always creates a new version (`source="user"`); a mapping with errors
  is refused with the structured issues (422). The editor runs `check` 600 ms after each change,
  so validation is the same code the agent's `mapping_write` uses (Pydantic shape + XSD-derived
  checks) instead of a CodeMirror JSON-schema plugin, which would only cover the shape.
- `MappingEditor`: version selector, note, CodeMirror JSON, issues, and a source-to-DMLex tree
  computed from the JSON being edited; "Run sample" creates a sample run with the selected
  version through `POST /runs`. `SpecEditor`: Markdown with versioned save. Both are read-only
  for viewers. The left pane got a "Conversion" section (mapping, specification).
- JSONB storage sorts object keys by length, so stored mappings come back with `children` before
  `inSelector`; the editor reorders descriptor keys into reading order (`formatMapping`) before
  showing them, and compares against the same rendering to detect unsaved changes.
- Approval-gated overwrite of exports (PLAN 7.3): moot for now, full runs write new versioned
  output files and never overwrite; nothing to gate until an export target with fixed names
  exists (e.g. a Lexonomy push, backlog).
- Verified in the browser on the Oxford project: edited the agent's v2 (part-of-speech tag
  `noun` -> `substantive`), saved as v3, ran a sample from the editor, the rendered entry shows
  the new tag; the e2e smoke now covers editor -> save -> sample -> side-by-side -> spec save.

## 2026-09-16 PDF layout extraction (M2.1)

- `elexifier_pipeline.pdf.layout.PdfplumberLayout`: words from pdfplumber with font, size, bold
  and italic (from the font name), baseline rows split at large horizontal gaps, columns from the
  distribution of line starts (peaks at least 6% of the lines, merged within 12% of the page
  width so hanging indents stay in their column), lines in reading order (column, top), blocks
  as runs of lines without a large vertical gap, font statistics and the body size. A page with
  fewer than 20 characters is flagged `is_scanned`. Rendering uses pypdfium2 (PNG at a given dpi).
  Measured on a generated 162-page two-column PDF: 76 ms per page, 78 MB RSS.
- Fixtures: `sample/pdfgen.py` writes small PDFs with the standard Type1 fonts (no library needed),
  deterministic; `sample/columns-dictionary.pdf` is three two-column pages plus one page without a
  text layer. It stands in until the owner provides a real PDF in `legacy/pdf/`.
- Migration 0005 `pdf_pages` (geometry, `layout_s3_key`, text_chars, is_scanned, line_count). The
  job `documents.layout` stores one layout JSON per page under `derived/{document_id}/` and works
  in chunks of 10 pages with progress events; PDF uploads in PDF projects create a document
  automatically (the same path as XML), and `POST /documents` accepts PDFs in any project.
- API: `GET .../documents/{id}/pages`, `.../pages/{n}/layout` (the JSON), `.../pages/{n}/image?dpi`
  (rendered once per resolution and kept in storage; meant for the model and thumbnails, the
  browser renders with PDF.js). Web: PDF documents show page counts; opening one shows page facts
  and the scan warning above the existing viewer. Overlays follow in M2.2/M2.3.

## 2026-09-16 Verification: M1 acceptance with claude-opus-5 (M1.10)

Run on `oxford.xml` (102 entries) in the browser and through the chat endpoint, owner's key,
`claude-opus-5`, conversation "Acceptance 2" of the project "Oxford acceptance M1.6".

- Turn 1 (profile, samples, distinct values, XPath tests, mapping v1, spec v1), turn 2
  (corrections: hidden entries skipped, langCode on explanations; v2), a sample run of 10 entries
  (38 output entries, 2 hidden skipped), then, after the mapping had been edited in the UI (v3):
  the agent noticed the outside edit, found through the sample that translations after an inline
  label were dropped by its own selector, stored v4, re-sampled to 0 issues, ran `diff_runs`
  (15 of 38 entries changed, issues -8) and updated the spec. Next turn: v5 (tag reverted on
  request), approval card, full run (119 entries, 1 error: duplicate sense indicator, 4 empty
  senses), a correct diagnosis of `<ic>` as a mid-translation discriminator with two options
  offered. Final turn: v6 with both agreed fixes, a clean sample, approval, full run:
  **119 entries, 0 errors, 0 warnings, schema-valid DMLex XML and JSON**, downloadable, spec v4
  with the dropped cross-reference pointers recorded. Independent validation of the export:
  valid (8 uniqueness checks the validator cannot evaluate, the known multi-POS limitation).
- Defects found and fixed during the run: (1) after an approval round trip the browser keeps the
  approved tool part in state `approval-responded`; the adapter treated it as a pending decision
  on every later turn ("Tool call results were provided, but the message history does not contain
  any unprocessed tool calls"). The server now normalises incoming messages against the outputs
  it recorded (`elexifier_agent.ui_messages`), folds the continuation's output into the stored
  part, and keeps the approved call's return when dumping a continuation (`dump_messages` drops
  orphan tool returns). (2) The output-token cap, entry-selector predicates and the langCode check
  (M1.6 entry).
- Cost: 7 completed agent runs, 55 tool calls, 4.35 M input tokens and 77 k output tokens, with
  no prompt caching: each request resends the whole history and the tool results (samples carry
  entry XML). Prompt caching is now on for the Anthropic provider (`anthropic_cache`,
  `anthropic_cache_instructions`, `anthropic_cache_tool_definitions`); cache read/write tokens are
  recorded in `agent_runs.usage`. Not yet measured live: the owner's credits ran out again before
  the check. Conversation compaction (M4) remains the structural answer for long sessions.
- Prompt adjustments recorded: none needed beyond the M1.6 workflow additions; the model followed
  the sample-fix-sample loop, asked before destructive choices and kept the spec current.
- Not done: a second legacy export with a real model (the KKS mapping was only run as-is in the
  scale check); to be picked up when credits allow.

## 2026-09-16 Rule segmenter and annotations (M2.2)

- `RuleSegmenter` (`elexifier_pipeline.pdf.segmenters`) decides entry starts per line: mode
  `bold` (first word bold), `size` (first word at least `headword_size_delta` above the page's
  body size) or `indent` (line flush with the column edge within `indent_tolerance`); `auto`
  picks bold when 3-70% of text lines start bold, else size when 2-70% start larger, else indent.
  Running heads and page numbers (short lines in the top/bottom 5% of the page) are skipped.
  Lines before the first start on a page or column become a continuation span (`continuation`,
  confidence 0.4); spans do not cross pages. Confidence: 0.95 bold and flush, 0.8 bold indented,
  0.9/0.75 size, 0.6 indent. Stats (spans, continuations, bold share, mean confidence) come with
  warnings when the counts are implausible (under 1.5 entries per page, nearly one span per
  line, many continuations). On the two-column fixture: 130 spans for 130 entries; size mode
  shows the failure as 3 page-sized blobs.
- Segmenters are discovered through `elexifier_pipeline.registry` (built-in `rule`, entry points
  in group `elexifier.pipeline` named `segmenter.<name>`).
- Migration 0006 `annotations`: one row per span with `bbox` (union box), `payload` (per-column
  `bboxes`, `line_ids`, headword guess, confidence, continuation, text), `source` rule|ml|user,
  `status` proposed|accepted|rejected|edited, `set_id` per segmentation run and `position` for
  reading order. Re-segmenting replaces only `proposed` non-user rows; decisions and the
  lexicographer's own boxes stay. `documents.entry_count` for PDFs counts non-rejected spans.
- `run_segmentation` in `elexifier_agent.pdf_service` is shared by the `pdf_segment` tool, the
  `POST .../segment` route and the `pdf.segment` job; documents above 400 pages are segmented in
  the worker, smaller ones inside the request (the fixture takes milliseconds per page).
- Tools `pdf_analyze` (profile from up to 12 pages spread over the document: columns, fonts,
  bold-start share, sample lines, current segmentation and annotation counts) and `pdf_segment`
  (returns stats, spans per page, first headwords, lowest-confidence spans, and a `show_overlay`
  ui-action). The web opens the document at the page for now; drawing the boxes and the review
  keyboard are M2.3.
- API: `GET/POST .../documents/{id}/annotations`, `PATCH/DELETE .../annotations/{id}` (a moved
  box keeps `original_bbox` and becomes `edited`), `POST .../annotations/bulk` (accept/reject by
  ids, page or document, filtered by current status), `POST .../documents/{id}/segment`.

## 2026-09-16 Overlays and review mode (M2.3)

- `OverlayLayer` draws every span's per-column boxes as SVG rects over the rendered page,
  coloured by status (proposed blue, accepted green, rejected red dashed and hidden unless
  "show rejected", edited violet, the lexicographer's own teal); the selected span gets a thick
  border and its headword guess. `PdfViewer` gained `renderOverlay` (per page, with the PDF.js
  viewport), `onCurrentPageChange`, scrolling to a box, and a fit-to-width initial zoom.
- Coordinates: PDF.js 6 no longer has `convertToViewportRectangle` (PLAN 7.10 names it), so
  `boxToViewport` converts the two corners with `convertToViewportPoint` for unrotated pages and
  scales directly otherwise. pdfplumber's coordinates for pages with /Rotate are already in
  reading orientation, so the double rotation must not be applied; rotated pages are untested
  (no fixture) and on the backlog.
- `ReviewPanel`: filters (to review, accepted, rejected, all), per-row accept/reject, "Accept
  page n" (bulk, proposed only), Segment/Re-segment with default parameters, paging by 200. The
  keys j/k/a/r/u are a window listener that ignores inputs, textareas and contenteditable
  (the chat composer), so they work while looking at the page. Overlays are fetched per page
  for the page in view and its neighbours; the list is one paged query per filter.
  `annotations.changed` events refresh both. Reload keeps the decisions (all server-side).
- The e2e smoke covers segment from the panel, 58 boxes on page 1, accept/reject by keyboard,
  the filters and a reload.

## 2026-09-16 Edit mode for entry spans (M2.4)

- "Edit boxes" in the PDF panel switches the overlay to editing with three tools: Select (drag a
  box to move it, drag one of eight handles to resize; the box becomes `edited`, the original
  geometry stays in `payload.original_bbox`), Draw (drag on the page creates a `user` box,
  accepted), Split (click inside a box). Merge with the next span on the page, relabel
  (entry, heading, note, other) and delete act on the selection. Gestures convert pixels to
  points with the page scale, which is exact for unrotated pages.
- Split and merge are server-side (`POST .../annotations/{id}/split`, `.../merge`) because
  they need the page layout: a split puts the line under the click point and everything below
  into the second span (or splits the geometry when the box has no lines); a merge joins the
  lines of both spans in reading order. Both produce the lexicographer's own spans (`user`,
  accepted) and delete the originals, with `split_from` / `merged_from` kept in the payload.
- Scrolling the viewer to a selected box measures against the scroll container, not
  `offsetTop` (whose offset parent is the resizable panel); the earlier version hid the box
  under the toolbars at small viewports.
- The e2e smoke covers split, merge, draw and delete on the two-column fixture.

## 2026-09-16 Select-to-chat and pdf_get_page (M2.5)

- Two ways to quote the page: "Select for chat" turns the overlay into a region tool (the words
  inside the dragged box come from the page layout, fetched on demand), and any text selected in
  PDF.js's text layer shows an "Add to chat" button (box from the selection's client rects,
  converted with the page scale). Both add a chip (page, box, words) on the composer; up to six
  chips are kept, removable, and travel with the next message.
- Transport: `prepareSendMessagesRequest` adds `context: [chips]` to the request body and clears
  the store (the AI SDK body shape id/messages/trigger/messageId is reproduced). The API validates
  the chips (`ContextChip`, 4,000 characters each) and appends to the last user message a text
  part the model sees ("[Selected on page 3, box (...) in points]" plus the quoted text) and a
  `data-context` part for the record; attaching is idempotent because the browser resends the
  list. The user bubble therefore shows exactly what the model received.
- `pdf_get_page(page, bbox?)` returns the lines of a page or region with boxes (capped at 6,000
  characters) and, when the model supports images (or `with_image`), the rendering at 110 dpi
  cropped to the region, sent as `BinaryContent` next to the tool result (`ToolReturn.content`).
  Page images are rendered once per resolution and kept in storage (`page_image`, shared with
  the API route); `open_page` opens the document view at that page and box.

## 2026-09-16 Structuring a sample of spans (M2.6)

- `elexifier_pipeline.pdf.structurers`: `DmlexEntryDraft` is what the model fills in (headword,
  homograph number, parts of speech, labels, transcriptions, inflected forms, senses with
  indicator, labels, definitions, translations, explanations and examples, plus confidence and
  notes); `draft_to_dmlex` turns it into the normalized DMLex JSON the writers and validator
  already use, with generated ids `e1`, `e1.s1` and the profile's first target language as
  `langCode` on translations. `LlmStructurer` is a Pydantic AI agent with structured output per
  entry (instructions: copy the source wording, one sense per meaning, translations are not
  definitions, lower the confidence when unsure), the page crop as `BinaryContent` when the model
  sees images, a semaphore of 4, retries, per-entry usage; a failing entry is recorded, the batch
  goes on. `RuleStructurer` (headword + one definition, confidence 0.3) is the free baseline and
  what the tests and the e2e use.
- The pipeline package now depends on `pydantic-ai-slim` (the structurer lives there per PLAN
  7.7); the model object comes from the caller, so the pipeline stays provider-agnostic.
- `StructureProfile` (source language, target languages, contents, id prefix) is given once to
  `pdf_structure` and kept in `projects.settings.structure_profile`; later calls merge over it.
- Tools: `pdf_estimate_cost` (token and price estimate from the curated or cached model info:
  700 prompt tokens plus text/4 and 1,100 per page crop per entry, 260 plus text/6 output) and
  `pdf_structure(kind="sample", n | span_ids, profile, structurer, with_images)`: usable spans
  are accepted and edited ones, or all proposals when nothing was reviewed. Results are stored as
  a `sample` run with `mapping_version=0`, `converted_entries` linked to the span
  (`annotation_id`, migration 0007 with `confidence`, `notes`, `override`), per-entry validation
  and a `usage_records` row with purpose `structure`. `AgentDeps` carries the turn's model and
  its spec for tools that call models; `model_info` is resolved from the catalogue cache or the
  curated registry (vision, prices).
- API: `POST .../documents/{id}/structure` (same service; the UI's button and the tests),
  `GET .../annotations/{id}`, converted entries expose `annotation_id`, `confidence`, `notes`.
  Web: `SpanView` shows the page crop of the span (the cached 110 dpi rendering, clipped) with
  the span text next to the DMLex output, notes and confidence; runs without a mapping are
  labelled by structurer.
- Not verified live: structuring 10 spans with the owner's model (credits); the scripted-model
  and rule paths are covered by tests. The curated registry has no prices for direct providers,
  so estimates there give tokens only; OpenRouter models come with prices.

## 2026-09-16 Full structuring runs, corrections and export (M2.7)

- `pdf_structure(kind="full")` needs approval, records the estimate in the run's scope and
  queues `pdf.structure` (`elexifier_api.jobs.structuring`). The worker rebuilds the model from
  the credential stored with the run's model spec (decrypted with the app's secret box), works
  in batches of 16 with the structurer's own concurrency, publishes progress with token counts,
  and writes `converted_entries` per span. Entry ids are assigned by position when the run is
  assembled (`renumber_entry`), since batches and cached results number from 1.
- Cache: `converted_entries.cache_key` = sha256(model, provider, structurer, profile, span text)
  (migration 0008). A full run reuses the latest earlier result of this project for an unchanged
  span (its correction if there is one) and notes "reused from an earlier run"; the run stats
  count `cached`. Re-segmentation therefore only pays for spans whose text changed.
- Cost accounting: `stats.cost_check` compares the estimate with the tokens actually used; the
  run panel shows the ratio. The rule structurer's estimate is zero.
- Corrections: `PATCH .../runs/{id}/entries/{cid}` stores the lexicographer's DMLex JSON as
  `override` (or removes it), re-validates that entry and keeps the model's original;
  `POST .../runs/{id}/rebuild` writes the XML and JSON again from the stored entries with
  corrections applied (versioned file names) and refreshes per-entry schema issues.
  `GET .../runs/{id}/entries?review=true` lists entries with issues or confidence below 0.6.
- Writers: resource-level lists such as `translationLanguages` are now written (after the
  entries, as the XSD sequence requires) and appear in the JSON header. Translations always get
  a `langCode` ("und" when unknown): XSD 1.1 assertions are evaluated on the element subtree, so
  the schema's "unless exactly one translationLanguage" escape can never hold under a conforming
  validator; `check_mapping` and the XML workflow prompt now say so.
- Web: the run panel shows tokens, the estimate ratio, cached and failed counts, a "needs
  review" filter and "Rebuild export"; `SpanView` gets "Correct" (JSON editor, save or remove)
  and a "corrected" badge; the rendered view shows the correction.
- Verified with the rule structurer end to end (130 spans through the worker, cached sample
  reuse, correction, rebuilt export valid). The model path is covered by the scripted-model tests;
  the cost-estimate check within 25% (M2.7 acceptance) waits for credits and the owner's PDF.

## 2026-09-16 The owner's PDF: Šega, Veliki poslovni moderni slovar (M2.8 preparation)

Born-digital (Word to Distiller 2006), 1,267 pages, 8.2 MB, single column, Times 9 pt body,
headwords in Arial Bold 8 pt, collocation blocks in Times Bold introduced by a pointing-hand
glyph (Wingdings on most pages, a Times-encoded "F" on others), domain labels in 6 pt small caps,
English examples in italics with Slovene translations. Findings and fixes, all without a model:

- pdfplumber split words wherever the font *subset* changed, which cut every Slovene letter out
  of its word ("ve", "č", "jega"): the layout now asks for words split on spaces only and takes
  each word's font from the majority of its characters, splitting a word only where the font
  family or size changes inside it (the marker glyph glued to the next bold word, a headword
  touching its part of speech).
- The `bold` rule fails here: wrapped lines starting with a bold collocation word looked like
  entries. The segmenter gained a `font` mode: entries start with a distinct headword font,
  detected automatically as the font that starts at least 8% of flush-left lines while at least
  60% of its characters sit at such line starts (the body font and symbol fonts excluded; a
  minimum average word length rejects marker glyphs). `auto` tries it first; `pdf_analyze`
  reports the candidate fonts with their shares so the agent can see why. Symbol-font words never
  start entries. Result: 9,277 spans on 1,267 pages in 4 s, mode font (Arial-BoldMT 8.0), pages
  1-6, 300, 634 and 1,266-1,267 checked against the images: one span per entry, letter dividers
  dropped, running entries split only at page breaks.
- Entries running over a page break (955 here) are now linked: the continuation span carries
  `payload.continues` and the entry `payload.continued_by`; structuring excludes continuation
  spans and joins their text to the entry (`full_texts`), unless the continuation was rejected.
  The page crop shown to the model is the entry's own page.
- Fixture: `pdfgen` now uses the real Helvetica metrics, so touching glyphs and spaces behave as
  in real PDFs; the two-column fixture has 52/50/28 entries on three pages.
- Timings in the dev stack: upload 0.3 s, layout job 137-142 s (about 110 ms per page, 59,914
  lines, 4.1 M characters), segmentation job 4 s. Size for structuring: 8,322 entries, mean 484
  and median 189 characters (the longest, "price", 16,005), 836 joined with continuations;
  estimate text-only about 6.8 M input and 2.8 M output tokens, with page crops about 16 M
  input. The model runs (profile, sample, full) are the owner's to trigger.

## 2026-09-16 M2.9 Review at scale: anomaly scoring, a review sample, "accept the rest"

Reviewing 1,267 pages one by one is not an option, and a learned second opinion (M3) needs
corrections first. The app now scores a segmentation against the document's own statistics,
without a model (`pipeline/pdf/anomalies.py`, run inside every segmentation):

- Spans: `very_long` (above the 99th percentile and three times the median length),
  `many_lines`, `very_short` (under 8 characters), `no_headword`, `numeric_headword`,
  `long_headword` (more than 4 words), `low_confidence` (under 0.7), `continuation_mid_page`.
  Stored as `payload.flags`; the span list takes `?flagged=true`.
- Pages: `no_entries`, `few_entries` (under 40% of the median with at least half the median
  line count), `many_entries` (over 2.5 times the median), `flagged_spans`.
- The sample: the first and last two pages, a third of the budget for letter changes (a new
  first letter counts only when the next entries agree, so "f.c" among the a-words does not open
  a letter), then every kind of oddity, each spread evenly through the book; 40 pages by
  default. Stored as `document.stats.review` (`ReviewSummary`). The first version took flagged
  pages by reason count and page number, which gave 36 pages of the same phenomenon from the
  start of the book; the spread replaced it (unit test with a synthetic 300-page book).
- API: `GET .../documents/{id}/review` (summary, counts, remaining proposals and how many of
  them are flagged), `POST .../annotations/accept-rest` (`skip_flagged`, default true). Tool
  `pdf_review_summary`. Web: the review panel shows a "Sample" strip (page n of m,
  previous/next sample page, the page's reasons), a "Flagged" filter, amber boxes and a warning
  icon with the reasons on flagged spans, and "Accept the remaining N" with "except flagged"
  behind a confirmation.
- On the owner's PDF (rule segmenter, no model): 8,322 entries and 955 continuations, median 6
  entries per page and 179 characters per entry; 82 flagged spans (79 `many_lines`, the
  whole-page entries such as *accounting*, *company*, *cost*; `numeric_headword` "©";
  `long_headword` "c.i.f. (and) c. (and) i") and 333 flagged pages (129 without entries, which
  are the continuation pages of the giant entries *account*, *balance*, *insurance*; 115 with
  few, 50 with many). The sample has 40 pages from 1 to 1,267 with 12 letter pages. Scoring adds
  under a second to the 8 s segmentation job.
- Not done (backlog): the per-span check "first word not in the headword font" and the page
  check "a line that should have started an entry" from the plan text; both need the segmenter
  to hand its evidence to the scorer.

## 2026-09-16 M2.10 Segmentation feedback from structuring

The structurer reads every span's text, so it is the cheapest judge of the segmentation. The
draft schema gained `segmentation` (ok | merged | fragment | not_an_entry) and
`segmentation_reason`; the instructions ask the model to describe only the first entry of a
merged span and to name the headword where the next one starts.

- A verdict other than ok becomes a `segmentation` issue on the converted entry (source
  `structurer`, warning; kept through rebuilds and copied with cached results, so a second full
  run keeps the doubts without new model calls) and a flag on the span (`payload.flags`:
  merged | fragment | not_an_entry, the reason and run id under `payload.structurer`); an ok
  verdict clears an earlier one. `stats.segmentation` counts the verdicts per run.
- Where they show: the review panel's "Flagged" filter and amber boxes (the structurer's words
  in the tooltip), the run panel's stats line and its "with issues" / "needs review" lists, a
  badge in the span view; `pdf_structure` and `run_stats` say "segmentation doubts: 2 merged,
  1 fragment". The review endpoint reports the live `flagged_total`.
- PDF projects now also get `run_stats`, `export` and `validate_dmlex` (the conversion toolset
  filtered to the run tools): without them the agent could not report on a full structuring
  job or point to the download. `run_stats` labels mapping-less runs as structuring runs.
- The rule structurer never doubts (it does not read the text); verdicts come from the model.
  Tests: a scripted model in the agent test, a doubting structurer in the API job test (store,
  keep through a rebuild, reuse from the cache), a pipeline unit test for the issue.
- Not done: acting on a verdict automatically (splitting a merged span at the named headword);
  in the backlog.

## 2026-09-16 M3.1 The sandbox runner executes code

- No bind mounts. The runner builds a tar (`main.py` and `in/` root-owned and read-only, `out/`
  owned by uid 1000), creates the container with an anonymous volume at `/work` (the rootfs is
  read-only, so the archive needs a writable mount that exists before the start; tmpfs only
  appears at start), `put_archive`s the tar, starts, `wait(timeout)`s, kills at the timeout
  (exit code 137, a `[runner] killed after N s` note in stderr), reads stdout and stderr
  separately (64 KB each), `get_archive`s `/work/out` (refused beyond `max_output_mb`) and
  uploads the files to `projects/{project_id}/artifacts/{run_id}/`. This works for a sibling
  container in Compose and for `make runner` on a laptop alike. Cost: inputs are copied twice
  (staged on the runner's disk, then into the volume); streaming them is in the backlog.
- The sandbox image creates `/work` root-owned 755 before switching to the sandbox user:
  BuildKit's `WORKDIR` after `USER` would make it writable, and then a snippet could write
  outside `out/` and `/tmp` (the integration test caught it).
- The runner depends on `elexifier-core` for the storage backends, so its image now holds the
  workspace (`uv sync --package elexifier-sandbox`); Compose gives it the shared environment and
  the socket. `SANDBOX_IMAGE` setting; `make sandbox-image`.
- Limits as in 7.12: `network_mode=none`, `user=1000:1000`, `read_only`, `tmpfs /tmp`
  (`noexec,nosuid`, 256 MB), `cap_drop ALL`, `no-new-privileges`, `pids_limit`, `mem_limit`
  with `memswap_limit` equal (no swap), `nano_cpus`, `runtime=runsc` when configured.
- Tests: unit tests with a fake docker client check the archive layout and ownership, the
  container options, log truncation, the timeout path and the output cap; integration tests
  (`pytest -m docker`, skipped without daemon or image) run real containers: hello world with an
  input and an output round trip, a network attempt fails, a runaway snippet is killed at the
  timeout, the rootfs and `/work` are not writable. Verified on Docker Desktop 27.5 (arm64,
  runc): a hello-world run takes 0.12 s wall time inside the container, about 0.5 s end to end.

## 2026-09-16 M3.2 `run_code`: the agent runs snippets after approval

- Contract models moved to `elexifier_core.sandbox_contract`, shared by the runner and its
  clients; `RunOutput` carries the sha256 the runner computes while uploading, so the API
  registers outputs as `files` rows without reading them back.
- `RunnerClient` (httpx, token header, the run's timeout plus a minute) lives in the agent
  package, on `AgentDeps.runner` (built in the chat router) and as `job_runner()` for jobs.
  Errors become tool results the model can act on: unreachable runner, refused run with the
  runner's detail (missing image, bad request).
- `run_code(code, purpose, inputs, timeout_s)` is approval-gated. Inputs are file ids or the
  words `source` and `latest_export`, mounted as `in/<file name>` (duplicates get a suffix).
  The snippet is stored as an artifact before the run (`code-<id>.py`, so a failed run still
  leaves it to read); afterwards the outputs (already uploaded by the runner), a log
  (`log-<id>.txt`, stdout and stderr) and `step-<id>.json` (purpose, inputs, outputs, exit
  code, who and when: what `save_as_step` will read in M3.4) become artifacts with
  `meta.code_run`, and an audit row `sandbox.run` records purpose, exit code and duration. The
  first previewable output opens in the middle pane (`open_artifact`).
- The prompt section `sandbox_workflow.md` (both project types) says when a snippet is worth
  an approval, what is mounted where, the libraries, and the `entries.jsonl` contract for
  snippets that should become steps.
- The dev/CI test model calls every tool it sees with schema-generated arguments, so the sandbox
  toolset is filtered out for it (`isinstance(ctx.model, TestModel)`): otherwise every e2e turn
  would send junk to the sandbox and the approval flows would need two answers. The e2e still
  answers every pending approval card in a loop, which is the right shape for a real model.
- PDF and XML projects both get the tool. Tests: three scripted-model tests with a fake runner
  (httpx `MockTransport`): approval then execution with artifacts, audit and step file; input
  resolution and runner failures; a killed run. Browser: the approval card, the tool card and
  the artifact preview are checked by the owner with a real model (the canned model never
  calls the tool); the API and agent tests cover the same path.
- Incidental: the model picker dialog and its credential chips now scroll; with many credentials
  (each e2e run adds one) the model list was pushed off-screen.

## 2026-09-16 M3.3 Code-run card and artifact previews

- The approval card for `run_code` shows what the lexicographer is asked to allow: the
  purpose in words, the inputs and time limit, and the snippet itself in a code block
  (`RunCodeArgs`), instead of the raw JSON arguments the generic card shows for other tools
  (`TOOL_ARGS` next to `TOOL_BODIES`).
- The result card (`RunCodeBody`) shows the exit code or "killed at the time limit", the
  duration, the mounted inputs, the outputs (open in the middle pane, download) and stdout /
  stderr as collapsible blocks; the tool now puts the purpose into its data too.
- Previews: `TextPreview` gets a Python mode (`@codemirror/lang-python`); `.txt`, `.log`,
  `.jsonl`, `.csv` open as plain text (JSON Lines is deliberately not parsed as JSON). The
  file list shows a code run's purpose in the tooltip of its artifacts (`meta.purpose`).
- Not done: a per-run grouping of artifacts in the left pane; artifacts of a code run share
  `meta.code_run`, so a grouping can be added when the list grows long (backlog).

## 2026-09-16 M3.4 Custom steps stored

- Migration 0009 `pipeline_steps` (project, position, kind builtin | custom_code, name, config,
  code_artifact_id, enabled, created_by, timestamps). `config` holds `stage` ("entries"),
  `purpose`, `code_run_id`, `code_sha256` (the snippet's hash at save time, recorded in run
  snapshots later) and the `inputs` the snippet was tried with.
- API: `GET/POST /api/projects/{id}/steps`, `PATCH/DELETE .../steps/{id}`, `POST
  .../steps/reorder` (every id once, in the new order). A step points at a Python artifact of
  the project (the `code-<id>.py` that `run_code` keeps); editors only; audit rows
  `step.created` / `step.deleted`; a `steps.changed` event refreshes the left pane.
- Tools: `save_as_step(code_run_id, name)` finds the run's artifacts by `meta.code_run`, refuses
  a snippet that is already a step, and records purpose and inputs from the run; `list_steps`;
  `remove_step` (approval, a delete). The prompt says a step is proposed only after the
  lexicographer agreed with the snippet's result on a sample, and describes the
  `entries.jsonl` contract.
- Web: a "Steps" section in the left pane (checkbox on/off, move up/down, delete with a
  confirmation, click opens the code in the middle pane; the purpose in the tooltip). The
  section is hidden while a project has no steps.
- The request-scoped session commits on exit; routers that return a freshly written row refresh
  it first (server-side timestamps), instead of committing themselves. Tests: API CRUD and
  reorder, agent save/list/remove through the scripted model.
- M3.5 makes the steps run inside conversions; until then a saved step is stored and shown but
  not applied.

## 2026-09-16 M3.5 Custom steps run inside samples and full runs

- `convert_document` takes an `entries_hook`: after the conversion pass, the spooled entries
  go out as a plain JSONL file (one normalized entry per line, no wrapper), the hook returns
  the transformed file, and the pass that writes XML and JSON reads it back with the source
  ordinals re-attached by position. When a step changes the number of entries the ordinal
  links become approximate and `step_warnings` says so (the run panel and `run_stats` show
  it); homograph groups are recomputed after the hook. The hook is synchronous because the
  conversion runs in a worker thread; the job bridges to the async runner client with
  `run_coroutine_threadsafe` on the job's loop.
- `elexifier_agent.steps`: `StepRunner` uploads the current entries file as a temporary
  derived object, runs the snippet through the sandbox runner with `in/entries.jsonl` (and
  the run's source at the mount path the snippet was tried with, when it used one), takes
  `out/entries.jsonl` back, deletes the temporary objects (only the log tails stay, on the
  run), and fails the run with the step's name, exit code and stderr tail when the snippet
  fails, is killed, or writes no entries file. `records_snapshot` merges what each step did
  into the run's `steps_snapshot`.
- Samples (tool and API) apply the enabled steps to their few entries and refuse a step that
  changes the entry count (a sample needs one output per source entry); the lexicographer
  sees the effect before the full run. Full runs snapshot the enabled steps when queued and
  replay exactly those (a step deleted in between fails the run with a message).
- `AppState.runner` holds the one `RunnerClient`; the chat router and the sample endpoint use
  it, jobs use `job_runner()`. Tests plug in `elexifier_agent.testing.local_runner`, which
  executes snippets with the test interpreter in a temporary directory and uploads outputs
  like the real runner, so the sample, tool and job tests run real transformations.
- Run panel: "Steps" line with each step's position, name and duration (stderr in the
  tooltip, red when it failed); `run_stats` lists the applied steps and step warnings.
- Verified: pipeline tests for the hook (transform, count change, errors), an API test that
  saves a step, sees it in a sample, in the full run's export and in a failing run's error,
  and an agent test where the scripted model runs a snippet, saves it as a step and the next
  sample shows it applied.

## 2026-09-16 M3.6 Sandbox limits verified and documented

Observed on Docker Desktop 27.5 (linux/arm64 VM, `runc`) through the `docker`-marked
integration tests, eight real containers in about 6 s:
- a socket connection to 1.1.1.1 fails at once (`OSError`, exit code 3 from the snippet);
- `time.sleep(60)` with a 3 s timeout is killed by the runner: exit code 137, killed flag,
  the note in stderr, duration under 30 s;
- 64 × 32 MB allocations with a 256 MB limit: the kernel kills the process (exit code 137,
  not the timeout) after a handful of allocations;
- 200 sleeping subprocesses with a 32-pid limit: `BlockingIOError` before 32 children;
- 3 MB of outputs with a 1 MB cap: nothing uploaded, `outputs exceed 1 MB` in stderr;
- 400 MB into `/tmp` (256 MB tmpfs): `OSError`; writes to `/usr` and `/work` fail; `in/` is
  read-only for the sandbox user.
The self-hosting guide now states the per-run limits, what is not limited, the gVisor
installation and daemon configuration for `SANDBOX_RUNTIME=runsc`, that Docker Desktop has no
`runsc`, and how to verify an installation with the integration tests. `runsc` itself could
not be exercised here (no gVisor on Docker Desktop); the runner passes the runtime name
through and the unit test covers the option.

## 2026-09-16 M3.7 Trainable segmenter: a second opinion learned from decisions

- `pipeline/pdf/learned.py`: `LearnedSegmenter` (registered as `learned`) classifies every
  body line as an entry start or not with an averaged perceptron over string features (the
  first word's font family, weight, italic, size and size relative to the body; indent bucket
  and flush-left; first character class; the previous line's end punctuation, fill of its
  column width and the gap above; position on the page; a few conjunctions such as
  `font&flush`). Pure Python, no new dependency; 12 epochs over the reviewed pages take well
  under a second for 1,800 lines. Spans are assembled like the rule segmenter's (text before the
  first start on a page is a continuation); the headword guess is the run of words in the
  first word's font. `train` holds every fourth reviewed page out when there are at least four
  and reports precision, recall and F1 on them, then retrains on all; with fewer pages the
  numbers are on the training pages and a warning says so. `save`/`load`/`to_dict` keep the
  weights as JSON.
- Gold comes from the lexicographer's decisions (`gold_from_annotations`): accepted, edited and
  drawn spans on the pages with at least one decision are positives, every other line on those
  pages is a negative, and the first lines of still-undecided proposals there are ignored.
- `pdf_train_segmenter` (tool) trains, stores the model under `derived/<document>/`, segments
  every page and compares the result with the proposals on the pages nobody reviewed: a
  proposal the model would not start, or a learned start inside a proposal, gets the flag
  `learned_disagrees` (with the kind under `payload.learned`), and the pages with most
  disagreements join the review sample (up to 50 pages). `apply=True` replaces the proposals
  on unreviewed pages with the learned spans (source `ml`); `pdf_segment(segmenter="learned")`
  runs the stored model again.
- Fixture: trained on two reviewed pages (102 starts), the model reproduces the third page
  exactly (F1 1.0; the acceptance asked for 0.95); save/load and the registry path give the
  same spans.
- Owner's PDF, offline (no data changed): with the 40 sample pages' proposals taken as decided
  (310 starts, 1,819 lines), held-out F1 is 1.0 and the model reproduces the rule segmentation
  on all 1,227 other pages, 0 disagreements. The top weights are the Arial-BoldMT font, bold and
  flush left, the gap above and the body-size features: it learned the headword-font rule. The
  second opinion becomes informative only where the lexicographer's decisions deviate from the
  proposals (corrections, drawn boxes); the plan's "flags the odd pages" needs such decisions,
  which are the owner's to make during the user test.

## 2026-09-16 Verification (M3) on the dev stack, real containers, no paid model

Run with a scripted model driving the real tools against the dev database, the local runner
(Docker Desktop 27.5, `runc`, `elexifier-sandbox:local`) and the worker; project "M3 acceptance
19:30", Oxford sample with the owner's mapping:
- `run_code` with a snippet that opens a socket: exit code 3, `blocked: OSError`, 0.2 s end to
  end. A `time.sleep(60)` with a 3 s limit: killed at the timeout after 3.1 s, the note in
  stderr. Both kept their `code-*.py`, `log-*.txt` and `step-*.json` artifacts.
- The post-processing snippet (reads `in/entries.jsonl`, upper-cases headwords, writes
  `out/entries.jsonl`; on a dry run with the source mounted it counts the 102 entries) was
  approved and run, then `save_as_step` registered it as step 1.
- `run_conversion(kind="sample")`: 3 entries in 0.4 s with the step applied (AZYME, B, BA).
  The full run queued through the API with the step in its snapshot finished in 1 s: the step
  ran once on 102 entries (0.116 s), the export is schema-valid and upper-cased throughout.
- Browser (Playwright against the dev web app): the left pane's Steps section lists the step
  with its checkbox, clicking it opens the snippet in the middle pane with Python
  highlighting, the run panel shows "Steps 1. Upper-case headwords (0.1 s)", and the artifacts
  are listed with their sizes.
- Not covered here, by design: the agent writing the snippet itself and the approval card in
  the chat, which need a paid model; the owner's in-depth user test covers them. The
  scripted-model tests exercise the same tool path, and the vitest renders the cards.

## 2026-09-21 User test finding: entries convert live, runs are for batches

The owner, testing with a real model: after a mapping change and a sample run, only the
sampled entries showed fresh DMLex; other entries showed nothing or the output of whichever
older run (and mapping version) last included them, with no way to tell. Runs were built for
the agent's batch check and for full conversions; using them as the browsing view was wrong.
Converting one entry is `convert_fragment` on a byte range of the source plus a per-entry
schema validation, a few milliseconds, so:
- `convert_live` (agent package) is the conversion behind both samples and the new preview:
  mapping plan, fragment conversion with ordinal-seeded ids, the project's enabled custom
  steps through the sandbox runner, per-entry validation. Samples require the steps (a
  missing runner or a failing step is an error, as before); the preview is best effort and
  returns the mapping output with a warning when the steps cannot run.
- `GET /api/projects/{id}/entries/{entry_id}/preview?mapping_version=` converts one entry now
  with the latest (or a given) mapping version; nothing is stored. The response carries the
  outputs (DMLex JSON, XML, issues), the steps applied, warnings, and `checked_alone`.
- The entry view shows the live output by default, labelled "Live · mapping vN", with a note
  that the entry was checked alone (duplicate ids, homograph numbers and other cross-entry
  checks need a sample or a full run). The query key carries the mapping version, so saving a
  mapping refreshes every open entry; a `steps.changed` event refreshes them too. An entry
  opened from a run keeps showing that run's stored output ("From run · mapping vN") with a
  switch to the live view.
- The XML workflow prompt tells the agent that the lexicographer sees any entry converted on
  the spot ("open any entry to see it") and that samples and full runs are where it reads
  issues and where cross-entry checks happen.
- Not done: an issue marker per entry in the navigator, which would need converting every
  entry on each mapping save (fine for small documents, a job for the large ones); backlog.

## 2026-09-22 User test finding: the XML sidebar shows the essentials

The owner asked for a quieter sidebar in XML projects. It now shows the document(s) and the
conversion links (mapping, specification); files (with the upload area), steps, runs and
members sit behind "Show more", with a one-line summary ("3 files · 2 runs · 1 step ·
members") while collapsed. A fresh project with nothing uploaded yet opens with the upload
area, an upload in the current visit keeps the files in view, and an explicit choice is
remembered per project in the browser (`elx.leftpane.more.<project>`). PDF projects are
unchanged: their review happens in the middle pane and the sidebar was not the complaint. The
state is derived (persisted choice, then this visit's upload, then "no documents yet") rather
than synchronised in an effect, so nothing collapses under the lexicographer's cursor when the
first document finishes parsing.

## 2026-09-22 User test finding: the PDF sidebar shows the structure profile, not a mapping

The "Conversion" section (Mapping, Specification) was rendered for PDF projects too, where a
mapping means nothing and nothing writes the specification; the owner found it empty on the
Šega project. Now:
- PDF projects show a **Structuring** section: **Profile** (the structure profile the agent
  agrees with the lexicographer and stores in the project settings: source language, target
  languages, what an entry contains and how it is printed, entry id prefix) and
  **Specification**. Mapping is XML-only.
- The profile is editable in the middle pane (`ProfileEditor`): `GET/PUT
  /api/projects/{id}/structure-profile` (editors; PDF projects only). The editor says that a
  change makes the next sample or full structuring call the model again for every entry,
  because the profile is part of the structuring cache key. A `project.updated` event, sent by
  the endpoint and by the agent's profile store, refreshes the sidebar and the editor.
- The specification tools moved from the XML toolset into `tools/spec.py` (`spec_toolset`),
  registered for both project types; the PDF workflow prompt tells the agent to keep the
  specification in step with the profile (`contents` is the short version the structurer reads
  on every entry, the specification is the lexicographer's full record).

## 2026-09-22 User test finding: the agent knows what the lexicographer is looking at

The owner selected an entry in an XML project and asked the agent about it; the agent had no
idea, because only the message text and PDF region chips were ever sent. Now:
- **A "looking at" note on every message.** The browser sends the middle pane's target
  (`viewing`) with each chat request; the API resolves it against the project (entry: ordinal,
  headword, document, and whether it is the live conversion or a run's output; run; PDF page;
  span; mapping version; specification; profile) and appends `[Looking at: …]` as a text part
  plus a `data-viewing` part to the user message. Targets of other projects or malformed ones
  are ignored. It lives in the user message, not in the instructions, so the cached system
  prompt and history prefixes stay intact. The system prompt explains the line and that it
  carries no content (the agent fetches the entry with its tools).
- **"Add to chat" on the entry view and the PDF span view** attaches the entry as shown:
  source XML (or the span's text and page), the DMLex output and the issues, with its origin
  (live with mapping vN, or the run). Context chips are now `region` or `entry`
  (`ContextChip.kind`); the model gets them as quoted blocks headed `[Attached: entry 8
  "babble" of oxford.xml; …]` and is told that quoted blocks are data, not instructions.
- **Attachments survive later turns.** The browser resends its own copy of earlier messages,
  which never had the server-appended parts, and `replace_messages` then dropped them: chips
  used to reach the model for one turn only. `restore_attachments` puts the stored parts back
  when the browser's parts are their prefix (an edited message is left alone).
- In the user bubble the note renders as one muted line and an attachment as a collapsed block
  (`UserTextPart`), so the lexicographer's own words stay readable.

## 2026-09-22 Split spans took positions that collided with their neighbours

The PDF e2e failed on roughly every second run at "Accept the remaining 127" (it showed 126).
Cause: splitting a span gave the halves positions `2p` and `2p+1`, which collided with other
spans' positions further down the page, and the panel's "next span" tie-break was the random
uuid, so "Merge with next" merged the first half with a random neighbour half the time and
consumed that neighbour's proposal. The split now shifts every later span of the document up
one and gives the halves `p` and `p+1`; the panel takes the next span in the list's own order
(position, top edge, id). A regression test checks the positions after a split and that the
merge leaves every other proposal alone. Lesson: a flaky e2e is a bug until proven otherwise;
the two earlier one-off failures of this test were the same defect.

## 2026-09-22 User test finding: the rendered view names the fields and shows the ids

The dictionary-style rendering hid which DMLex field a value had landed in, and showed no
sense ids; for checking a mapping that is the whole point. The rendered view is now a generic
walk over the entry's JSON: every field named the way DMLex XML names it (`partOfSpeech`,
`definition`, `headwordTranslation`, `exampleTranslation`, …), scalars as "name value",
objects with content of their own (senses, examples, pronunciations) as numbered blocks with
their id on the right, qualifiers (`langCode`, `scheme`, `definitionType`, an inflected form's
`tag`) as small tags next to the text. Because it is generic, extension modules and unexpected
fields show as well; nothing is hidden. Used by the entry view and the PDF span view.
