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
