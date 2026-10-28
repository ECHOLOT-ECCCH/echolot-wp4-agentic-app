# The runner host

The runner executes workspace scripts record by record, next to the services they call. It is
a Prefect deployment (`run-script/default`) served by `python -m echolot_runner.serve`; the app
creates flow runs over Prefect's REST API and never runs scripts itself (docs/PLAN.md 7.2–7.7).

```
app (ECHO_EXECUTOR=prefect) ──REST──▶ Prefect server :4200 ◀──polls── runner (serve)
        ▲                                                              │
        └────────── input, progress, results (HTTP, per-run token) ────┤
                                                                       └──▶ services (ECHOLOT :5000, …)
```

## What it needs

- Python 3.12 and [uv](https://docs.astral.sh/uv/).
- A Prefect 3 server it can reach. Use the same `prefect` version on the runner as on the
  server.
- Network access to the services (ECHOLOT binds to 127.0.0.1:5000, so the runner runs on the
  same host) and to the app's `ECHO_PUBLIC_URL`. ECHOLOT's code and setup:
  https://github.com/ECHOLOT-ECCCH/echolot-wp4-api-service.

## Install

From a checkout of this repository:

```bash
uv venv /opt/echolot-runner/.venv --python 3.12
uv pip install --python /opt/echolot-runner/.venv ./runner "prefect==<the server's version>"
```

## Services

Copy `runner/services.example.toml` to `/opt/echolot-runner/services.toml` and list every
service a script may call. The names must match the app's `services/<name>.md`. Keys are not
written in the file: `key_env` names the environment variable that holds the key, and the runner
sends it in `auth_header`.

```toml
[echolot]
url = "http://127.0.0.1:5000"
auth_header = "X-API-Key"
key_env = "ECHOLOT_API_KEY"
concurrency = 4
timeout_s = 70
retry_statuses = [502]
retries = 3
```

## Environment

| Variable | Meaning |
|---|---|
| `PREFECT_API_URL` | The Prefect server's API, e.g. `http://127.0.0.1:4200/api` |
| `PREFECT_API_AUTH_STRING` | `user:password` when the server sets `PREFECT_SERVER_API_AUTH_STRING` |
| `RUNNER_SERVICES_FILE` | Path of `services.toml` (default `./services.toml`) |
| `RUNNER_MAX_RUNS` | Runs executed at the same time (default 4) |
| `PREFECT_RUNNER_POLL_FREQUENCY` | Seconds between polls for new runs (Prefect's default is 10; 2 makes samples start quickly) |
| `ECHOLOT_API_KEY`, … | The keys named by `key_env` |

## Start

```bash
cd /opt/echolot-runner
set -a; . ./runner.env; set +a      # the variables above, readable only by the runner's user
.venv/bin/python -m echolot_runner.serve
```

As a systemd service:

```ini
[Unit]
Description=ECHOLOT workbench runner
After=network-online.target

[Service]
User=echolot-runner
WorkingDirectory=/opt/echolot-runner
EnvironmentFile=/opt/echolot-runner/runner.env
ExecStart=/opt/echolot-runner/.venv/bin/python -m echolot_runner.serve
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## The app's side

```bash
ECHO_EXECUTOR=prefect
ECHO_PREFECT_API_URL=http://<prefect host>:4200/api
ECHO_PREFECT_AUTH_STRING=user:password        # only with basic auth on the server
ECHO_PREFECT_DEPLOYMENT=run-script/default
ECHO_PUBLIC_URL=http://<app host as the runner reaches it>:8000
```

The runner fetches the run's input and sends progress and results to `ECHO_PUBLIC_URL`, so that
URL must be reachable from the runner host. When it is not, see BACKLOG (object-store data plane).

## A Prefect server for development

Prefect 3.8.8's server does not start with FastAPI 0.150 or later (it still passes `min_items`
to `Body`), so run it in its own environment:

```bash
PREFECT_HOME=./data/prefect uvx --from 'prefect==3.8.8' --with 'fastapi<0.150' \
  prefect server start --host 127.0.0.1 --port 4200
```

## Security

- Scripts run with the runner user's rights: its network, its files, its environment, including
  the service keys. Every run the agent submits needs the user's approval; run the runner as a
  dedicated user with nothing else to read. Container isolation is in docs/BACKLOG.md.
- A run's token travels in the flow run's parameters and is visible to anyone who can open the
  Prefect UI. It only opens that run's input, progress and result endpoints, and only until
  the run finishes.
