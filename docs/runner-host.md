# The runner host

The app ([deploy/README.md](../deploy/README.md)) sends runs to a Prefect server
(`ECHO_PREFECT_API_URL`). This guide sets up the runner that serves the workbench's flow on that
server, on a host of its own next to the services, natively or with the runner image.

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
sends it in `auth_header`. `concurrency_for` keeps ECHOLOT's `llm_based` to fewer calls at a time
(each makes six Wikidata requests, and Wikidata rate-limits); the timeout covers the first
`rule_based` call after ECHOLOT starts, which waits while its table is indexed.

```toml
[echolot]
url = "http://127.0.0.1:5000"
auth_header = "X-API-Key"
key_env = "ECHOLOT_API_KEY"
concurrency = 4
concurrency_for = { "algorithm=llm_based" = 2 }   # fewer calls at a time for these requests
timeout_s = 180
retry_statuses = [502]
retries = 3
```

## Environment

| Variable | Meaning |
|---|---|
| `PREFECT_API_URL` | The Prefect server's API, e.g. `http://127.0.0.1:4200/api` |
| `PREFECT_API_AUTH_STRING` | `user:password` when the server sets `PREFECT_SERVER_API_AUTH_STRING` |
| `PREFECT_API_KEY` | Instead, an API key (Prefect Cloud, or a proxy that wants a bearer token) |
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

## With Docker

The runner image (`deploy/Dockerfile`, target `runner`) holds the runner and Prefect, and runs
as uid 10001. Build it on the runner host from a checkout, with the server's Prefect version, and
start it on the host's network, so that `url = "http://127.0.0.1:5000"` in `services.toml`
reaches ECHOLOT as it binds by default:

The scripts themselves run in the sandbox image (target `sandbox`): no network, no secrets, a
uid per run (docs/PLAN.md M6.4). The two share a small volume for the sandbox's socket:

```bash
docker build -f deploy/Dockerfile --target runner \
  --build-arg PREFECT_VERSION=<the server's version> -t echolot-workbench/runner .
docker build -f deploy/Dockerfile --target sandbox \
  --build-arg PREFECT_VERSION=<the server's version> -t echolot-workbench/sandbox .
docker volume create --opt type=tmpfs --opt device=tmpfs --opt o=size=1m,mode=0755 echolot-sandbox
docker run -d --name echolot-sandbox --restart unless-stopped --network none \
  --cap-drop ALL --cap-add SETUID --cap-add SETGID --cap-add KILL \
  --security-opt no-new-privileges:true --read-only --tmpfs /tmp:size=512m,mode=1777 \
  --memory 2g --memory-swap 2g --pids-limit 1024 \
  -v echolot-sandbox:/run/sandbox echolot-workbench/sandbox
chmod 644 /opt/echolot-runner/services.toml      # readable by the container's user
docker run -d --name echolot-runner --restart unless-stopped --network host \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --env-file /opt/echolot-runner/runner.env \
  -e RUNNER_SERVICES_FILE=/config/services.toml \
  -e RUNNER_SANDBOX_SOCKET=/run/sandbox/sandbox.sock \
  -v /opt/echolot-runner/services.toml:/config/services.toml:ro \
  -v echolot-sandbox:/run/sandbox:ro \
  echolot-workbench/runner
docker logs -f echolot-runner
```

`runner.env` holds the variables above, without quotes (Docker's env-file format).

## The app's side

```bash
ECHO_EXECUTOR=prefect                          # the Compose deployment's default
ECHO_PREFECT_API_URL=https://<prefect host>/api
ECHO_PREFECT_AUTH_STRING=user:password         # with basic auth on the server
# ECHO_PREFECT_API_KEY=...                     # or with an API key
ECHO_PREFECT_DEPLOYMENT=run-script/default
ECHO_PUBLIC_URL=https://<the workbench's address, as the runner reaches it>
```

The runner fetches the run's input and sends progress and results to `ECHO_PUBLIC_URL`, so that
URL must be reachable from the runner host. When it is not, see BACKLOG (object-store data plane).
`echolot-admin check-runs` on the app's side tells whether the app reaches the server and a
runner polls the deployment.

## A Prefect server for development

Prefect 3.8.8's server does not start with FastAPI 0.150 or later (it still passes `min_items`
to `Body`), so run it in its own environment:

```bash
PREFECT_HOME=./data/prefect uvx --from 'prefect==3.8.8' --with 'fastapi<0.150' \
  prefect server start --host 127.0.0.1 --port 4200
```

## Security

- With Docker, scripts run in the sandbox container: no network, no secrets, a uid per run; the
  runner makes their service calls with the keys. Natively, without the sandbox, a script runs
  in a child process with an empty environment but the runner user's rights: its network, its
  files and the environment of the runner's own process, including the service keys. There,
  run the runner as a dedicated user with nothing else to read, for trusted users only. Every
  run the agent submits needs the user's approval.
- A run's token travels in the flow run's parameters and is visible to anyone who can open the
  Prefect UI. It only opens that run's input, progress and result endpoints, and only until
  the run finishes.
