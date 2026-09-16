# Elexifier Agentic App: implementation plan

## 0. How to use this plan (read this first)

This plan is executed by Claude Opus in Claude Code, often in fresh sessions with no memory of the discussion that produced it. Everything needed is in this file. After task M0.1 the copy at `docs/PLAN.md` inside the repository is the source of truth; keep it updated there.

Session start routine:
1. Read `CLAUDE.md`, then the Status block below, then the checklist of the current milestone, then the last entries of `docs/DECISIONS.md`.
2. Run `git status` and `git log --oneline -15`.
3. Take the first unchecked task. Do not redo checked tasks.

Working agreement:
- One task at a time, finished completely: code, tests, the task's acceptance check, `make check` green, one commit, checkbox ticked, Status block updated. Then the next task.
- Verify before claiming. A task is done only when its acceptance command or manual check has actually been run. The final message of a session states what was verified and what the next task is.
- Library APIs: trust the installed version and its docs, not memory and not the names in this plan. Check versions with `uv pip show <pkg>` and `pnpm list <pkg>`. If a class or parameter named here does not exist, find the equivalent in the docs listed in section 16 and keep the contract the same. Record the substitution in `docs/DECISIONS.md`.
- Decisions in section 3 and contracts in section 7 are fixed. If one is impossible, make the smallest deviation, record it with the reason and date in `docs/DECISIONS.md`, and say so in the final message. Do not re-open decisions because another approach seems nicer.
- Ask the user only for things only they can provide: license choice, real provider keys for a manual test, SSO identity provider details, domain names. Ask once with AskUserQuestion, keep working on tasks that do not depend on the answer.
- Never add infrastructure not listed here (no Redis, Celery, RabbitMQ, Next.js, other agent frameworks). Never mock auth or storage in the running app; tests use fakes and the test model provider.
- Security invariants: whole source files never go to the browser or to a model; provider keys are decrypted only inside a request and never logged; approval-gated tools are listed in 7.3; sandbox rules are in 7.12.
- Scope discipline: implement what the task says. Write ideas into `docs/BACKLOG.md` instead of doing them.
- Prefer doing the work inline. Use a subagent only for broad read-only exploration.
- Keep the repository buildable at every commit. Conventional commit messages, one task per commit, follow the session's attribution instructions.

Definition of done for a task: acceptance check passed, `make check` green, docs touched if behaviour changed, committed, checkbox ticked.

## 1. Status

- Current milestone: M1 XML end to end (checklist in section 12b).
- Next task: M1.9
- Blockers: none. `make dev-infra` provides Postgres, Keycloak and Garage; the owner's Anthropic key is
  stored in the app (Settings). CI runs on the private GitHub repository `andrazrepar/elexifier-agentic-app`.
- Inputs from the owner (2026-09-15) in `legacy/`: `dmlexifier.py` (XML to DMLex converter driven by a
  nested JSON mapping), `dmlex.xsd` (XSD 1.1, namespaced), `validate.py`, the DMLex 1.0 specification
  (HTML), and `oxford.xml` (102-entry bilingual sample, root `<oxford>`, entries `<se>`).
- Last update: 2026-09-16 (M1.8 done)

Update this block at the end of every task.

## 2. Context

Lexicographers need to convert legacy dictionaries (XML exports and PDFs) into DMLex, the OASIS LEXIDMA data model (v1.0 Committee Specification). Today that needs an engineer who understands both source and target. This app lets the lexicographer instruct an LLM agent the way they used to instruct that engineer: explain what a structure means, look at samples, correct mistakes, approve the full run.

The user (project owner) will supply the Python conversion code for XML and PDF. This plan builds the platform around it: database, file store, agent harness with bring-your-own-key and multiple models, three-pane UI, sandbox, identity, deployment. Every component is open source.

Assumptions: up to ~50 concurrent users; dictionaries up to ~500 MB XML or ~3,000 born-digital PDF pages (OCR for scans is a later adapter); English UI first, i18n-ready; DMLex XML and JSON as primary outputs, NVH secondary; the app itself is open source, license chosen by the user (PyMuPDF is AGPL, so use pdfplumber + pypdfium2 unless the supplied pipeline already depends on PyMuPDF).

## 3. Decisions (fixed)

| Topic | Decision | Why |
|---|---|---|
| Shell | Custom React web app, three panes | Middle pane needs domain views (entries, overlays, diffs), not an editor. VS Code/code-server and Theia rejected: coding-agent UX, developer chrome, one backend per user. |
| Backend | Python 3.12, FastAPI | The conversion code is Python. |
| Agent core | Pydantic AI, behind an `AgentRuntime` interface | Model-agnostic, OpenRouter and OpenAI-compatible providers, tool approval, native Vercel AI data-stream adapter. DeepSeek Harness rejected as core (developer preview, single-user local coding agent). |
| Agent scope | Fixed domain tools, plus one approval-gated sandboxed Python tool whose snippets become replayable custom steps | Structured UI and reproducibility for the normal path, flexibility for odd sources. |
| Chat frontend | assistant-ui on AI SDK 7 (`ai@^7`, `@ai-sdk/react@^4`, latest `@assistant-ui/react-ai-sdk`) | Tool UIs, approval cards, streaming, markdown out of the box. |
| PDF pipeline | Deterministic layout extraction, rule segmentation first, trainable ML segmenter from corrections, LLM structuring with cost estimate | Must accommodate rules, trained models and multimodal extraction. |
| PDF UI | Overlays with accept/reject, draw and correct boxes, edit extracted result, select region to chat | Chosen by the user. |
| Identity | Keycloak; app speaks OIDC only (BFF pattern, session cookie) | Local accounts, ORCID/GitHub, SAML federation become Keycloak configuration. |
| Deployment | One Docker Compose stack for hosted and self-hosted | Institutions must be able to run it themselves. |
| Storage | PostgreSQL + S3-compatible object store (Garage), local disk in dev | MinIO CE is archived (April 2026). |
| Jobs | Procrastinate (Postgres-backed) | No extra broker. |
| Package managers | `uv` workspace (Python), `pnpm` (web) | |

## 4. Architecture

```
 Browser (React SPA)                      Docker Compose network
 +---------+--------------+----------+    +-----------+   +-----------+   +--------------+
 | files & |   preview    |  agent   |--->| caddy TLS |-->| api       |-->| postgres     |
 | project | XML/JSON/PDF |  chat    |    +-----------+   | FastAPI   |   | app + queue  |
 +---------+--------------+----------+          |         | + agent   |   +--------------+
   ^  SSE: chat stream, job progress,           |         +-----+-----+   +--------------+
   |       ui-actions                           v               |         | garage (S3)  |
   |                                      +-----------+   +-----v-----+   +--------------+
   +------------- OIDC ------------------>| keycloak  |   | worker(s) |-->| sandbox-     |
                                          +-----------+   | jobs +    |   | runner       |
                                                          | pipeline  |   | docker/run   |
                                                          +-----------+   +--------------+
```

Flows:
- Chat turn: the SPA posts AI SDK UI messages. The API builds a Pydantic AI agent for the project and the user's chosen model and streams the Vercel AI data-stream protocol back through `VercelAIAdapter` with `sdk_version=7`. Tools run server-side. Tool results carry `ui_actions` as data chunks that the SPA applies to the preview pane.
- Long job: a tool (full conversion, PDF layout extraction, PDF structuring) enqueues a Procrastinate job and returns a job id. The worker publishes progress with Postgres NOTIFY; the API fans it out over SSE; on completion the API appends a job-result message to the conversation so the agent sees it on the next turn.
- Approval: tools marked `requires_approval=True` end the run with `DeferredToolRequests`; the adapter emits a tool-approval-request chunk; the SPA renders an approval card; the decision goes back with the next request and the run resumes.
- Sandbox: `run_code` (approval required) posts code and input references to the sandbox-runner, which runs a fresh container with no network and strict limits, uploads outputs as artifacts, returns logs.

## 5. Stack and versions

Install the latest stable versions at the time of the task and record them in lockfiles. Do not downgrade to match this plan.

Python: FastAPI, uvicorn, SQLAlchemy 2 (async) + Alembic, asyncpg, pydantic v2, pydantic-settings, `pydantic-ai-slim[openai,anthropic,google,ui]` (add extras as needed), Procrastinate (psycopg async connector), authlib, itsdangerous, cryptography, aioboto3 (S3) or obstore, lxml, pdfplumber, pypdfium2, xmlschema, jsonschema, httpx, structlog, pytest, pytest-asyncio, ruff, pyright.

Web: React 19, Vite, TypeScript strict, TanStack Router (file-based) and TanStack Query, zustand, Tailwind v4, shadcn/ui (including its Resizable component built on react-resizable-panels), `ai@^7`, `@ai-sdk/react@^4`, `@assistant-ui/react`, `@assistant-ui/react-ai-sdk`, `@assistant-ui/react-markdown`, CodeMirror 6 (`@codemirror/lang-xml`, `lang-json`, `lang-markdown`, `merge`, `search`, via `@uiw/react-codemirror`), `react-pdf`, ESLint, Prettier, Vitest, Playwright.

Infra: PostgreSQL 16, Keycloak 26, Garage, Caddy, Docker Compose, gVisor `runsc` optional in production for the sandbox.

## 6. Repository layout

```
CLAUDE.md  README.md  Makefile  pyproject.toml (uv workspace)  pnpm-workspace.yaml  .env.example
docs/PLAN.md  docs/DECISIONS.md  docs/BACKLOG.md  docs/self-hosting.md  docs/pipeline-adapter-guide.md
apps/api/src/elexifier_api/        main.py, deps.py, auth/oidc.py, auth/session.py, routers/{me,credentials,projects,files,documents,entries,annotations,mappings,runs,conversations,chat,events,steps,spec}.py
apps/web/src/                       routes/ (TanStack), app/ThreePane.tsx, features/{project,files,preview,chat,settings}/, lib/{api.ts,sse.ts,uiActions.ts,auth.ts}
packages/core/src/elexifier_core/   settings.py, db/{base,models,session}.py, migrations/ (alembic), storage.py, crypto.py, events.py, jobs.py, tasks/
packages/agent/src/elexifier_agent/ agent.py, deps.py, runtime.py, models.py, contracts.py, ui_messages.py, prompts/{system.md,dmlex_cheatsheet.md}, tools/{project,xml,pdf,dmlex,sandbox}.py
packages/pipeline/src/elexifier_pipeline/  interfaces.py, registry.py, dmlex/{model,writers,validator}.py, schemas/ (vendored OASIS XSD + JSON Schema), xml/{source,mapping,converter}.py, pdf/{layout,segmenters,structurers}.py, sample/ (fixtures)
packages/sandbox/                   runner/main.py, image/Dockerfile
deploy/compose/                     docker-compose.yml, docker-compose.dev.yml, Caddyfile, keycloak/realm-elexifier.json, Dockerfile.api, Dockerfile.worker, Dockerfile.web
.github/workflows/ci.yml
```

Ports: api 8000, web dev 5173 (proxies `/api`, `/auth`, `/events` to 8000), keycloak 8080, postgres 5432, garage S3 3900, sandbox-runner 8010.

## 7. Contracts (exact; do not change without a DECISIONS entry)

### 7.1 Tool result and ui-actions (`packages/agent/contracts.py`)

```python
class UiAction(BaseModel):
    type: Literal["open_file", "open_entry", "open_page", "show_overlay", "open_mapping",
                  "open_run", "open_spec", "open_artifact"]
    file_id: str | None = None
    entry_id: str | None = None
    document_id: str | None = None
    page: int | None = None                      # 1-based
    bbox: tuple[float, float, float, float] | None = None   # x0, top, x1, bottom, PDF points, origin top-left
    overlay_set_id: str | None = None
    mapping_version: int | None = None
    run_id: str | None = None

class ArtifactRef(BaseModel):
    file_id: str
    name: str
    kind: str        # "report" | "sample" | "code" | "export" | "log"

class ToolResult(BaseModel):
    summary: str                  # <= 500 chars; the first thing the model and the UI see
    data: dict[str, Any] = {}     # tool-specific JSON, capped at ~6000 tokens; spill the rest into an artifact
    artifacts: list[ArtifactRef] = []
    ui_actions: list[UiAction] = []
    cost: dict[str, Any] | None = None   # nested LLM usage: input_tokens, output_tokens, usd_estimate
```

Every tool returns `ToolReturn(return_value={"summary": ..., "data": ..., "artifacts": ...}, metadata=[DataChunk(type="data-ui-action", data=a.model_dump()) for a in ui_actions])`. The `ui_actions` therefore reach the browser but not the model. Helper: `to_tool_return(result: ToolResult) -> ToolReturn`.

### 7.2 Chat request handling and persistence

- Endpoint: `POST /api/projects/{project_id}/conversations/{conversation_id}/chat`. Body is the AI SDK request as sent by `useChat` (full `messages` list). Stateless with respect to the model: the adapter rebuilds Pydantic AI history from the UI messages on every turn. This is also how approval responses arrive.
- Server code shape:

```python
@router.post("/{conversation_id}/chat")
async def chat(project_id: UUID, conversation_id: UUID, request: Request, user: CurrentUser) -> Response:
    ctx = await load_project_context(project_id, user)          # authorizes
    deps = AgentDeps(project=ctx, user=user, db=..., storage=..., model_info=...)
    agent = build_agent(ctx.source_type, model=build_model(...))
    body = await request.json()
    await persist_ui_messages(conversation_id, body["messages"])   # source of truth for reload
    return await VercelAIAdapter.dispatch_request(
        request, agent=agent, deps=deps, sdk_version=7,
        on_complete=lambda run: persist_agent_run(conversation_id, run),  # verify parameter name in installed version
    )
```

- `persist_agent_run` stores `run.all_messages_json()`, usage and the model id in `agent_runs`, and streams events into `agent_events` if the adapter exposes them (otherwise skip events in M0).
- The SPA calls `PUT .../conversations/{id}/messages` from `onFinish` with the final message list so reloads show the assistant turn. If `VercelAIAdapter` offers a server-side conversion of new messages to UI messages, use it instead and drop the PUT.
- Conversation reload: `GET .../messages` returns the stored UI messages; pass them as `initialMessages`.
- Compaction: not in M0. In M4, summarize older UI messages server-side when a conversation exceeds a token budget.

### 7.3 Approval flow

Approval-gated tools: `run_conversion(kind="full")`, `pdf_structure(scope="full")`, `run_code`, `export` when it overwrites an existing output, any delete tool. Sample runs never need approval.

Primary path (Pydantic AI + AI SDK 7):

```python
agent = Agent(model, deps_type=AgentDeps, output_type=[str, DeferredToolRequests], toolsets=[...])

@toolset.tool(requires_approval=True)
async def run_code(ctx: RunContext[AgentDeps], code: str, inputs: list[str], purpose: str) -> ToolReturn: ...

# conditional approval inside a tool:
if overwrites_existing and not ctx.tool_call_approved:
    raise ApprovalRequired(metadata={"reason": "overwrites existing export"})
```

The adapter with `sdk_version=7` emits `tool-approval-request` chunks. On the client, tool UIs receive `respondToApproval({approved, reason?})` (assistant-ui) and the runtime is configured with `sendAutomaticallyWhen: lastAssistantMessageIsCompleteWithApprovalResponses` (from `ai`). The next request carries the approval responses and the adapter turns them into `DeferredToolResults` (denied calls become `ToolDenied`).

Fallback (only if task M0.12 proves the primary path broken in the installed versions): store pending approvals in the `approvals` table when a run ends with `DeferredToolRequests`, emit a `data-approval-request` chunk, expose `POST /api/approvals/{id}` for the decision, and resume by running the agent with `deferred_tool_results=DeferredToolResults(approvals={call_id: True | ToolDenied(reason)})` and `message_history`, encoding the events with `adapter.encode_stream(...)`.

### 7.4 Model registry (`packages/agent/models.py`)

```python
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.models.test import TestModel

def build_model(provider: str, model_id: str, api_key: str | None, base_url: str | None) -> Model:
    match provider:
        case "openrouter": return OpenRouterModel(model_id, provider=OpenRouterProvider(api_key=api_key, app_title="Elexifier", app_url=settings.public_url))
        case "openai": return OpenAIResponsesModel(model_id, provider=OpenAIProvider(api_key=api_key))
        case "anthropic": return AnthropicModel(model_id, provider=AnthropicProvider(api_key=api_key))
        case "google": return GoogleModel(model_id, provider=GoogleProvider(api_key=api_key))
        case "deepseek": return OpenAIChatModel(model_id, provider=DeepSeekProvider(api_key=api_key))
        case "openai_compatible": return OpenAIChatModel(model_id, provider=OpenAIProvider(base_url=base_url, api_key=api_key or "not-needed"))
        case "test" if settings.enable_test_model: return TestModel()   # dev and CI only
    raise UnsupportedProvider(provider)
```

`ModelInfo`: `id, label, supports_tools, supports_vision, context_length, price_in_per_m, price_out_per_m`. Sources: OpenRouter `GET https://openrouter.ai/api/v1/models` (fields `architecture.input_modalities`, `supported_parameters` containing `tools`, `context_length`, `pricing`), cached one hour; a hand-maintained `model_registry.json` for OpenAI, Anthropic, Google, DeepSeek; user-declared capabilities for `openai_compatible`. PDF projects warn when the selected model lacks vision.

### 7.5 Storage (`packages/core/storage.py`)

```python
class Storage(Protocol):
    async def put(self, key: str, data: bytes | AsyncIterator[bytes], content_type: str | None = None) -> StorageStat
    def open(self, key: str, byte_range: tuple[int, int] | None = None) -> AsyncIterator[bytes]
    async def stat(self, key: str) -> StorageStat          # size, etag
    async def delete(self, key: str) -> None
    async def presigned_get_url(self, key: str, ttl_s: int) -> str | None   # None for LocalStorage
```

Implementations `LocalStorage(root)` and `S3Storage(endpoint, bucket, access_key, secret_key, region)`. Key scheme: `projects/{project_id}/{kind}/{file_id}/{filename}`. Downloads are proxied through the API in M0; presigned URLs are an optimisation for later.

### 7.6 Events

Postgres `LISTEN/NOTIFY` on channel `project_events`, payload `{"project_id": ..., "type": ..., "data": {...}}`. Types: `job.progress`, `job.finished`, `run.finished`, `document.ready`, `annotations.changed`, `ui_action`. The API holds one listener connection and fans out to SSE subscribers of `GET /api/projects/{id}/events` (heartbeat comment every 15 s). Workers emit with `SELECT pg_notify('project_events', $1)`.

### 7.7 Pipeline protocols (`packages/pipeline/interfaces.py`)

The user's code plugs in behind these. Ship stub and sample implementations so the app runs before the real pipeline arrives. All types are pydantic models.

```python
class XmlProfile(BaseModel): root: str; namespaces: dict[str, str]; total_elements: int; element_stats: list[ElementStat]; entry_candidates: list[EntryCandidate]
class ElementStat(BaseModel): path: str; count: int; attrs: dict[str, int]; sample_values: list[str]; has_text: bool; children: list[str]
class EntryCandidate(BaseModel): xpath: str; count: int; score: float; reason: str
class SourceEntry(BaseModel): ordinal: int; source_ref: str; xml: str; text: str; headword_guess: str | None

class XmlSource(Protocol):
    def profile(self, max_depth: int = 6, sample_values: int = 5) -> XmlProfile
    def iter_entries(self, entry_xpath: str) -> Iterator[SourceEntry]       # streaming, lxml.iterparse, clear elements after yield
    def sample(self, entry_xpath: str, n: int, strategy: Literal["first", "random", "spread"]) -> list[SourceEntry]
    def search(self, entry_xpath: str, query: str, limit: int) -> list[SourceEntry]

class ConversionResult(BaseModel): ordinal: int; entry: DmlexEntry | None; issues: list[Issue]; unmapped: list[str]
class XmlConverter(Protocol):
    def apply(self, spec: MappingSpec, entries: Iterable[SourceEntry]) -> Iterator[ConversionResult]

class Word(BaseModel): text: str; x0: float; top: float; x1: float; bottom: float; font: str; size: float; bold: bool; italic: bool
class Line(BaseModel): id: str; words: list[Word]; x0: float; top: float; x1: float; bottom: float
class PageLayout(BaseModel): page: int; width: float; height: float; rotation: int; lines: list[Line]; blocks: list[Block]
class PdfLayout(Protocol):
    def extract(self, pdf_path: Path, pages: Sequence[int] | None = None) -> Iterator[PageLayout]
    def render_page(self, pdf_path: Path, page: int, dpi: int = 110) -> bytes    # PNG

class EntrySpan(BaseModel): id: str; page: int; line_ids: list[str]; bboxes: list[tuple[float, float, float, float]]; headword_guess: str | None; confidence: float; source: Literal["rule", "ml", "user"]
class Segmenter(Protocol):
    name: str
    def segment(self, pages: Sequence[PageLayout], params: dict[str, Any]) -> list[EntrySpan]
class TrainableSegmenter(Segmenter, Protocol):
    def train(self, pages: Sequence[PageLayout], gold: Sequence[EntrySpan]) -> TrainReport
    def save(self, path: Path) -> None
    def load(self, path: Path) -> None

class ExtractedEntry(BaseModel): span: EntrySpan; text: str; page_image_png: bytes | None
class StructuredEntry(BaseModel): span_id: str; entry: DmlexEntry; confidence: float; notes: list[str]; usage: dict[str, int]
class Structurer(Protocol):
    name: str
    def structure(self, entries: Sequence[ExtractedEntry], profile: StructureProfile, model: Model | None) -> AsyncIterator[StructuredEntry]
    async def estimate(self, entry_count: int, avg_chars: int, with_images: bool, model_info: ModelInfo) -> CostEstimate

class DmlexWriter(Protocol):
    def write(self, entries: Iterable[DmlexEntry], fmt: Literal["xml", "json", "nvh"], resource: ResourceMeta, out: BinaryIO) -> None
class DmlexValidator(Protocol):
    def validate_file(self, path: Path, fmt: Literal["xml", "json"]) -> ValidationReport
    def check_consistency(self, entries: Iterable[DmlexEntry]) -> list[Issue]
```

`registry.py` exposes `get_segmenter(name)`, `get_structurer(name)`, `get_xml_source(path)`, discovered via entry points group `elexifier.pipeline`, so the user's package registers implementations without editing the platform. Sample implementations: `LxmlXmlSource`, `DeclarativeXmlConverter`, `PdfplumberLayout`, `RuleSegmenter` (headword detection by bold or size change at line start, hanging indent, column detection; all thresholds are `params`), `LlmStructurer` (Pydantic AI agent with `output_type=DmlexEntryDraft`, concurrency semaphore, retries, per-entry usage), `RuleStructurer` (headword plus one definition), writers for xml/json/nvh, `SchemaValidator` using the XSD and JSON Schema vendored from the OASIS lexidma repository with their license notice.

### 7.8 MappingSpec v1 (XML to DMLex)

**Superseded on 2026-09-15**: the project uses the owner's mapping format from `legacy/dmlexifier.py`
(section 7.8b). This section is kept for reference only.

```yaml
version: 1
entry_xpath: "//entry"
entry_id: { xpath: "@id" }                 # optional
fields:
  - target: headword                       # DMLex path relative to the current object
    xpath: "form/orth"
    transforms: [strip, normalize_ws]
    required: true
  - target: partOfSpeech[].tag
    xpath: "gramGrp/pos"
    value_map: { "n.": "noun", "v.": "verb" }
    unmapped_value: keep                    # keep | drop | fail
  - target: sense[]
    xpath: "sense"
    fields:
      - { target: indicator, xpath: "@n" }
      - { target: definition[].text, xpath: "def", transforms: [text_content, normalize_ws] }
      - { target: example[].text, xpath: "cit[@type='example']/quote", transforms: [text_content] }
defaults: {}
unmapped_policy: report                    # report | ignore | fail
```

Rules: `target` paths use DMLex core names; `[]` marks a repeated object; nested `fields` are evaluated relative to each node matched by the parent `xpath`. Transform registry (fixed list, extend only by adding): `strip, normalize_ws, lowercase, uppercase, text_content, inner_xml, regex_replace(pattern, repl), split(sep), join(sep), prefix(s), suffix(s), default(v)`. `mapping_write` validates the spec against a JSON Schema and rejects unknown targets and transforms. The mapping table editor in the UI reads and writes exactly this structure.

### 7.8b Mapping format (the legacy dmlexifier mapping, canonical)

A mapping is a JSON tree of element descriptors, starting at the lexicographic resource:

```json
{
  "inSelector": "/oxford", "outElement": "lexicographicResource",
  "textVals": [
    {"attribute": "{meta}constant", "constant": "Oxford sample", "outElement": "title"},
    {"attribute": "{meta}constant", "constant": "http://example.org/oxford", "outElement": "uri"},
    {"attribute": "{meta}constant", "constant": "en", "outElement": "langCode"}
  ],
  "children": [
    {"inSelector": "se", "outElement": "entry", "jsonPlural": "entries",
     "textVals": [
       {"inSelector": "hw", "attribute": "{meta}innerTextRec", "outElement": "headword"},
       {"attribute": "{meta}autogenerated", "outElement": "id"}
     ],
     "children": [
       {"inSelector": "hg/ps", "outElement": "partOfSpeech", "jsonPlural": "partsOfSpeech",
        "textVals": [{"attribute": "{meta}innerText", "outElement": "tag", "xlat": {"sam.": "noun"}}]},
       {"inSelector": "s2", "outElement": "sense", "jsonPlural": "senses",
        "textVals": [{"attribute": "{meta}autogenerated", "outElement": "id"}],
        "children": [
          {"inSelector": "tr", "outElement": "definition", "jsonPlural": "definitions",
           "textVals": [{"attribute": "{meta}innerTextRec", "outElement": "text", "regex": "\\S.*\\S|\\S"}]}
        ]}
     ]}
  ]
}
```

- `{meta}` stands for the namespace `http://elex.is/wp1/teiLex0Mapper/meta`; attributes are
  `innerText`, `innerTextRec` (recursive), `constant`, `autogenerated`, or a plain source attribute name.
- Element descriptors: `inSelector` (XPath relative to the parent match; absolute for the root),
  `outElement`, optional `jsonPlural`, `namespaces`, `children`, `textVals`, `copyToOutElt`.
- Text descriptors: optional `inSelector`, `attribute`, `constant`, `regex` + `regexGroup`, `xlat`
  value map, `markers` (span markers: `inSelector`, `outElement`, `regex`, `regexGroup`).
- Platform extensions on text descriptors (ignored by the original script): `excludeSelector`
  (XPath, relative to the text element; matched sub-elements contribute no text, their tails do) and
  `normalizeWhitespace` (collapse runs, trim; not applied when markers are present). Generated ids
  are hierarchical: `entry.id.3`, `entry.id.3.sense.id.2`.
- Rules the platform adds: a JSON Schema for the format (rejecting unknown keys), a check that every
  `outElement` is a DMLex element or member, and required auto-generated ids where the XSD demands
  them (sense). `mapping_write` validates against these before storing a version.
- Streaming: `elexifier_pipeline.xml.streaming` applies the mapping entry by entry when the entry
  descriptor's selector ends in a plain element name (`DictionaryEntry`, `.//se`,
  `tei:text/tei:body/tei:entry`); otherwise the whole document is parsed (size-guarded).

### 7.9 DMLex entry model

Implement the DMLex core module as pydantic models in `packages/pipeline/dmlex/model.py`: `DmlexEntry` (id, headword, homographNumber, partOfSpeech[], label[], pronunciation[], inflectedForm[], sense[]), `Sense` (id, indicator, label[], definition[], example[]), `Definition` (text, definitionType), `Example` (text, sourceIdentity, sourceElaboration, label[], soundFile), `PartOfSpeech` (tag, listingOrder), `Label` (tag, listingOrder), `Pronunciation` (soundFile, transcription[], label[]), `Transcription` (text, scheme), `InflectedForm` (text, tag, label[], pronunciation[], listingOrder). Other modules (crosslingual, linking, etymology, annotation, values) travel in `extensions: dict` and are validated only by the schema. The XML and JSON writers must follow the serialisation sections of the specification and pass the vendored schemas.

### 7.10 PDF coordinate convention

All bounding boxes everywhere (pipeline, database, ui-actions, annotations) use pdfplumber's convention: PDF points, origin top-left, `(x0, top, x1, bottom)`, with the page `width` and `height` in points. In the browser, convert through PDF.js so rotation is handled: `viewport.convertToViewportRectangle([x0, height - bottom, x1, height - top])` where `viewport = page.getViewport({scale})`. Never store pixel coordinates.

### 7.11 Authentication (BFF)

- authlib OIDC client against Keycloak discovery (`OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`), scope `openid email profile`.
- `GET /auth/login?next=` redirects to Keycloak; `GET /auth/callback` exchanges the code, upserts the user by `sub`, creates a `sessions` row, sets cookie `elx_session` (HttpOnly, Secure outside dev, SameSite=Lax, 7-day sliding). `POST /auth/logout` deletes the session and redirects to Keycloak end-session.
- CSRF: double submit, header `X-CSRF-Token` must equal cookie `elx_csrf` on every non-GET `/api` call.
- Roles from `realm_access.roles`: `admin`, `user`. Project authorization through `project_members`.
- Dev realm import `deploy/compose/keycloak/realm-elexifier.json`: realm `elexifier`, confidential client `elexifier-web` with redirect `http://localhost:8000/auth/callback` and `http://localhost:5173/*`, users `dev@example.com` / `dev` (user) and `admin@example.com` / `dev` (admin).
- SPA: on 401 redirect to `/auth/login?next=<current path>`.

### 7.12 Sandbox runner

```
POST /run   (header X-Runner-Token)
{ "run_id": "...", "project_id": "...", "language": "python", "code": "...",
  "inputs": [ { "s3_key": "...", "mount_path": "in/source.xml" } ],
  "limits": { "timeout_s": 300, "cpus": 2, "memory_mb": 2048, "pids": 256, "max_output_mb": 200 } }
200 { "exit_code": 0, "stdout": "...", "stderr": "...", "truncated": false, "duration_s": 12.3,
      "outputs": [ { "s3_key": "...", "name": "...", "size": 123 } ] }
```

Container: image `elexifier-sandbox:<git sha>`, `network_mode="none"`, `user="1000:1000"`, read-only rootfs, tmpfs `/tmp`, `/work/in` bind read-only, `/work/out` bind read-write, `cap_drop=ALL`, `no-new-privileges`, `pids_limit`, `mem_limit`, `nano_cpus`, `runtime="runsc"` when `SANDBOX_RUNTIME=runsc`, killed at timeout, auto-removed. Runs `python /work/main.py` with cwd `/work`. Only the runner mounts `/var/run/docker.sock`. Logs truncated at 64 KB each. Outputs uploaded under `projects/{project_id}/artifacts/{run_id}/`. The snippet is saved as artifacts `code.py` and `step.json` so `save_as_step` can register it as a `pipeline_steps` row.

### 7.13 Caps and privacy

- Browser whole-file preview only for files up to 5 MB; larger files are entry-centric only.
- Tool `data` capped at ~6,000 tokens; overflow goes to an artifact with a pointer in `data`.
- Samples sent to the model: at most 20 entries and 4,000 characters per entry per call; at most 6 page images at 110 dpi per call.
- The whole source file is never sent to a model and never logged.
- Provider keys: Fernet-encrypted with `ELX_SECRET_KEY`, decrypted only inside the request that builds the model, redacted as `***` in all logs and error messages.

## 8. Data model (PostgreSQL, UUID primary keys, timestamps everywhere)

- `users` (oidc_sub unique, email, name, role), `sessions` (user_id, expires_at, csrf), `provider_credentials` (user_id, provider, label, base_url, encrypted_key, declared_capabilities json, last_used_at), `audit_log` (user_id, action, target, meta json).
- `projects` (owner_id, name, description, source_type xml|pdf, default_model json {provider, model_id, credential_id}, settings json), `project_members` (project_id, user_id, role owner|editor|viewer).
- `files` (project_id, kind source|derived|output|artifact, name, s3_key, size, sha256, mime, meta json, created_by), `documents` (project_id, source_file_id, type, status uploaded|parsing|ready|error, stats json, error), `entries` (document_id, ordinal, headword, source_ref, snippet; index on (document_id, ordinal) and (document_id, headword)), `pdf_pages` (document_id, page_no, width, height, rotation, layout_s3_key), `annotations` (document_id, page_no, bbox json, label, entry_span_id, source rule|ml|user, status proposed|accepted|rejected|edited, payload json, created_by).
- `mappings` (project_id, version, spec json, note, created_by), `pipeline_steps` (project_id, position, kind builtin|custom_code, name, config json, code_artifact_id, enabled), `conversion_runs` (project_id, kind sample|full, mapping_version, steps_snapshot json, scope json, status queued|running|done|failed, output_file_id, stats json, validation_report json, job_id, triggered_by), `converted_entries` (run_id, entry_id, dmlex jsonb, issues json, confidence), `conversion_specs` (project_id, version, markdown, updated_by).
- `conversations` (project_id, title, created_by), `messages` (conversation_id, position, ui_message json), `agent_runs` (conversation_id, model json, messages json, usage json, status, started_at, finished_at), `agent_events` (run_id, seq, event json), `approvals` (run_id, tool_call_id, tool_name, args json, status pending|approved|rejected|expired, decided_by, decided_at), `usage_records` (user_id, project_id, run_id, provider, model, input_tokens, output_tokens, usd_estimate).
- `job_links` (job_id, project_id, run_id, kind, progress json). Procrastinate creates its own tables.

M0 creates: users, sessions, provider_credentials, audit_log, projects, project_members, files, conversations, messages, agent_runs, agent_events, approvals, usage_records. Later milestones add the rest with new migrations.

## 9. API routes

- Auth: `GET /auth/login`, `GET /auth/callback`, `POST /auth/logout`, `GET /api/me`.
- Credentials: `GET/POST /api/me/credentials`, `DELETE /api/me/credentials/{id}`, `POST /api/me/credentials/{id}/test`, `GET /api/models?credential_id=`.
- Projects: `GET/POST /api/projects`, `GET/PATCH/DELETE /api/projects/{id}`, `GET/POST/DELETE /api/projects/{id}/members`.
- Files: `GET/POST /api/projects/{id}/files` (streaming multipart, sha256 while streaming), `GET .../files/{fid}` (meta), `GET .../files/{fid}/download`, `GET .../files/{fid}/content?offset&length` (only if size <= 5 MB), `DELETE .../files/{fid}`.
- Conversations and chat: `GET/POST .../conversations`, `GET/PUT .../conversations/{cid}/messages`, `POST .../conversations/{cid}/chat`, `POST /api/approvals/{id}` (fallback only).
- Events: `GET /api/projects/{id}/events` (SSE).
- M1: documents, entries, mappings, runs (`POST .../runs`, `GET .../runs/{rid}`, `GET .../runs/{rid}/entries?offset&limit&issues=`, `GET .../runs/{rid}/diff?against=`, `GET .../runs/{rid}/export?format=`), spec.
- M2: pdf pages, annotations (bulk accept/reject), structuring jobs.
- M3: steps.

## 10. Frontend structure

- Routes: `/` (projects), `/projects/$projectId` (workspace), `/settings/credentials`.
- `app/ThreePane.tsx`: shadcn Resizable with three panels, left and right collapsible, sizes persisted in localStorage.
- Left pane (`features/project`, `features/files`): header with name, type, model chip and quick switch; files grouped by kind with upload dropzone and progress; documents with status; runs; mapping versions; steps; spec link; members.
- Middle pane (`features/preview`): a `PreviewHost` that shows whatever the current ui-action or selection points at: `TextPreview` (CodeMirror read-only, XML/JSON/Markdown modes) for files up to 5 MB, `PdfViewer` (react-pdf, virtualised pages, `OverlayLayer` SVG), later `EntryView`, `DiffView`, `MappingTable`, `SpecEditor`.
- Right pane (`features/chat`): assistant-ui thread; runtime:

```ts
const runtime = useChatRuntime({
  transport: new AssistantChatTransport({ api: `/api/projects/${projectId}/conversations/${conversationId}/chat` }),
  sendAutomaticallyWhen: lastAssistantMessageIsCompleteWithApprovalResponses,
  messages: initialMessages,
});
```

  Tool UIs with `makeAssistantToolUI({ toolName, render })`: a `GenericToolCard` (summary, collapsible data JSON, artifact links) for every tool in M0; specialised cards in M1 and M2. Approval cards use the `respondToApproval` prop. Data chunks `data-ui-action` are dispatched to `lib/uiActions.ts`.
- `lib/sse.ts`: one `EventSource` per open project; dispatches events to TanStack Query invalidations and to the ui-action store.
- `lib/api.ts`: fetch wrapper adding `X-CSRF-Token`, redirecting to login on 401.

## 11. Files to create on day one

### `CLAUDE.md`

```markdown
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
```

### `docs/DECISIONS.md`

Seed with the table from section 3 (date 2026-09-15) and the rule: append one dated entry per deviation or substitution: what, why, alternatives, consequences.

### `Makefile` targets

`setup` (uv sync, pnpm install, playwright install), `dev-infra`, `dev-infra-down`, `api`, `web`, `worker`, `runner`, `check`, `test`, `e2e`, `migration`, `upgrade`, `test-db` (creates `elexifier_test`), `images` (build docker images), `up` (prod compose).

### `.env.example`

`DATABASE_URL`, `ELX_SECRET_KEY` (Fernet key), `ELX_SESSION_SECRET`, `PUBLIC_URL`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `STORAGE_BACKEND=local|s3`, `STORAGE_LOCAL_ROOT`, `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION`, `SANDBOX_RUNNER_URL`, `SANDBOX_RUNNER_TOKEN`, `SANDBOX_RUNTIME=runc|runsc`, `ELX_ENABLE_TEST_MODEL=0`, `LOG_LEVEL`.

## 12. Milestone 0: Foundation (checklist)

Goal: a user logs in through Keycloak, creates a project, uploads files, previews them, and chats with a model of their choice using their own key; conversations survive restarts; approvals work; CI is green; the production compose stack serves the app.

- [x] M0.1 Repository bootstrap. `git init`; `.gitignore` (Python, Node, `.env`, `data/`); `README.md`; copy this plan to `docs/PLAN.md`; create `CLAUDE.md`, `docs/DECISIONS.md`, `docs/BACKLOG.md` per section 11; `Makefile` skeleton; ask the user for the license once (AskUserQuestion), continue without blocking. Acceptance: first commit exists, `cat docs/PLAN.md | head` shows this plan.
- [x] M0.2 Python workspace. Root `pyproject.toml` with `[tool.uv.workspace] members = ["apps/api", "packages/*"]`; packages `elexifier_core`, `elexifier_agent`, `elexifier_pipeline`, `elexifier_api` with `src/` layout; ruff, pyright, pytest configured at the root; one trivial test per package. Acceptance: `uv sync && uv run pytest` passes; `uv run ruff check . && uv run pyright` clean.
- [x] M0.3 Web bootstrap. `pnpm create vite apps/web --template react-ts`; add the section 5 web dependencies; Tailwind v4; shadcn init with `button, dialog, dropdown-menu, tabs, resizable, scroll-area, sonner, input, badge, tooltip`; TanStack Router file-based routing; ESLint flat config, Prettier, Vitest; Vite proxy for `/api`, `/auth`, `/events`. Acceptance: `pnpm -C apps/web build && pnpm -C apps/web test` pass.
- [x] M0.4 Dev infrastructure. `docker-compose.dev.yml` with postgres:16 and keycloak:26 (`start-dev --import-realm`) plus a `garage` service under profile `s3`; realm import per 7.11; `.env.example`; `make dev-infra`. Acceptance: `http://localhost:8080` shows the realm; login as `dev@example.com` works in the Keycloak account console.
- [x] M0.5 Core package. `settings.py` (pydantic-settings); async engine and session factory; models for the M0 tables (section 8); Alembic with migration 0001; `storage.py` with LocalStorage and S3Storage; `crypto.py` (Fernet encrypt/decrypt, key rotation-ready); `events.py` (notify + listener). Tests: crypto round trip, LocalStorage put/open/stat/delete, S3Storage against garage when `S3_ENDPOINT` is set (skip otherwise), migration upgrade and downgrade on `elexifier_test`. Acceptance: `make test` green.
- [x] M0.6 API skeleton and auth. App factory, structured logging with key redaction, `/healthz`; OIDC BFF per 7.11; sessions; CSRF; `GET /api/me`; test helper that creates a user and session directly. Acceptance: browser login through Keycloak with the dev user returns the profile from `/api/me`; `make test` green.
- [x] M0.7 Projects and files. Routes per section 9 (projects, members, files); streaming upload with sha256; download proxy with range support; content endpoint for files up to 5 MB; audit log entries. Tests with httpx ASGI client. Acceptance: upload a 100 MB file through curl with constant API memory (check with `ps`), download matches sha256.
- [x] M0.8 Credentials and models. Routes per section 9; encryption; OpenRouter model list fetch with caching; `model_registry.json`; test endpoint sends a five-token completion with the selected model; usage recorded. Tests mock HTTP. Acceptance: add an OpenRouter key in the API, list models, test succeeds (ask the user for a key or use provider `test`).
- [x] M0.9 Agent package. `contracts.py` (7.1), `deps.py`, `models.py` (7.4), `prompts/system.md` and `prompts/dmlex_cheatsheet.md` (identity as conversion engineer for lexicographers; process: understand source, confirm semantics with the user, sample before full run, validate, export, keep the spec updated; never guess meaning of unknown elements; one step at a time; use ui-actions to show things), `tools/project.py` with `list_files` and `read_file_head(file_id, max_bytes<=8192)`, `agent.py` (`build_agent(source_type, model)` with `output_type=[str, DeferredToolRequests]`), `runtime.py` (`AgentRuntime` protocol and `PydanticAIRuntime`). Tests with `TestModel` and `FunctionModel`. Acceptance: `make test` green.
- [x] M0.10 Chat endpoint and persistence. Conversations routes; chat endpoint per 7.2 with `sdk_version=7`; `agent_runs` persistence; `PUT messages`; SSE events endpoint per 7.6. Test: streaming chat with provider `test` through the adapter returns a well-formed data stream. Acceptance: curl the chat endpoint with a test-provider project and see streamed chunks.
- [x] M0.11 Web shell. Auth guard; projects list and create; workspace route with ThreePane; left pane files with upload and progress; middle pane TextPreview and PdfViewer; right pane assistant-ui chat with model picker, conversation list, GenericToolCard, ui-action dispatch; settings page for credentials; SSE client. Acceptance: manual walk-through: login, create project, upload XML and PDF, preview both, ask the agent "what files do I have" and see the tool card and answer.
- [x] M0.12 Approval spike. Add a temporary tool `demo_requires_approval` with `requires_approval=True`; verify the approval card appears, approve resumes and runs the tool, deny produces a denied result. If broken in the installed versions, implement the fallback in 7.3 and record it in `docs/DECISIONS.md`. Remove the demo tool. Acceptance: both outcomes observed in the browser and covered by an API test.
- [x] M0.13 Quality gates and CI. (first CI run needs a GitHub remote) `make check` runs ruff, pyright, pytest, eslint, tsc, vitest; Playwright smoke: login with dev user, create project, upload sample XML, preview, send a chat message using provider `test`; `.github/workflows/ci.yml` with a Postgres service running `make check` and the web build. Acceptance: CI green on the default branch.
- [x] M0.14 Production compose. `docker-compose.yml` with caddy, api, worker (placeholder task), sandbox-runner (stub returning 501 until M3), postgres, garage, keycloak; Dockerfiles; `Caddyfile` serving the web build and proxying `/api`, `/auth`, `/events`; `docs/self-hosting.md`. Acceptance: on a clean machine `cp .env.example .env && docker compose up` serves the app at `http://localhost` with working login. Tag `m0`.

## 12b. Milestone 1: XML end to end (checklist)

Goal: `legacy/oxford.xml` (and any other legacy export) becomes schema-valid DMLex XML and JSON through
the chat, using the owner's mapping format and converter; full runs are background jobs with progress,
validation report and export; every decision is recorded in the conversion spec.

- [x] M1.1 Vendor the converter. Copy `legacy/dmlexifier.py` to
  `packages/pipeline/src/elexifier_pipeline/xml/dmlexifier.py` with: LF endings, no `eventlet`/wsgi
  imports, the `inselector` typo fixed (line 139), `encoding="UTF-8"`, type hints on the public
  functions, and no module-level logging config. Keep the public functions (`TransformEx`,
  `TransformSingleXmlDoc`, `ExtractEntries`, `ExtractTextValues`, `PruneMapping`,
  `ConvertDmLexJsonToXml`) and the mapping classes. Add `sample/oxford.xml` (copy of
  `legacy/oxford.xml`) and `sample/oxford-mapping.json` (the 7.8b mapping extended to examples and
  labels). Tests: transform the sample and assert entry count 102, headwords, senses per entry.
  Acceptance: `uv run pytest packages/pipeline` green.
- [x] M1.2 Mapping format model. `xml/mapping.py`: pydantic models mirroring the descriptor classes
  (strict, unknown keys rejected), `load_mapping(dict) -> MappingSpec`, a JSON Schema export for the
  UI, and `check_mapping(spec) -> list[Issue]` (unknown DMLex `outElement`, missing sense id
  autogeneration, XPath syntax errors, plural mismatches). Tests for each check.
- [x] M1.3 Schema-aware DMLex writer and validator. Vendor `legacy/dmlex.xsd` as
  `dmlex/schemas/dmlex.xsd` (with the OASIS notice) and derive the child order per element from its
  sequences at import time. `dmlex/writers.py`: `write_xml(resources, out)` emitting the DMLex
  namespace, schema-ordered children and UTF-8; `write_json(...)` (the converter's JSON with
  plurals); NVH later. `dmlex/validator.py`: `validate_xml(path) -> ValidationReport` using
  `xmlschema.XMLSchema11` (errors with path, reason, entry index), plus `check_consistency(entries)`
  (empty headwords, duplicate ids, senses without content). Acceptance: the Oxford sample converts to
  XML that validates with zero errors; the test asserts it.
- [x] M1.4 Document parse job and entry index. Migration 0002 adds `documents`, `entries`
  (see section 8). On XML upload the API creates a document and enqueues `documents.parse`: the
  worker streams the file with `lxml.iterparse`, computes the profile (element stats, namespaces,
  entry candidates: repeated children of the root, or the most frequent repeated element with a
  short text child), stores the profile in `documents.stats`, indexes entries (ordinal, headword
  guess, byte offsets as `source_ref`, first 200 chars as snippet) for the chosen entry XPath, and
  publishes `document.ready`. Re-parse with a different entry XPath is a tool/API call.
  Acceptance: uploading `oxford.xml` yields a ready document with 102 entries; a 100 MB synthetic file
  indexes without the worker exceeding a few hundred MB RSS.
- [x] M1.5 Entry API and preview. `GET .../documents`, `GET .../documents/{id}` (profile),
  `GET .../documents/{id}/entries?offset&limit&q`, `GET .../entries/{id}` (source XML by offsets from
  storage). Web: `EntryNavigator` (paged list with search) and `EntryView` (source XML in CodeMirror)
  in the middle pane; `open_entry` ui-action opens it. Acceptance: browse and search Oxford entries.
- [x] M1.6 XML toolset. `tools/xml.py`: `xml_profile(document_id)`, `xml_sample_entries(document_id,
  n, strategy)`, `xml_search(document_id, query)`, `xml_distinct_values(document_id, xpath, limit)`
  (via `ExtractTextValues`), `mapping_get`, `mapping_write(mapping_json, note)` (validates, stores a
  new version in `mappings`), `mapping_explain` (plain-language rendering of the mapping tree),
  `spec_read`, `spec_update(markdown)`. Migration 0003 adds `mappings`, `conversion_specs`. System
  prompt gains a "how to build a mapping" section with the 7.8b format. Tests with `FunctionModel`.
  Acceptance: with the owner's Claude key, the agent profiles Oxford, proposes a mapping, and stores it.
- [x] M1.7 Sample conversion and side-by-side view. `run_conversion(kind="sample", entries=n |
  entry_ids)` runs synchronously: converts the selected entries with the stored mapping, validates each
  entry (writer + validator on a temporary resource), stores `conversion_runs` + `converted_entries`
  (migration 0004), returns issues and a `open_entry` ui-action. `EntryView` shows source, DMLex XML
  and DMLex JSON side by side with per-entry issues; `diff_runs` for two runs. Tool cards for
  profile, mapping proposal and sample results. Acceptance: sample of 10 Oxford entries visible
  side by side with validation results.
- [x] M1.8 Full run, export, report. `run_conversion(kind="full")` requires approval and enqueues
  `conversions.run`: the worker streams entries, converts with the mapping, writes DMLex XML and JSON
  to storage as `output` files, validates the XML, stores stats and the validation report
  (artifact), publishes progress every N entries and `run.finished`. `export(run_id, format)`,
  `run_stats`, `validate_dmlex(run_id)`. Left pane lists runs with progress; `RunView` shows the
  report. Acceptance: full Oxford run completes as a job with progress in the UI, output validates,
  export downloads.
- [ ] M1.9 Mapping and spec editing. Middle pane `MappingEditor`: CodeMirror JSON with schema
  validation and a tree summary (source path -> DMLex element), saving a new version; `SpecEditor`
  (Markdown) for the conversion spec. Approval-gated overwrite of exports. Acceptance: edit the
  mapping in the UI, re-run a sample, see the change.
- [ ] M1.10 Acceptance with a real model. Run the whole flow on `oxford.xml` with the owner's Claude
  model and on one more legacy export if provided; record results and prompt adjustments in
  `docs/DECISIONS.md` under Verification.

## 13. Later milestones (expand into a checklist in docs/PLAN.md when starting each)

### M1 XML end-to-end (expanded into section 12b)
Scope: document parse job (`documents`, `entries` tables, `LxmlXmlSource`), XML toolset (`xml_profile`, `xml_sample_entries`, `xml_search`, `mapping_write`, `mapping_explain`), `run_conversion` (sample synchronous up to 50 entries, full as a job with progress and approval), `validate_dmlex`, `diff_runs`, `run_stats`, `export`, `spec_read`, `spec_update`; DMLex model, writers, validator with vendored schemas; UI: entry navigator, `EntryView` side by side (source XML, DMLex XML/JSON, readable rendering), `DiffView`, `MappingTable` editor bound to MappingSpec v1, `SpecEditor`, specialised tool cards (profile summary, mapping proposal, sample before/after, validation report, job progress).
Acceptance: a real legacy XML dictionary becomes schema-valid DMLex XML and JSON through the chat, with at most manual edits in the mapping table; full run of 50k entries completes as a job with progress; export downloads.

### M2 PDF end-to-end
Scope: layout extraction job (`pdf_pages`, layout JSON in storage), `PdfViewer` overlays from `EntrySpan` sets, review mode (accept/reject, keyboard j/k/a/r), edit mode (draw, resize, relabel, split, merge), select-to-chat (drag region or text-layer selection, words inside become a context chip with page and bbox), PDF toolset (`pdf_analyze`, `pdf_get_page`, `pdf_segment`, `pdf_estimate_cost`, `pdf_structure` sample and full, `annotations_get`), `LlmStructurer` batch job with concurrency, caching and cost accounting, extracted-entry editing saved as user overrides, low-confidence review list.
Acceptance: a ~200-page born-digital PDF dictionary becomes DMLex with user corrections applied; the cost estimate shown before the batch is within 25% of the recorded usage.

### M3 Sandbox and custom steps
Scope: runner service and image per 7.12, `run_code` tool with approval, artifact preview in the middle pane, `save_as_step` and replay of custom steps inside full runs, `pdf_train_segmenter` with the trainable segmenter, SANDBOX_RUNTIME=runsc documented.
Acceptance: the agent writes a post-processing snippet for a quirky XML, the user approves, the output previews, the saved step runs inside the next full conversion; a snippet trying to open a network connection fails; a runaway snippet is killed at the timeout.

### M4 Hardening
Scope: conversation compaction, usage dashboards per user and project, project sharing, audit views, rate limits, backups documented and tested, SSO federation guide (SAML brokering, SATOSA proxy for eduGAIN-scale federations), optional push to Lexonomy, i18n scaffolding, OCR adapter hook.

## 14. Verification

- Every task: its acceptance check plus `make check`.
- Per milestone: the milestone acceptance run manually with a real key on at least three models (one via OpenRouter, one direct, one self-hosted OpenAI-compatible) to check tool calling and vision behaviour; results noted in `docs/DECISIONS.md` under a "Verification" heading with date.
- Scale checks (M1, M2): 100 MB XML paginates without loading the file into browser or API memory; 1,000-page PDF layout extraction completes in the worker with progress; sandbox limits enforced (M3).
- CI on every push: lint, types, unit and API tests against Postgres, web build, Playwright smoke with provider `test`.

## 15. Risks and fallbacks

- Approval streaming through the Pydantic AI adapter had an open issue (pydantic-ai #4279). M0.12 verifies it; the fallback is specified in 7.3.
- assistant-ui and AI SDK move fast; pin the versions that work together in M0.3 and upgrade deliberately.
- Model variance through OpenRouter: keep `model_registry.json` and the recommended-models list current; capability checks before PDF work.
- Scanned PDFs: out of scope until the OCR adapter; detect low text density in `pdf_analyze` and tell the user.
- Sandbox escape: only the runner holds the Docker socket; gVisor in production; no network; limits; audit log.
- Keycloak operations for small institutes: realm import, docs, single-container default.

## 16. References (read before touching each integration)

- Pydantic AI docs index: https://pydantic.dev/docs/ai/ (llms.txt available). Vercel AI adapter: https://pydantic.dev/docs/ai/integrations/ui/vercel-ai/ and API https://pydantic.dev/docs/ai/api/ui/vercel_ai/ . Deferred tools and approval: https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/ . OpenRouter: https://pydantic.dev/docs/ai/models/openrouter/ . Known approval issue: https://github.com/pydantic/pydantic-ai/issues/4279
- assistant-ui: https://www.assistant-ui.com/llms.txt , AI SDK runtime and approvals (`respondToApproval`): https://www.assistant-ui.com/docs/runtimes/ai-sdk/v6-legacy (explains the AI SDK 7 pairing), tool UI guide: https://www.assistant-ui.com/docs/guides/ToolUI
- AI SDK 7: https://vercel.com/changelog/ai-sdk-7 , migration guide https://ai-sdk.dev/docs/migration-guides/migration-guide-7-0 , tool usage https://ai-sdk.dev/docs/ai-sdk-ui/chatbot-tool-usage
- DMLex specification: https://docs.oasis-open.org/lexidma/dmlex/v1.0/dmlex-v1.0.html , schemas: https://github.com/oasis-tcs/lexidma
- Procrastinate: https://procrastinate.readthedocs.io/ . Garage: https://garagehq.deuxfleurs.fr/documentation/ . Keycloak realm import: https://www.keycloak.org/server/importExport . gVisor with Docker: https://gvisor.dev/docs/user_guide/quick_start/docker/
- PDF.js coordinates and highlights: https://www.nutrient.io/blog/pdfjs-text-highlight-annotations/ ; react-pdf custom layers: https://github.com/wojtekmaj/react-pdf/issues/479
- Background on rejected options: DeepSeek Harness https://github.com/deepseek-ai/deepseek-harness , Theia AI https://theia-ide.org/docs/user_ai/ , MinIO CE status https://www.glukhov.org/data-infrastructure/object-storage/minio-dead/
