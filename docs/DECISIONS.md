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

## 2026-10-27 Scripts (M1.2)

- `check_script` (AST only, nothing is executed in the app): `process` must be a top-level
  `def` (not `async def`) taking `(record, ctx)`; extra parameters need defaults. `setup`, if
  present, takes `(ctx)`. Every `.service(...)` call needs a literal string as its first
  argument; the names, in order of first use, are stored on the version and shown as badges
  (and later on the approval card). Errors name the line and what is wrong.
- Versions are numbered per workspace (`max + 1`); the unique constraint turns a concurrent
  double write into an error rather than two versions 3. Good enough for the POC.
- `GET .../scripts` lists summaries without code; `GET .../scripts/{n|latest}` has the code.
  A saved user edit is version n+1 with author `user`; the agent sees "written by the user" in
  its workspace block and in `script_read`.

## 2026-10-27 Runner package (M1.3)

- `runner/` depends on httpx and rapidfuzz only; Prefect is the optional extra `flow` (M2). uv
  locks extras too, so `uv.lock` carries Prefect 3.8.8 and its tree without installing it, and
  the shared resolution pins websockets to 16.1 for the whole workspace.
- Records are processed on a thread pool with a bounded window (`concurrency × 4` in flight);
  results are written as the oldest pending record finishes, so the file is in `_row` order
  without holding all results in memory.
- Per-service concurrency is a semaphore inside `ServiceClient`, shared by all threads, so a run
  with `concurrency=16` against a service limited to 3 never has more than 3 calls in flight.
- Retries apply to the service's `retry_statuses`, timeouts and transport errors, with backoff
  `backoff_s × 2^n`; other 4xx/5xx fail the record at once. Errors read
  `ServiceError: 502 from echolot: <body excerpt> (after 3 attempts)`. Keys are only put in
  headers; `services.toml` rejects unknown keys, so an inline `api_key` is refused.
- `ctx.service(name)` for a name outside the run's services fails that record; a run service
  missing from the host's `services.toml`, or its key variable unset, fails the run before any
  record (no partial work against a misconfigured host).
- A record error keeps `type: message` in `_error`; the first 20 tracebacks go to the run log.
  `process` must return a dict without `_` keys, otherwise that record fails.
- Test packages: the runner's are in `runner/runner_tests/`, because two packages named
  `tests` clash in one pytest session.

## 2026-10-27 Executors and the runs API (M1.4)

- `ExecState.kind` uses Prefect's state type names for both executors, so the mapping of PLAN 7.5
  lives once (`runs.executor.app_status`). The LocalExecutor reports RUNNING / CANCELLING /
  COMPLETED / FAILED / CANCELLED from its subprocess, and CRASHED for a run it does not know (the
  app restarted).
- The LocalExecutor runs `python -m echolot_runner.cli params.json` in `data/runner/<run id>/`
  (params file mode 0600, it holds the token), with the app's environment and
  `RUNNER_SERVICES_FILE=ECHO_LOCAL_SERVICES_FILE` (default `./services.toml`, gitignored). The
  failure message is the CLI's "run failed: …" line, else the stderr tail.
- A run is committed before it is submitted (the runner may ask for its input at once). The
  first progress post moves a queued run to running; the poller never moves a run back to
  queued (Prefect can still say PENDING for a moment).
- The runner endpoints answer 404 for an unknown run, a wrong token and a finished run alike.
- The uploaded results are stored as a `result` file and normalized like an input, so they open
  as records too; the counts (`total`, `ok`, `error`) are taken from the file, not from the
  runner's last progress. COMPLETED without a result file is a failed run.
- Rows join each result line with its input record; the CSV export is input columns, output
  columns (renamed `<name>_out` on a clash), `status`, `error`, nested values as JSON, built by
  merging the two row-ordered streams.
- Retry-failed creates a new run over the failed rows with the latest script and the parent's
  concurrency and params (`selection = {"rows": [...]}`, `parent_run_id`).
- Tested over real HTTP: uvicorn on a free port inside the test, a fake service on another, the
  real runner subprocess: sample, full run with one failing record, rows, error filter, CSV and
  JSONL export, retry, cancel of a slow run, a failing setup, token checks.

## 2026-10-27 Run tools and prompt (M1.5)

- `submit_run` gained an optional `script_version` (PLAN 7.9 updated): the approval card shows
  which version will run, and the agent can pin the one it just wrote. Without it the latest
  version at approval time runs.
- A sample is a run with `limit` ≤ 25: the tool waits up to 120 s (polling the executor itself,
  not only the background poller) and returns the first 20 rows with input and output, values
  clipped. Larger runs return at once with an `open_run` ui-action.
- Run and service logic moved out of the router (`runs.service.start_run`,
  `retry_failed_run`) so the API and the tools share it; `AgentDeps` carries the executor.
- Service docs are read from `ECHO_SERVICES_DIR` (default `./services`) on every call, so a doc
  edited on disk is used at once; `README.md` there is not a service.
- The canned test model does not see `submit_run` and `retry_failed` (it calls every tool it
  sees; one demo approval per turn keeps the approval tests and the e2e meaningful). Real models
  and the FunctionModel tests see them.
- The system prompt is filled with a plain `replace("{workspace_block}", …)`: its code examples
  contain braces, which `str.format` would take for fields.
- Tool tests run against the live app (real port, real runner subprocess); a helper approves or
  denies the gated call through `DeferredToolResults`. A denied `submit_run` creates no run.

## 2026-10-27 Run UI (M1.6)

- The run list and an open run refetch every 2 s while unfinished and stop once finished; the
  run's status is part of the rows query key, so results appear when it turns done.
- The results table shows `#`, status, the output columns and the error first, then the input
  columns muted: the walk-through showed four input columns (a long URI first) pushing the
  outputs out of view.
- "Run 20" / "Run all" in the script editor start a run of the shown version on the chosen
  input file (a user action: no approval card); they are disabled while the editor has
  unsaved changes.
- Bug found by the e2e run: with a relative `ECHO_DATA_DIR` the LocalExecutor gave the runner a
  relative params path, but the subprocess starts in its own directory. The executor now
  resolves its root (regression test `test_local_executor.py`).
- Walk-through with a fake reconciler on :5099 (`services.toml` and a service doc in a scratch
  directory): a script calling `ctx.service("fakerecon")` ran on 20 records from the UI, 19 ok
  and 1 error (the fake's 502 after two attempts), with Retry 1 failed, CSV and JSONL offered.

## 2026-10-27 Flow and serve (M2.1)

- `echolot_runner.flow.run_script` (flow `run-script`, `persist_result=False`, no retries) calls
  the harness; the harness's `echolot_runner` log lines are forwarded to the flow run's logger.
  `python -m echolot_runner.serve` serves deployment `default` with `limit=RUNNER_MAX_RUNS`
  (default 4).
- Prefect 3.8.8 is in the root dev group (type checks, tests) and the runner's `flow` extra; the
  app does not import it. Its client side (`serve`, the runner) imports without the server API.
- Prefect 3.8.8's server cannot start with FastAPI ≥ 0.150 (`Body(min_items=...)`), and the
  workspace has FastAPI 0.162 for the app. So the flow is unit-tested through `run_script.fn`
  (no server, no run context), and a dev Prefect server runs in its own environment:
  `uvx --from 'prefect==3.8.8' --with 'fastapi<0.150' prefect server start` (docs/runner-host.md).
- Acceptance: with that server, `serve` from the workspace venv and a stand-in for the app's
  runner endpoints, a flow run created through the REST API with hand-written params completed in
  about 4 s (`PREFECT_RUNNER_POLL_FREQUENCY=2`) and uploaded 50 results.
- `~/.prefect` on the dev machine predates this work (ECHOLOT); the tests and the checks here ran
  with `PREFECT_HOME` in a scratch directory where a server was involved.

## 2026-10-27 Prefect executor (M2.2)

- `PrefectExecutor` (httpx, no Prefect import): deployment id looked up once by name and cached
  (forgotten on a 404 when creating a flow run), flow runs named `echolot-<run id prefix>` and
  tagged `echolot`, cancel as `set_state` CANCELLING (the serving runner then kills the
  process and reports CANCELLED). Basic auth as Prefect's client sends it:
  `Authorization: Basic base64(auth_string)`.
- Prefect wraps a flow's exception ("Flow run encountered an exception: RunFailed: …"); the
  prefix is stripped so the run's error reads in the runner's words. A flow run that no longer
  exists reports CRASHED; an unreachable server or a refused request raises `ExecutorError`,
  which the poller logs and retries on its next pass.
- The live tests switch to the Prefect executor with `ECHO_TEST_PREFECT_API_URL`; the fake
  service then listens on `ECHO_TEST_FAKE_SERVICE_PORT`, which the runner's `services.toml`
  names. Against a local Prefect 3.8.8 server with `serve` (poll frequency 1 s), the run tests
  (5, the token test skipped: it reads the local executor's params file) and the run-tool tests
  (6) pass: sample, full run with a failing record, rows, export, retry, cancel, setup failure.
- Regression caught on the way: moving the live fixture into `conftest.py` in M1.5 had cut the
  main run scenario test out of `test_runs.py` (make check stayed green with five tests). It is
  restored from `da9606a`, and the test lists of all test files were compared with M1.4.

## 2026-10-27 Live acceptance with Prefect and ECHOLOT (M2.3)

- On this machine: the app (`ECHO_EXECUTOR=prefect`, test model), a Prefect 3.8.8 server, the
  runner serving `run-script/default` (`PREFECT_RUNNER_POLL_FREQUENCY=2`) with `services.toml`
  pointing at ECHOLOT's standalone API on 127.0.0.1:5000. The key was loaded from ECHOLOT's
  `.env` into the runner's environment by the shell that started it, never printed.
- Data: 200 rows from 40 well-known people (mostly Basque art and letters) with a context
  sentence each; row 57 has an empty name, which the script turns into a record error.
- Script: strip `"` and `\` from the name, POST /reconcile, keep top QID, title, score, the
  number of candidates and the candidates.
- Results:
  - A 10-record sample went from submit to done in 5.7 s (runner poll 2 s, app poll 1 s).
  - The 200-record run took 53 s at concurrency 4, with progress in 22 steps; 199 ok and 1
    error (row 57, "ValueError: the record has no name"); the export had 201 lines.
  - Cancel after 11 records: CANCELLED about 3 s later. The Prefect server lists the runs as
    `echolot-<id>` with their states.
- Finding about the service, not the app: 90 of the 199 ok records got no candidate. Direct
  calls confirm it: Eduardo Chillida returns `[]` with and without a context sentence, Kirmen
  Uribe matches "Uribe (surname)" at 0.38, Albert Einstein matches Q937 at 0.98. Wikifier's
  `pageRankSqThreshold=0.8` seems to drop entities that are well known but not globally
  prominent. This goes into `services/echolot.md` (M3.1) and is the owner's to judge.

## 2026-10-27 The ECHOLOT service doc (M3.1, acceptance pending)

- `services/echolot.md` is written for the agent from the owner's context note and the M2.3
  findings: the entity and match shapes, the errors and which ones mean "stop", what the score
  does and does not mean, `matches: []` as a normal outcome, English only, the measured coverage
  gap, stripping `"` and `\`, and an example script that returns `top_qid`, `top_title`,
  `top_score`, `verdict` (agree, conflict, new, none from an existing Wikidata id) and the full
  `matches` list.
- `tests/test_service_docs.py` keeps the shipped docs honest: they parse, and the example script
  passes `check_script` and gives the four verdicts against a fake ECHOLOT through the real
  harness.
- The checklist box stays open until a real model has written a working script from the doc
  alone (needs a model key).

## 2026-10-27 Result cells (M3.2)

- `CellValue` renders every records and results cell: a list of objects with
  `wikidata_item_id` (or `title` and `url`) as up to three ranked candidates, each with the QID
  linked to wikidata.org, the title linked to its Wikipedia page and the score with a tooltip
  saying it ranks candidates and is not a certainty; "+N more" beyond three, the full list in
  the cell's tooltip. QID strings and http(s) URLs become links; empty lists read "none"; other
  values are compact JSON. A click on a link does not select the row.
- Checked on the 200-record ECHOLOT run of M2.3. The review also surfaced high-score matches of
  the wrong person (Rafael Ruiz Balerdi → Leonardo Balerdi 0.93, Aurelio Arteta → Mikel Arteta
  0.91), now in `services/echolot.md` so the agent flags them.

## 2026-10-27 Decisions per row (M3.3)

- A decision is `{"qid": "Q…"}` (a chosen candidate) or `{"qid": null}` ("none of these"),
  optionally with a note, stored per run and row (`run_decisions`); DELETE clears it. Only rows
  present in the run's results can be decided.
- Rows carry their decision; the rows endpoint filters on `decided` and on an output field's
  value (`field` + `equals`, e.g. the example script's `verdict`). The run view offers "not
  decided yet", "decided" and, when the output has a `verdict` column, one filter per verdict.
- The decision selector appears when an output column holds candidates (detected from the
  values, else a column named `matches`) and lists the row's candidates with a QID, plus "none of
  these".
- The CSV export gets a last `decision` column (the QID, `none`, or empty) as soon as the run has
  any decision; the JSONL export carries the decision object. `run_results` shows decisions to
  the agent.
- Checked on the 200-record ECHOLOT run of M2.3: "none of these" for row 6 (the wrong Balerdi)
  and Q220980 for row 12 (Pío Baroja) are in the "decided" filter and in the export. The check
  also caught a crash when an older API sent rows without a `decision` field; the selector now
  treats a missing decision as undecided.

## 2026-10-28 Service keys for the LocalExecutor (demo preparation)

- The app's `.env` is read into its settings, not into its process environment, so a service key
  put there never reached the runner subprocess: every run would fail with "needs its key in the
  environment variable …". The LocalExecutor now adds the `KEY=VALUE` lines of
  `ECHO_LOCAL_RUNNER_ENV_FILE` (default `./runner.env`, gitignored, read at every submit) to the
  subprocess's environment. Exporting the key in the shell that starts the app still works.
- On the Prefect runner host nothing changes: keys are in the runner's own environment
  (docs/runner-host.md).
