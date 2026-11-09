# Deploying the workbench with Docker Compose

One host runs the web front with https and the app. Runs go to a Prefect server that you give
the app (`ECHO_PREFECT_API_URL`), where a runner serves the workbench's flow and executes the
scripts next to the services they call (ECHOLOT). The runner can also run in this stack, and for
a single host or a trial the stack can bring a Prefect server of its own.

```
 browser ──https──▶ web (Caddy) ──/api, /runner──▶ app (FastAPI, SQLite) ──REST──▶ Prefect server
                     └─ the single-page app            ▲                         (ECHO_PREFECT_API_URL)
                                                       │                                ▲ polls
                                                       └──── input, results ──────── runner ──▶ ECHOLOT
                                                         (ECHO_PUBLIC_URL, per-run token)  (service keys)
```

| Container | Image | Holds |
|---|---|---|
| `web` | Caddy with the built web app | certificates (`caddy-data`) |
| `app` | the FastAPI app and `echolot-admin` | database, uploads, results (`app-data`); the secret key and the users' encrypted model keys |
| `runner` (optional) | the runner and Prefect | the service keys (`config/runner.env`); nothing of the app |
| `prefect` (optional) | `prefecthq/prefect` | the flow runs (`prefect-data`) |

Only `web` is published (80 and 443). A script runs where the runner runs: it can call the
services and reach the app only with its own run's token, never the app's data or secrets.

## Requirements

- Docker Engine with the Compose plugin (`docker compose version`), python3 for `init.sh`.
- 2 CPUs, 4 GB of memory and a few GB of disk for the images; data grows with the uploads.
- For automatic https: a DNS name pointing at the host, ports 80 and 443 open. Behind an
  institution's reverse proxy instead, see below.
- A Prefect 3 server the app reaches over HTTP(S): its API URL and its credentials (basic auth
  or an API key). The runner must reach the same server and this site.

## Install

```bash
git clone https://github.com/ECHOLOT-ECCCH/echolot-wp4-agentic-app.git
cd echolot-wp4-agentic-app/deploy
./init.sh                          # .env with a new secret key, config/ from the examples
$EDITOR .env                       # SITE_ADDRESS, ECHO_PREFECT_*, ECHO_PUBLIC_URL, the models
docker compose up -d --build       # the first build takes a few minutes
docker compose ps                  # app healthy, web up
docker compose exec app echolot-admin create-user you@example.org --name "You" --admin
docker compose exec app echolot-admin check-runs
```

`create-user` prints a temporary password. Sign in at `https://<SITE_ADDRESS>`, change it
under Settings → Account, and add the testers under Settings → Users: each new account gets a
temporary password, shown once, that you hand over privately. There is no sign-up and no e-mail.

`check-runs` shows where runs go and whether the Prefect server answers, the runner's
deployment (`run-script/default`) exists and a runner polls it:

```
Runs go to the Prefect server at https://prefect.example.org/api (basic auth), deployment run-script/default.
ok    the Prefect server answers (3.8.8)
ok    the deployment run-script/default exists
ok    a runner polls it (last at 2026-11-09T10:00:02Z)
note  the runner fetches input and sends results to https://workbench.example.org (ECHO_PUBLIC_URL)
```

## Models

Testers bring their own keys (Settings → Model keys) for Anthropic, OpenAI, Google, Mistral,
OpenRouter or DeepSeek, and choose the model in the chat. If `.env` sets `ECHO_MODEL_API_KEY`,
`ECHO_MODEL_PROVIDER` and `ECHO_MODEL_ID` are also offered to everyone as "Server default", on
your bill. The app container must reach the vendors' APIs over https.

## Runs

The app creates each run as a flow run of the deployment `run-script/default` on the Prefect
server in `.env`:

```bash
ECHO_PREFECT_API_URL=https://prefect.example.org/api   # the API, usually ending in /api
ECHO_PREFECT_AUTH_STRING=user:password                 # basic auth (PREFECT_SERVER_API_AUTH_STRING)
# ECHO_PREFECT_API_KEY=...                             # or a key: Prefect Cloud, a token proxy
ECHO_PUBLIC_URL=https://workbench.example.org          # where the runner reaches this app
```

A runner serves that deployment on the same server, picks the run up, fetches its input from
`ECHO_PUBLIC_URL` (Caddy passes `/runner/*` to the app), calls the services and sends the
results back the same way. So the runner must reach this site, and the app must reach the
Prefect server. Each run's parameters on the Prefect server hold its token, which opens that
run's input and results until the run ends: keep that server's UI to the people who run the
workbench.

**The runner's Prefect version** should match the server's. The runner image has uv.lock's
(3.8.8); for another server version set `PREFECT_VERSION` in `.env` (or `--build-arg
PREFECT_VERSION=...` for the image alone) and rebuild. The app talks to any Prefect 3 server.

Where the runner runs:

- **Next to ECHOLOT, on a host of its own** (the usual case): set it up there per
  [docs/runner-host.md](../docs/runner-host.md), natively or with this repository's runner
  image. Nothing more to set here; `config/` is unused.
- **In this stack:** `COMPOSE_PROFILES=runner` and `ECHO_PUBLIC_URL=http://app:8000` in
  `.env`; the services it may call in `config/services.toml`, their keys in
  `config/runner.env` (`ECHOLOT_API_KEY=...`). After changing either:
  `docker compose up -d --force-recreate runner`. It uses the app's Prefect settings.
- **In this stack, with a Prefect server of its own** (one host, a trial, no Prefect server to
  use): `./init.sh --with-prefect` writes a `.env` that adds `docker-compose.prefect.yml` and
  the runner, with a generated auth string; `ECHO_PREFECT_API_URL` and `ECHO_PUBLIC_URL` are
  then not used. Its UI: `ssh -L 4200:localhost:4200 <host>`, then http://localhost:4200, with
  `ECHO_PREFECT_AUTH_STRING`.
- **Inside the app container**, without Prefect:
  `COMPOSE_FILE=docker-compose.yml:docker-compose.local-runs.yml` in `.env`. Only for a single
  user or people who trust each other: a script then runs as the app's user and can read the
  app's data and secrets, other users' model keys included.

**ECHOLOT and a runner in this stack.** ECHOLOT's standalone API listens on 127.0.0.1:5000 by
default, which a container cannot reach. Either:

- **ECHOLOT on this host, outside Docker:** let it listen on the Docker bridge (`172.17.0.1`) or
  on all interfaces behind a firewall, and keep `url = "http://host.docker.internal:5000"`.
- **ECHOLOT in Docker Compose on this host:** put the runner on ECHOLOT's network with a
  `docker-compose.override.yml` next to this file (Compose reads it automatically, unless
  `.env` sets `COMPOSE_FILE`: then add it to that list), and use
  ECHOLOT's service name in `services.toml`, e.g. `url = "http://api:5000"`:

  ```yaml
  services:
    runner:
      networks: [default, echolot]
  networks:
    echolot:
      external: true
      name: echolot-wp4-api-service_default   # docker network ls
  ```

- **ECHOLOT on another host:** its URL in `services.toml`.

## Behind a reverse proxy

When the institution's proxy terminates TLS and forwards to this host:

```bash
SITE_ADDRESS=:80
HTTP_PORT=127.0.0.1:8080                # or the address the proxy connects to
TRUSTED_PROXY_CIDR=192.0.2.10/32        # the proxy, so sign-in throttling sees the testers
```

The proxy must pass `Host` and `X-Forwarded-For`, must not buffer the chat (it streams for
minutes), and must allow large uploads. For nginx:

```nginx
location / {
    proxy_pass http://<this host>:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;              # the app also sends X-Accel-Buffering: no on the chat
    proxy_read_timeout 600s;
    client_max_body_size 1g;
}
```

A runner on another host comes in the same way (`ECHO_PUBLIC_URL`, the paths under
`/runner/`), so the proxy must be reachable from it too.

Keep `ECHO_COOKIE_SECURE=true`: the browser talks https to the proxy. Set it to `false` only
for a plain-http trial from another machine (http://localhost works with it on).

## Operating it

| Task | Command (in `deploy/`) |
|---|---|
| Logs (JSON lines for the app) | `docker compose logs -f app` (or `web`, `runner`, `prefect`) |
| Update | `git pull && docker compose up -d --build` |
| Accounts from the shell | `docker compose exec app echolot-admin --help` (create, reset, disable, list) |
| Runs fail to start | `docker compose exec app echolot-admin check-runs` |
| Stop / start | `docker compose stop` / `docker compose up -d` |

The database schema is upgraded at the app's start. Run a single app container and a single
uvicorn worker: sign-in throttling, the run poller and the agent's streams live in its process.

**Backups.** Everything that matters is in the `app-data` volume (`app.db` and the files) and in
`.env` (the secret key: without it the stored model keys are lost; the testers would re-enter
them). For a consistent copy, stop the app for a moment:

```bash
docker compose stop app
docker run --rm -v echolot-workbench_app-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/app-data-$(date +%F).tgz -C /data .
docker compose start app
```

Restore by extracting into the (empty) volume the same way. Keep `.env` apart from these
archives: a backup that holds both the database and the secret key holds the testers' keys.

**Secret key rotation.** Put a new key first, comma-separated with the old one
(`ECHO_SECRET_KEY=new,old`), `docker compose up -d app`; keys saved from then on use the new one.
Drop the old key once every tester has re-saved theirs.

## Checking a build

`deploy/smoke.sh` (or `make deploy-smoke` from the repository root) builds the images, starts
the whole stack with a Prefect server of its own and the test model on http://127.0.0.1:8080,
waits for `check-runs` to pass, and checks the pages, sign-in, a streamed chat turn and a run
through Prefect and the runner; then it removes the stack. CI runs it on every push. It uses
neither `.env` nor `config/`.
