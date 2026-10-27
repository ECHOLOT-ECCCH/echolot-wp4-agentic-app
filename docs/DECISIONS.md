# Decisions

Newest at the bottom. Each entry: date, what was decided, why. Elexifier's log is in
`docs/archive/elexifier/DECISIONS.md`.

## 2026-10-27 The repository becomes a lightweight agentic workbench for ECHOLOT

- The Elexifier app (dictionary conversion to DMLex, last commit `98999b1`) is cut down to a POC
  for per-record tasks over datasets: entity reconciliation through the ECHOLOT service first,
  enrichment and similar tasks later.
- The workflow is: files and preview, a chat that agrees what to do with each record, a Python
  script the agent writes (`process(record, ctx)`), runs on a Prefect deployment next to the
  services, and results to review and export. Task knowledge lives in service docs and scripts,
  not in the platform.
- Kept from Elexifier: the Pydantic AI runtime and `VercelAIAdapter` streaming, UI-message
  persistence, the approval flow, the tool result contract, the assistant-ui chat, the
  three-pane shell and the file upload.
- Dropped: XML/PDF/DMLex pipeline, Keycloak and sessions, bring-your-own-key credentials and the
  model catalog, Postgres, Procrastinate, NOTIFY/SSE, Garage, Caddy, the Docker sandbox runner
  and custom steps, and the deployment bundle.
- Execution moves to Prefect because ECHOLOT already uses it, the services are reachable from
  its host (ECHOLOT binds to 127.0.0.1:5000), and the service keys can stay there. The app talks
  to the Prefect REST API with httpx, verified against Prefect 3.7.5.dev5:
  - `/deployments/name/{flow}/{deployment}`, `/deployments/{id}/create_flow_run`,
    `/flow_runs/{id}`, `/flow_runs/{id}/set_state`.
  - Basic auth through `PREFECT_API_AUTH_STRING` / `PREFECT_SERVER_API_AUTH_STRING`.
  - `PREFECT_RUNNER_POLL_FREQUENCY` defaults to 10 s.
- Prefect parameters carry the script and URLs, not data. The runner pulls input and pushes
  progress and results to the app with a per-run token.
- Accepted for the POC: scripts run unsandboxed on the runner host after approval, and the app
  has no login (it binds to 127.0.0.1). Hardening is listed in PLAN section 14.

## 2026-10-27 Server skeleton (M0.2)

- Installed: FastAPI 0.162, SQLAlchemy 2.1.6, aiosqlite 0.22.1, pydantic 2.14, pydantic-settings
  2.16, uvicorn 0.54, structlog 26.1.
- SQLite pragmas are set on every connection: WAL, `busy_timeout=5000`, `foreign_keys=ON` (the
  cascades in `db.py` depend on it). Timestamps go through `UTCDateTime`, which stores naive UTC
  and returns aware UTC; defaults are set in Python (`utcnow`), not as server defaults.
- `runs.limit` is called `record_limit` (`limit` is an SQL keyword); `runs` also stores the
  `purpose` shown on the approval card.
- Storage keeps only `LocalStorage`, rooted at `ECHO_DATA_DIR` next to `app.db`; keys start with
  `workspaces/`. The Storage protocol and S3 backend can come back from `98999b1` with the
  object-store data plane.
- CI is cut to the check job until M0.6 (the old e2e and image publishing referenced deleted
  files).
- Dev machine: Node 22 installed with nvm (`nvm use 22`); the default Node there is 20.

## 2026-10-27 Records (M0.3)

- Uploads are raw request bodies only (`?name=` or `X-File-Name`), as the web app and
  `curl --data-binary` send them; the multipart path of the old router is dropped.
- CSV: the encoding is sniffed from the first 64 KB (BOM → `utf-8-sig`, valid UTF-8 → `utf-8`,
  otherwise `latin-1`, which never fails); the delimiter from `,;\t|` with `csv.Sniffer`; the
  first row is always the header. Empty and duplicate header names become `column_N` / `name_2`.
  Rows shorter than the header are filled with empty strings; longer rows add `column_N` keys.
  Both are counted in `meta.ragged_rows` with the first line numbers. Blank rows are skipped.
- JSON: a list of objects, or an object holding exactly one list (ECHOLOT's `{"entities": [...]}`),
  parsed whole up to 200 MB. JSONL streams; lines that are not objects are counted and skipped.
- A file that cannot be read as records is still stored, with `meta.format = null` and
  `meta.error`.
- The runner's input line is `{"_row": n, "record": {...}}` (PLAN 7.3 updated).
- Measured: a 100 MB, 678,651-row CSV uploads and normalizes in 3.4 s with server RSS flat at
  74–76 MB; a page at offset 1,000 answers in 21 ms.

## 2026-10-27 Agent and chat (M0.4)

- pydantic-ai-slim 2.62 (Elexifier ran 2.43). `VercelAIAdapter(sdk_version=7)`, `from_request`,
  `build_run_input`, `dump_messages(..., sdk_version=7)` and `run_stream(on_complete=...)` are
  unchanged, so `runtime.py` is lifted as is; the approval tests pass unchanged in substance.
- The model comes from `ECHO_MODEL_*` (`model_from_settings`); a provider that cannot be built
  answers 400 naming the settings. No per-turn model override, no usage records.
- `ui_messages.py` keeps normalization, `restore_attachments` and the viewing note; the PDF
  region and entry context chips are gone. Viewing kinds: file (with an optional row), task,
  script; run follows in M1.4.
- The workspace block in the instructions lists up to 20 input files with their record counts
  and file ids, and whether task notes exist, so the model rarely needs `list_files` first.
- Tool tests drive the real agent with a scripted `FunctionModel` (`tests/conftest.call_tool`).

## 2026-10-27 Web prune (M0.5)

- Removed: the PDF, entries, mapping, runs, steps, settings and auth features, the model picker,
  the PDF viewer, context chips, the SSE client and the XML tool bodies; dependencies react-pdf,
  pdfjs-dist, `@codemirror/lang-xml` and `@codemirror/merge`. The web package is `@echolot/web`.
- Routes: `/` (workspaces) and `/w/$workspaceId`. The preview store's targets are file (with an
  optional row), task, script and run, matching the server's ui-actions.
- Records open in a paged table (50 rows a page, go to row); clicking a row selects it, so the
  "[Looking at: …]" note names the row. A row the agent opens brings its page into view (state
  adjusted during render, which the linter accepts, instead of an effect).
- The task notes editor is the old spec editor on `GET/PUT /api/workspaces/{id}/task`; it is
  wired now, and M1.1 adds the agent's tools for it.
- Bug found in the walk-through: a conversation created by POST could answer 404 on the
  immediate GET of its messages, because the session dependency committed after the response
  was sent. `DbSession` now uses `Depends(get_db, scope="function")` (FastAPI 0.162), which
  commits before the response goes out.
- Walk-through (Playwright script against uvicorn + Vite, test model): create a workspace,
  upload a 120-row CSV, page to rows 50–99, select row 57, ask "What files do I have?", allow
  the demo tool, the turn completes; the stored message carries
  "[Looking at: row 57 of the file people.csv (file_id …)]".

## 2026-10-27 e2e and CI (M0.6)

- The Playwright config starts the API itself with the test model and SQLite under `data/e2e`
  (gitignored), and Vite; no login state, no Keycloak. Fixture: `e2e/fixtures/people.csv`
  (120 rows).
- Smoke tests: browse records (paging, go to row, select a row); chat with the test model,
  allow the demo tool, the conversation survives a reload; write and save the task notes.
  CodeMirror puts its aria-label on the wrapper, so the tests click `.cm-content` inside it.
- CI: the check job (no services) and the e2e job (Chromium with deps). Not run yet: the
  workflow only runs on GitHub, and nothing has been pushed.

## 2026-10-27 Task notes for the agent (M1.1)

- One text per workspace (`workspaces.task_md`), no versions: scripts are versioned and a run
  records its script, which is what reproducibility needs.
- Notes up to 4,000 characters go into the instructions block every turn, quoted, so a user's
  edit is in the model's context on the next turn without a tool call; longer notes are named
  and read with `task_read`.
- Without SSE, the chat refetches what changed: on each `data-ui-action` the target's query
  (task notes, files), and at the end of a turn the files and task notes.
- The editor keeps a draft only while the user is editing; a newer stored version (the agent
  wrote meanwhile) replaces the text when there is no draft, otherwise the header says so and
  offers "Discard mine".
