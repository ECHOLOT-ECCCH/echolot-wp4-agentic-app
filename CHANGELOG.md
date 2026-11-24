# Changelog

What was added when, by commit, newest first. Each section names the branch the commits are on;
`main` is what has been merged. The reasons behind each change are in
[docs/DECISIONS.md](docs/DECISIONS.md), the milestones in [docs/PLAN.md](docs/PLAN.md). ECHOLOT's
API ([echolot-wp4-api-service](https://github.com/ECHOLOT-ECCCH/echolot-wp4-api-service)) keeps
its own changelog.

## Deployments

| Date | Where | Workbench | ECHOLOT API | What changed |
|---|---|---|---|---|
| 2026-11-11 | https://echolot.ijs.si | `8a9e25c` | `eecc6a9` | First deployment: web, app, Prefect server, runner and ECHOLOT's API in one Compose project; the API at `/service/` |
| 2026-11-12 | https://echolot.ijs.si | `1cebcda` | `d0af95e` | `service_call` (single requests on a card); the changelogs |
| 2026-11-12 | https://echolot.ijs.si | `67fed4e` | `ce6175f` | `llm_based` through HiTZ's hosted Latxa service, with the server's own HiTZ key |
| 2026-11-24 | https://echolot.ijs.si | `6a74095` | `ce6175f` | The runner fenced in (M6.1 to M6.3): only on the internal network `runs`, which ECHOLOT joins; limits on its container; the run timeout. The owner turned on the host's firewall (ufw), with the bridge refused first and port 80 only for the frontend |
| 2026-11-24 | https://echolot.ijs.si | `096af15` | `ce6175f` | Scripts in a sandbox container (M6.4): no network, no secrets, a uid per run; the runner 1 GB, the sandbox 2 GB. Checked on the server with real ECHOLOT calls |
| 2026-11-24 | https://echolot.ijs.si | `33c8cca` | `579e4f1` | Both from `main` after the merge. M6.5: the API docs pages off (the app's and ECHOLOT's), ECHOLOT's image as a user |

The host keeps the same log, with the exact commits checked out, in `/opt/echolot/README.md`.

## `main` after the merge of 2026-11-24

New work branches off `main` and is merged back into it.

| Commit | Date | What it adds | Where |
|---|---|---|---|
| (this commit) | 2026-11-24 | The deployment of `33c8cca` recorded | `CHANGELOG.md`, `docs/PLAN.md` |
| `33c8cca` | 2026-11-24 | The merge of `feat/runner-hardening`, and with it `feat/wikidata-statements` and `feat/users-and-byok` (pzajec agreed) | |

## Branch `feat/runner-hardening` (merged into `main` on 2026-11-24; built on `feat/wikidata-statements`)

Work by andrazrepar, 2026-11-23. Milestone 6 (running scripts safely on a shared host), after a
review of what a script can reach on echolot.ijs.si (DECISIONS 2026-11-23).

| Commit | Date | What it adds | Where |
|---|---|---|---|
| `b6fa93f` | 2026-11-24 | The three branches marked as merged into `main`, and the hashes filled in | `CHANGELOG.md`, `docs/PLAN.md` |
| `9213da2` | 2026-11-24 | **The API's documentation pages can be turned off (M6.5):** `ECHO_API_DOCS` (on by default, off in the Compose deployment); `/api/docs` and `/api/openapi.json` answered without a session. ECHOLOT's side (its docs switch, its image as a user) is in its repository. | `settings.py`, `main.py`, `deploy/docker-compose.yml`, `deploy/.env.example`, `tests/test_auth.py`, docs |
| `e6cf7b1` | 2026-11-24 | The deployment of `096af15` recorded, and the hashes above filled in | `CHANGELOG.md`, `docs/PLAN.md` |
| `096af15` | 2026-11-24 | The sandbox's supervisor no longer counts a killed worker that waits to be collected as running, which made it retry and log a false alarm after every run (seen on the server) | `runner/src/echolot_runner/sandbox.py`, `runner/runner_tests/test_sandbox.py` |
| `47ade40` | 2026-11-24 | **Scripts in a sandbox container (M6.4):** a `sandbox` container with no network and no secrets runs each run's script in a worker under a uid of its own; the runner keeps the token, the keys and Prefect's credentials and makes the service calls over a Unix socket only it may open. A worker that dies fails the run with its reason; `print()` goes to the run log; a service path may not lead out of the service. Without Docker the worker is a child process with an empty environment. The runner gets `no-new-privileges` and no capabilities; its memory default goes to 1g, the sandbox has 2g. | `runner/src/echolot_runner/` (`worker.py`, `sandbox.py`, `protocol.py`, `harness.py`, `context.py`, `services.py`), `deploy/` (Dockerfile, compose, smoke), tests, docs |
| `8d8b26b` | 2026-11-24 | M6.4 designed (proposed): scripts in a `sandbox` container with no network and no secrets, a uid per run; the runner makes the service calls | `docs/DECISIONS.md`, `docs/PLAN.md` |
| `cea0010` | 2026-11-24 | M6.1 done: the host's firewall on, recorded; deploy/README.md says to check `ufw status` and to allow ssh before turning ufw on | `deploy/README.md`, `docs/PLAN.md`, `docs/DECISIONS.md` |
| `2653b56` | 2026-11-24 | The deployment of `6a74095` recorded, and its hash filled in below | `CHANGELOG.md`, `docs/PLAN.md` |
| `6a74095` | 2026-11-23 | This section | `CHANGELOG.md` |
| `1bbd7d5` | 2026-11-23 | **Limits on the runner's container (M6.2):** memory with no swap beyond it (`RUNNER_MEMORY`, 2g), CPUs (`RUNNER_CPUS`, 2), processes and threads (`RUNNER_PIDS`, 512), a read-only filesystem with `/tmp` (`RUNNER_TMP_SIZE`, 1g) and the home directory in memory. A script that exhausts the memory fails its own run; the smoke check runs one and then a normal run. | `deploy/docker-compose.yml`, `deploy/smoke_check.py`, `deploy/README.md` |
| `a9766d1` | 2026-11-23 | **Run timeout (M6.3):** the poller stops a running run in which no record has finished for `ECHO_RUN_STALL_MINUTES` (30) or that has run for `ECHO_RUN_MAX_HOURS` (24), through the executor's cancel, so Prefect kills a script stuck in a loop; the run ends failed with the reason. The agent's workspace block names both limits. | `runs/poller.py`, `runs/service.py`, `settings.py`, `agent/agent.py`, `tests/test_run_limits.py` |
| `714746e` | 2026-11-23 | **The runner's network (M6.1):** with the bundled Prefect server the runner is only on the internal network `runs`: the app, Prefect and the services that join it, not the internet or the host's network. The bridge is named (`echolot-runs`) for the host's firewall. The smoke check runs a script that tries to reach the internet. | `deploy/docker-compose.prefect.yml`, `deploy/smoke.sh`, `deploy/smoke_check.py`, `deploy/README.md`, `CLAUDE.md` |
| `5004988` | 2026-11-23 | Milestone 6 planned: what a script can reach today, the order of the fixes, what is left to the owner | `docs/PLAN.md`, `docs/DECISIONS.md` |

## Branch `feat/wikidata-statements` (merged into `main` on 2026-11-24; built on `feat/users-and-byok`)

Work by andrazrepar, 2026-11-11 and 2026-11-12. Milestone 5 (enrichment from Wikidata) and the
first deployment.

| Commit | Date | What it adds | Where |
|---|---|---|---|
| `a6a0a34` | 2026-11-12 | The deployment of `67fed4e` recorded, and the hashes of the rows above filled in | `CHANGELOG.md` |
| `67fed4e` | 2026-11-12 | `llm_based` now runs through HiTZ's hosted Latxa service when ECHOLOT has HiTZ's key (that change is in ECHOLOT's repository): the agent's doc says so, with its speed and results; DECISIONS records the service and the choice | `services/echolot.md`, `docs/DECISIONS.md` |
| `1cebcda` | 2026-11-12 | This changelog | `CHANGELOG.md`, `README.md`, `CLAUDE.md` |
| `46eeae1` | 2026-11-11 | **`service_call` (M5.4):** the agent sends one request to a service and gets the reply, e.g. one QID's Wikidata statements or one record reconciled, without writing a script or starting a run. Every call needs the user's approval on a card showing the service, method, path, query and body. Calls go through the executor and the runner like a one-record run, so service keys stay on the runner, and leave no run, script version or file. | `runs/calls.py`, `agent/tools/services.py`, `routers/runner_io.py` (`/runner/calls/{id}`), `db.py` (`service_calls`), web `runCards.tsx`; PLAN 7.3 and 7.9, CLAUDE.md |
| `e2839fd` | 2026-11-11 | The deployment to echolot.ijs.si recorded (M4.3): topology, shared layout, generated keys, the end-to-end check | `docs/DECISIONS.md`, `docs/PLAN.md` |
| `8a9e25c` | 2026-11-11 | **The agent's instructions (M5.3):** the full run is unattended and can be millions of records, so a script must work end to end; with QIDs, the agent explores a sample on Wikidata, suggests data and extracts what the user chose | `agent/prompts/system.md` |
| `3a81819` | 2026-11-11 | **`/wikidata/statements` in the agent's ECHOLOT doc (M5.2):** request, reply, errors, speed, an exploring and an extracting example script, both tested against a fake ECHOLOT | `services/echolot.md`, `tests/test_service_docs.py` |
| `62427aa` | 2026-11-11 | M5.1 recorded: the endpoint itself is in ECHOLOT's repository (`eecc6a9` there) | `docs/` |
| `be54895` | 2026-11-11 | Milestone 5 planned: why the Wikidata access lives in ECHOLOT, why the final script is one end-to-end script, what is left to the user and the agent | `docs/PLAN.md`, `docs/DECISIONS.md` |

## Branch `feat/users-and-byok` (merged into `main` on 2026-11-24, with pzajec's agreement)

Work by pzajec, 2026-11-09 and 2026-11-10. Milestone 4 (accounts, own model keys, deployment).

| Commit | Date | What it adds |
|---|---|---|
| `a32eb66` | 2026-11-10 | ECHOLOT's three reconciliation algorithms (`wikifier`, `llm_based`, `rule_based`) in the agent's doc, the runner (`post` with query parameters, `concurrency_for`) and the run review |
| `620fc79` | 2026-11-09 | Deployment: runs go to an external Prefect server by default; the bundled one becomes an option; `echolot-admin check-runs` |
| `c29cc0f` | 2026-11-09 | Deployment: Docker Compose with Caddy, the app and an optional runner; `init.sh`, `smoke.sh` |
| `981c27d` | 2026-11-09 | Accounts and own model keys in the plan, decisions, README and CLAUDE.md |
| `449f01b` | 2026-11-09 | Web: sign-in, Settings (model keys, account, users), a model picker in the chat |
| `0571560` | 2026-11-09 | Server: user accounts with sessions, workspace ownership, encrypted bring-your-own model keys, `echolot-admin` |

## `main`

### 2026-10-27 to 2026-10-28: the ECHOLOT workbench (milestones 0 to 3)

Mostly pzajec, cut from the Elexifier app.

| Milestone | Commits | What it adds |
|---|---|---|
| M0 Skeleton | `ba0529b` `4dbb550` `2622fcd` `94a74e4` `1c5b669` `95311a8` | The plan; a SQLite app skeleton; workspaces, task notes and file upload normalized to records; the agent with file tools and the streaming chat; the web app cut to workspaces, records, task notes and chat; Playwright smoke and CI |
| M1 Scripts and local runs | `709db36` `8bdaa1e` `6347532` `da9606a` `f8673d3` `a5d6f30` | Task notes in the agent's instructions; versioned scripts with contract checks; the runner's harness, service client and CLI; runs with a local executor, runner endpoints, export and retry; run and service tools with approval cards; the run view |
| M2 Prefect | `162166f` `37df42b` `bce1bac` | The Prefect flow and serve entry point; the Prefect executor over REST; the live check with Prefect and ECHOLOT |
| M3 Reconciliation | `3a0af3a` `eebad0b` `e7dd5c2` | ECHOLOT's doc for the agent; result cells with ranked candidates and links; per-row decisions with filters and export |
| After M3 | `83c0f00` `40019c1` `1bc0401` `d6ac734` `c796811` `a53b354` `16114f1` | README status; service keys for the local executor (`runner.env`); a demo file; `run_status` can wait; result files listed at once; usage screenshots; the move to ECHOLOT-ECCCH |

### Before 2026-10-27

The Elexifier app this repository was cut from (last commit `98999b1`; its docs are in
`docs/archive/elexifier/`).
