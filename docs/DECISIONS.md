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

## 2026-10-27 Service keys for the LocalExecutor (demo preparation)

- The app's `.env` is read into its settings, not into its process environment, so a service key
  put there never reached the runner subprocess: every run would fail with "needs its key in the
  environment variable …". The LocalExecutor now adds the `KEY=VALUE` lines of
  `ECHO_LOCAL_RUNNER_ENV_FILE` (default `./runner.env`, gitignored, read at every submit) to the
  subprocess's environment. Exporting the key in the shell that starts the app still works.
- On the Prefect runner host nothing changes: keys are in the runner's own environment
  (docs/runner-host.md).

## 2026-10-27 Dry run with a real model (demo preparation)

- Model: Claude Sonnet 5.5 through OpenRouter (`anthropic/claude-sonnet-5.5`); the Anthropic
  account had no API credit. Data: `demo/people-demo.csv`; services: the local executor and the
  real ECHOLOT.
- Five turns, driven like the browser (approval cards allowed): the agent profiled and sampled
  the file, read the ECHOLOT doc, wrote task notes, asked one question, wrote a readable script
  (accent-insensitive title check with rapidfuzz, threshold in `ctx.params`), ran an approved
  sample of 10 and the full 20, and reported honestly: 2 agree, 1 conflict, 13 new, 3 none, with
  Oteiza and Ruiz Balerdi flagged as wrong people. It noticed on its own that the Goya row's
  existing Q5593 is Picasso's QID. A second script version (retry with a shorter context) found
  nothing better, which it recorded in the task notes but hardly mentioned in the chat.
- Fixes from it: `run_status` takes `wait_s` (up to 60 s), because the agent polled it four
  times while a 20-record run went on; the prompt says to follow a run with one waiting call.
  The decision select has a fixed width (it varied per row).
- The conversation, tool groups, run view, candidate links and decisions rendered without
  browser errors.

## 2026-10-27 Usage screenshots

- `screenshots/` holds 15 step-by-step screenshots with a README, recorded against the app with
  Claude Sonnet 5.5 (OpenRouter), the local executor and `demo/people-demo.csv`;
  `apps/web/scripts/usage-screenshots.mjs` records them again (the agent's words vary per run).
- Recording them found a bug: a run started from the script view finished without its result file
  appearing under Results until the next agent turn. The run queries now refetch the file list
  when a run they follow finishes.
- Two demo-preparation entries above were dated 2026-10-28 by mistake; corrected to 2026-10-27.

## 2026-10-28 Repositories moved to the ECHOLOT-ECCCH organization

- This app: https://github.com/ECHOLOT-ECCCH/echolot-wp4-agentic-app, a copy of
  `pzajec/echolot-agentic-app` with its full history (`main`, the only branch). Issues and PRs
  were not copied.
- The ECHOLOT service: https://github.com/ECHOLOT-ECCCH/echolot-wp4-api-service, from
  `pzajec/ECHOLOT-reconciliation`. The uncommitted `/enrich` work was committed on `api-service`,
  which was then fast-forwarded into `main`; `main` is the branch to deploy, and PLAN 16 points
  there.
- The old repositories stay with their owner, to be archived.

## 2026-11-09 Accounts and bring-your-own model keys (M4.1, M4.2; branch `feat/users-and-byok`)

The owner asked for login and for users to bring their own keys and pick among model vendors,
"nice and simple, but robust enough to deploy for real testing with real users". Both existed in
Elexifier (`6ed7527` OIDC login with Keycloak, `84b1b04` credentials and catalogs) and were cut
for the demo. PLAN section 3 (Auth, Model) and the new contract 7.11 change with this entry.

- **Local accounts, not OIDC.** Elexifier's login needed a Keycloak next to the app; that is the
  part that made it heavy. Accounts here are e-mail and password, made by an admin (no sign-up:
  testers are invited), stored in the app's SQLite. Single sign-on stays possible later
  (BACKLOG). Passwords: stdlib scrypt with OWASP's N=2^15, r=8, p=3 (no new dependency),
  verified in a thread; unknown addresses are checked against a dummy hash so they take as long
  as a wrong password; at least 12 characters (between NIST's 8 and its 15 for password-only
  sign-in, as testers get a generated one first).
- **Server-side sessions** rather than signed cookies, so sign-out, a password change and
  disabling an account end sessions at once. The cookie holds a random token; the table holds
  its SHA-256. Expiry slides by use, written at most hourly.
- **CSRF by a required header** (`X-Echolot-Request`) on unsafe `/api/` requests, on top of
  SameSite=Lax: a page elsewhere cannot add the header without a CORS preflight that this app
  never answers. Simpler than Elexifier's double-submit token: no token to fetch or rotate. Done
  as pure ASGI middleware so the streamed chat and uploads pass untouched.
- **Throttling in memory** per e-mail and per client address. One process (PLAN section 3);
  a restart forgets the counts, acceptable for a pilot.
- **Workspaces are private.** Owner column, checked by a router dependency on every router under
  `/api/workspaces/{workspace_id}`, so a route that never loads the workspace is covered too.
  The test enumerates the routes from the OpenAPI schema (FastAPI 0.162 nests included routers,
  so `app.routes` does not list them) and expects 404 for another user and 401 for nobody. The
  workspace's own routes take the workspace as a dependency, so ownership is checked before the
  body is validated. Admins see only their own workspaces; nothing is shared yet.
- **Old databases.** No Alembic yet: `create_all` makes the new tables, and a short list of
  added columns is applied with `ALTER TABLE` at startup (`workspaces.owner_id`). Workspaces
  without an owner go to the first admin created.
- **One key per vendor per user**, Fernet-encrypted (`cryptography`, now a direct dependency)
  with `ECHO_SECRET_KEY` or a generated `<data_dir>/secret.key` (0600). Labelled multiple
  credentials per vendor (Elexifier) were dropped as more than testers need.
- **Vendors**: Anthropic, OpenAI, Google, Mistral, OpenRouter, DeepSeek. Mistral is new (an
  EU vendor, relevant to an EU project); it goes through `OpenAIChatModel` on
  `https://api.mistral.ai/v1`, because pydantic-ai's own Mistral provider needs the `mistralai`
  package. `openai_compatible` for users is off unless `ECHO_ALLOW_CUSTOM_ENDPOINTS`: a
  user-entered base URL lets a signed-in user make the server call internal addresses (SSRF);
  the operator's own `ECHO_MODEL_BASE_URL` is unaffected.
- **No silent fallback.** The SDKs read e.g. `OPENAI_API_KEY` when given no key; `build_model`
  now refuses a vendor that needs a key without one, so a user without a key never runs on a
  key in the server's environment.
- **The model choice is per user**, not per conversation or workspace: one place to look, and
  the agent run records which model and whose key served each turn. The server's
  `ECHO_MODEL_*` model is offered as "Server default" only when it is usable; with no key it is
  hidden and the chat answers 409 "add a key".
- **Model lists** come live from each vendor with the user's key (Elexifier's catalog, slimmed:
  no prices or capabilities), suggested models first, non-chat models (embeddings, speech,
  images) dropped, cached an hour per key; on failure the suggestions come back with the reason,
  and any id can be typed. A "Test" button sends one tiny request; its errors are shown with
  anything key-like masked.
- **Web.** `/login` and an `AuthGuard` around the rest; any 401 (API, upload, chat stream) goes
  to sign-in and back to the same page (`next` only accepts paths on this site). Settings has
  model keys, account and (for admins) users; a new user's temporary password is shown once.
  The model picker sits in the chat header. The chat transport now turns a refused turn's JSON
  body into its detail, which assistant-ui shows inline.
- Checked: `make check` (145 Python and 30 web tests) and `make e2e` (5 smoke tests, signing in
  with an account `playwright.config.ts` creates through `echolot-admin`). In a real browser:
  sign-in, the settings tabs, a Mistral key with a fake value (the vendor refused the listing,
  the suggestions appeared with the reason), a new user's temporary password, the picker in the
  chat header, and a turn refused for lack of a model showing the plain reason.
- Not done (M4.3): a pilot behind https with the owner.

## 2026-11-09 Docker Compose deployment (M4.3, first part)

The owner asked to prepare the app for docker-compose deployment.

- **Scripts do not run in the app's container by default.** With the local executor a script is
  a subprocess of the app, as the same user: it can read `/proc/1/environ` (the secret key, the
  server's model key) and `app.db` (the users' encrypted keys), so one tester's script could
  decrypt everyone's keys. The compose file therefore bundles what PLAN section 3 always meant
  runs to use: a Prefect server and a runner container (profile `runner`, on by default in
  `.env`), with no app volume and no app secret. A script reaches the app only with its run's
  token. The local executor stays available as `docker-compose.local-runs.yml`, documented as
  trusted users only. Independently, the local executor now drops the app's `ECHO_*` variables
  from the subprocess's environment (variables from `runner.env` still pass); that keeps
  secrets out of `os.environ` but is not isolation.
- **Service keys stay out of the app container** in the default stack: `config/` is mounted
  into the runner only (and into the app only by the local-runs override).
- **Caddy in front, the single-page app inside its image**, as Elexifier's `deploy/compose` did:
  automatic https for a domain, or `:80` behind an institution's proxy. Caddy proxies `/api`,
  `/runner` and `/healthz` and sends one `X-Forwarded-For` value, `{client_ip}`, which already
  honours `trusted_proxies`. uvicorn trusts it (`FORWARDED_ALLOW_IPS=*`, safe because the app
  publishes no port) and, in 0.54, takes the first value, so a client cannot choose its own
  address for sign-in throttling. Static files are compressed; the API is not, so the chat
  stream is passed on chunk by chunk (`flush_interval -1`). Security headers (nosniff, frame
  denial, referrer policy, HSTS) and caching (hashed assets immutable, the page revalidated)
  are set there.
- **One Dockerfile, three targets** (`web`, `app`, `runner`) from the repository root, with an
  allowlist `.dockerignore` so `deploy/.env` and `deploy/config/` never reach a build. The
  Python images install the locked dependencies first (cached), then the workspace packages
  non-editable; the app image has no Prefect and no dev tools, the runner image no app code.
  Both recipes were run on this machine with uv 0.9.5 (the image's) into fresh environments:
  the app imports and has `echolot-admin`, the runner has Prefect 3.8.8, neither has the other.
  Processes run as uid 10001.
- **The Prefect server is the official `prefecthq/prefect:3.8.8-python3.12` image**, with basic
  auth (`PREFECT_AUTH_STRING`, generated by `init.sh`, shared by app, server and runner). Our
  own environment cannot serve it: the workspace lock's FastAPI 0.162 breaks Prefect 3.8.8's
  server (docs/runner-host.md); the client side used by the runner is fine with it.
- **Compose features stay within v2.3** (this machine's): profiles, health conditions,
  `host-gateway`. `docker compose config` renders the default stack, the local-runs override
  and the smoke stack here, and refuses to start without `ECHO_SECRET_KEY` or
  `PREFECT_AUTH_STRING`. The three file paths (`APP_ENV_FILE`, `RUNNER_ENV_FILE`,
  `SERVICES_FILE`) are variables so the smoke test can use throwaway files.
- **Checked without Docker** (this account cannot reach the daemon, and downloading a Caddy
  binary to validate the Caddyfile was not allowed): the app and the runner from the
  image-equivalent environments, with the compose file's settings, against a Prefect 3.8.8
  server with basic auth; `smoke_check.py --api-only` passed: sign-in and the CSRF refusal, a
  streamed chat turn, a 3-record run through Prefect and the runner with the right results,
  sign-out. Neither log contained the secret key, the Prefect credentials or the password. The
  web stage was built from the allowlisted files alone. Not run here: the images, Caddy and the
  compose stack itself; CI's new `compose` job runs `deploy/smoke.sh`, which does all of it
  through Caddy, on the first push.


## 2026-11-09 An external Prefect server (M4.3)

The owner: "prefect will be running on some external address". The Compose deployment now
assumes that, and keeps the bundled server as an option.

- **The default stack is web and app.** The app sends runs to `ECHO_PREFECT_API_URL`; the runner
  serves the flow on that server, normally next to ECHOLOT on a host of its own
  (docs/runner-host.md, natively or with the runner image on the host's network, so ECHOLOT
  can stay on 127.0.0.1). `ECHO_PUBLIC_URL` defaults in `.env.example` to the site's own
  address, which that runner reaches through Caddy's `/runner/*`.
- **Options, by file rather than by profile.** `COMPOSE_PROFILES=runner` adds the runner to the
  stack, with the app's Prefect settings. `docker-compose.prefect.yml` adds a Prefect server
  of the stack's own and points the app and the runner at it (`init.sh --with-prefect` writes
  the `.env` for it; `smoke.sh` and CI use it). A separate file, because a runner depending on
  a server in an inactive profile is an error before Compose 2.20 (`required: false`).
- **One name per setting.** `.env` uses the app's names (`ECHO_PREFECT_API_URL`,
  `ECHO_PREFECT_AUTH_STRING`, `ECHO_PREFECT_API_KEY`), which reach the app through `env_file`;
  Compose maps them to the runner's `PREFECT_API_*` and the bundled server's
  `PREFECT_SERVER_API_AUTH_STRING`. `PREFECT_AUTH_STRING` is gone (the branch is unreleased).
  Empty values are safe: Prefect's client sends no header for an empty key or auth string
  (checked with 3.8.8).
- **An API key, too.** The executor sends `Bearer <key>` when only `ECHO_PREFECT_API_KEY` is set
  (Prefect Cloud, or a proxy with tokens), with basic auth first as in Prefect's client. A 403
  now reads as a refusal like a 401. This extends PLAN 7.6; the routes are unchanged.
- **The runner's Prefect follows the server.** `PREFECT_VERSION` (build argument; in `.env` for
  Compose) installs that version over uv.lock's 3.8.8 in the runner image; checked here with
  3.4.0 in an environment built as the image's: the runner imports and serves. The bundled
  server's image takes the same variable.
- **`echolot-admin check-runs`**, because we cannot test the owner's server from here: where
  runs go (the URL without credentials, the auth kind), then the server's version from
  `/admin/version` (or, when a server does not report it, that `/deployments/filter` answers
  like Prefect, which catches a URL without `/api`), the deployment, and its `status`, which
  Prefect sets to `READY` while a runner polls and `NOT_READY` 60 s after the last poll. A note
  says who can reach `ECHO_PUBLIC_URL` when it is a loopback or a one-label (Docker) name.
  `smoke.sh` waits for it to pass before the end-to-end check. The app also logs the Prefect
  URL and `ECHO_PUBLIC_URL` at start.
- **Checked against a real Prefect 3.8.8 server with basic auth** (uvx, as in M2.3): no runner →
  "no deployment"; wrong password and an API key against basic auth → refused (401); the URL
  without `/api` → "no Prefect API answers"; nothing listening → not reachable; with
  `echolot_runner.serve` running → all ok at the first try; 65 s after stopping it → "no runner
  has polled it lately". Credentials in the URL are not printed. `docker compose config`
  renders the default stack, the runner profile, the bundled override (and refuses it without
  an auth string) and the local-runs override; `init.sh` with and without `--with-prefect`
  writes a `.env` that Compose reads as intended. The images and the stack are still unbuilt
  here (no Docker daemon); CI's `compose` job covers the bundled variant.

## 2026-11-10 ECHOLOT's three reconciliation algorithms

ECHOLOT's `POST /reconcile` takes `?algorithm=wikifier|llm_based|rule_based` (default
`wikifier`), and that is its only per-request choice. The owner passed on the service's own
contract for dashboards; this app follows it in four places. Prefect is left as it was.

- **The agent's doc (`services/echolot.md`)** now describes the three: what each reads (for
  `rule_based` only `name`, `entity_type == "person"` and `information.gender`), what its
  matches look like (Wikipedia URLs for `wikifier`, Wikidata or ULAN URLs for the others), what
  its score means (`llm_based`: always 1.0, no confidence), and its traps: an empty reply from
  `llm_based` can be a failed Wikidata lookup, `rule_based` only knows its table and can change
  answers after a restart, scores are not comparable, agreement is by `wikidata_item_id`. A 503
  naming the algorithm means "not configured here", not "retry". The example script takes the
  algorithm from `ctx.params`, so the user switches between runs without a new script; a second
  example compares the algorithms per record (one column per algorithm, an `agreement` column).
  Both are run against a fake ECHOLOT in `test_service_docs.py`.
- **Matches are tagged with their algorithm** by the scripts (`{**m, "algorithm": a}`), because
  the reply's `algorithm` is outside the match list the review reads. The review then shows
  `llm_based`'s pick as "selected" instead of 1.00, names the algorithm in a list that mixes
  them, and the decision select offers every candidate column's QIDs once, with who proposed
  each ("wikifier 0.98, llm_based selected"). The server stores any QID, so nothing changed
  there.
- **The runner** gets `post(path, json, params)` (PLAN 7.1; a query in the path also works) and
  `concurrency_for` in `services.toml`: a lower limit for requests with a given query value,
  `{ "algorithm=llm_based" = 2 }` in the examples, because each such call makes six Wikidata
  requests and Wikidata's rate limit shows up as silent "no match". The narrower slot is taken
  before the service's, in a fixed order. In the runner, not in the script or the run's
  thread count, so a comparison script with 16 threads still keeps `llm_based` at 2.
- **Timeouts**: the examples go from 70 s to 180 s (the contract says at least 120: the first
  `rule_based` call indexes its table, `llm_based` retries Wikidata). 503 stays out of
  `retry_statuses`.
- Not changed: `uri` is still required; extra fields such as `algorithm` in our match lists are
  ours. Not tried against a live ECHOLOT: the instance on 127.0.0.1:5000 here still answers
  without the parameter (its `openapi.json` has none); the checkout next to this repository has
  the new code (`bdfd048`), whose registry and wikifier module match the contract.

## 2026-11-11 Enrichment from Wikidata (M5, planned)

- **What the owner wants:** the agent looks up what Wikidata holds for a sample's QIDs, suggests
  and analyses what to add, and writes the script that extracts the chosen data. Review happens
  on samples only: the datasets can have millions of rows, so the final script must run end to
  end, unattended. Everything per record is therefore one script (get the QID, however it is
  got, then extract), not a chain of runs.
- **Not prescribed:** where the QIDs come from (a column, reconciliation, reconciling only the
  rows without one, or reconciling anyway), the order of the steps, and the script's parameters.
  The user and the agent settle them per dataset; the docs show building blocks, not a flow.
- **Where the Wikidata access lives:** an endpoint in the ECHOLOT service,
  `POST /wikidata/statements`, not the app and not raw Wikidata from scripts. Turning Wikidata's
  JSON into usable values (item labels, dates at their precision, ranks) should be written once,
  not by the agent in every script; ECHOLOT already has the code for `/enrich`; caching,
  Wikimedia's User-Agent rule and rate limits belong in one place; and a large dataset may later
  need a local Wikidata copy behind the same endpoint, which scripts would not notice.
- **No new agent tools:** the agent explores through a sample run, like any service call, with
  the usual approval card. A direct lookup tool would make the app call a service itself (today
  only the runner does, with config and keys on the runner host). Revisit if the cards get in
  the way.
- **Two modes, one shape:** without `properties`, every property with a count and one example
  (exploring); with `properties`, every value of those (extracting). A famous person has
  hundreds of statements, most of them external identifiers, so an exploring script must
  condense what it returns: the agent reads at most 4,000 characters of a record.
- **Scale, noted for later:** at M2.3's rate (200 records in 53 s with `wikifier`) a million
  records take about three days to reconcile, before any Wikidata lookups, and Wikidata's
  public API is not meant for millions of requests. The app has not been run beyond 200 rows.

## 2026-11-11 `POST /wikidata/statements` in ECHOLOT (M5.1)

- Built in `echolot-wp4-api-service` on the branch `feat/wikidata-statements` (`eecc6a9`, off
  `llm-and-rule-based`, not pushed), on the Wikidata helpers `/enrich` uses: batches of 50,
  the label cache, dates at their precision. New: values keep their statement's rank
  (preferred first), carry typed fields beside `text` (`id` and `label` of items, `time`,
  `precision` and `calendar`, `amount` and `unit`, `language`), and a requested property the
  item lacks comes back as `count: 0`. Unknown QIDs are `found: false`, not an error; a
  redirect is followed and the reply's `id` is the target. Qualifiers and references are left
  out for now.
- That repository had no tests on this branch: pytest is added as a dev dependency, and 15
  tests run the endpoint against canned Wikidata replies (both modes, typed values, language
  fallback, the Julian calendar, redirects, unknown ids, bad input, 401, Wikidata failures).
- Live, 2026-11-11: summary of Q5593 and Q156736 together in 8.3 s with a cold label cache
  (92 KB; Picasso 435 properties, 365 of them external identifiers; Chillida 191 and 147);
  six chosen properties of both in 2.3 s, labels in Basque. Through the running service on
  127.0.0.1:5002, three properties of Aurelio Arteta in 0.8 s.
- Wikidata's data is not always right: on that day it gave Tel Aviv (Q33935) as Chillida's
  place of birth. The doc tells the agent to say values are copied as they stand.

## 2026-11-11 The Wikidata statements in the agent's doc (M5.2)

- `services/echolot.md` gets a last section on `/wikidata/statements`: the call, both modes,
  the reply with its typed values, errors, speed, and what to present. It names where a QID can
  come from (a column, `/reconcile` in the same script, both) without choosing: that is settled
  with the user per dataset. The front matter's description mentions it, so `list_services`
  shows it.
- Two short examples, both run through the harness against a fake in `test_service_docs.py`:
  an exploring script that condenses an item to one line per property and identifiers by name,
  each list cut to fit (checked on a fake item with 480 statements: under 4,000 characters), and
  an extracting script with one column per chosen property, values joined with `; ` and a
  `_qid` column beside item labels. The fake ECHOLOT in `run_script` now answers both endpoints.
