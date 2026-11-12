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
| 2026-11-12 | https://echolot.ijs.si | head of `feat/wikidata-statements` with `46eeae1` | head of `feat/wikidata-statements` with `eecc6a9` | `service_call` (single requests on a card); the changelogs |

The host keeps the same log, with the exact commits checked out, in `/opt/echolot/README.md`.

## Branch `feat/wikidata-statements` (not merged; builds on `feat/users-and-byok`)

Work by andrazrepar, 2026-11-11 and 2026-11-12. Milestone 5 (enrichment from Wikidata) and the
first deployment.

| Commit | Date | What it adds | Where |
|---|---|---|---|
| (this commit) | 2026-11-12 | `llm_based` now runs through HiTZ's hosted Latxa service when ECHOLOT has HiTZ's key (that change is in ECHOLOT's repository): the agent's doc says so, with its speed and results; DECISIONS records the service and the choice | `services/echolot.md`, `docs/DECISIONS.md` |
| `1cebcda` | 2026-11-12 | This changelog | `CHANGELOG.md`, `README.md`, `CLAUDE.md` |
| `46eeae1` | 2026-11-11 | **`service_call` (M5.4):** the agent sends one request to a service and gets the reply, e.g. one QID's Wikidata statements or one record reconciled, without writing a script or starting a run. Every call needs the user's approval on a card showing the service, method, path, query and body. Calls go through the executor and the runner like a one-record run, so service keys stay on the runner, and leave no run, script version or file. | `runs/calls.py`, `agent/tools/services.py`, `routers/runner_io.py` (`/runner/calls/{id}`), `db.py` (`service_calls`), web `runCards.tsx`; PLAN 7.3 and 7.9, CLAUDE.md |
| `e2839fd` | 2026-11-11 | The deployment to echolot.ijs.si recorded (M4.3): topology, shared layout, generated keys, the end-to-end check | `docs/DECISIONS.md`, `docs/PLAN.md` |
| `8a9e25c` | 2026-11-11 | **The agent's instructions (M5.3):** the full run is unattended and can be millions of records, so a script must work end to end; with QIDs, the agent explores a sample on Wikidata, suggests data and extracts what the user chose | `agent/prompts/system.md` |
| `3a81819` | 2026-11-11 | **`/wikidata/statements` in the agent's ECHOLOT doc (M5.2):** request, reply, errors, speed, an exploring and an extracting example script, both tested against a fake ECHOLOT | `services/echolot.md`, `tests/test_service_docs.py` |
| `62427aa` | 2026-11-11 | M5.1 recorded: the endpoint itself is in ECHOLOT's repository (`eecc6a9` there) | `docs/` |
| `be54895` | 2026-11-11 | Milestone 5 planned: why the Wikidata access lives in ECHOLOT, why the final script is one end-to-end script, what is left to the user and the agent | `docs/PLAN.md`, `docs/DECISIONS.md` |

## Branch `feat/users-and-byok` (not merged)

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
