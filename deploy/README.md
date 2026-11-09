# Deploying the workbench with Docker Compose

One host runs the whole workbench for a group of testers: the web front with https, the app,
and a Prefect server with a runner that executes the scripts. The services the scripts call
(ECHOLOT) run elsewhere or on the same host.

```
 browser ──https──▶ web (Caddy) ──/api, /runner──▶ app (FastAPI, SQLite) ──REST──▶ prefect
                     └─ the single-page app                ▲                         ▲ polls
                                                           └── input, results ──── runner ──▶ ECHOLOT
                                                               (per-run token)     (service keys)
```

| Container | Image | Holds |
|---|---|---|
| `web` | Caddy with the built web app | certificates (`caddy-data`) |
| `app` | the FastAPI app and `echolot-admin` | database, uploads, results (`app-data`); the secret key and the users' encrypted model keys |
| `prefect` | `prefecthq/prefect` | the flow runs (`prefect-data`) |
| `runner` | the runner and Prefect | the service keys (`config/runner.env`); nothing of the app |

Only `web` is published (80 and 443; the Prefect UI on 127.0.0.1:4200). A script runs in the
runner container: it can call the services and reach the app only with its own run's token, never
the app's data or secrets. That is why the runner is a container of its own even on one host.

## Requirements

- Docker Engine with the Compose plugin (`docker compose version`), python3 for `init.sh`.
- 2 CPUs, 4 GB of memory and a few GB of disk for the images; data grows with the uploads.
- For automatic https: a DNS name pointing at the host, ports 80 and 443 open. Behind an
  institution's reverse proxy instead, see below.

## Install

```bash
git clone https://github.com/ECHOLOT-ECCCH/echolot-wp4-agentic-app.git
cd echolot-wp4-agentic-app/deploy
./init.sh                          # .env with new secrets, config/ from the examples
$EDITOR .env                       # SITE_ADDRESS; the model settings
$EDITOR config/services.toml       # where the runner finds ECHOLOT (below)
$EDITOR config/runner.env          # ECHOLOT_API_KEY=...
docker compose up -d --build       # the first build takes a few minutes
docker compose ps                  # app healthy, web, prefect healthy, runner up
docker compose exec app echolot-admin create-user you@example.org --name "You" --admin
```

The last command prints a temporary password. Sign in at `https://<SITE_ADDRESS>`, change it
under Settings → Account, and add the testers under Settings → Users: each new account gets a
temporary password, shown once, that you hand over privately. There is no sign-up and no e-mail.

## Models

Testers bring their own keys (Settings → Model keys) for Anthropic, OpenAI, Google, Mistral,
OpenRouter or DeepSeek, and choose the model in the chat. If `.env` sets `ECHO_MODEL_API_KEY`,
`ECHO_MODEL_PROVIDER` and `ECHO_MODEL_ID` are also offered to everyone as "Server default", on
your bill. The app container must reach the vendors' APIs over https.

## Runs and ECHOLOT

The runner calls the services listed in `config/services.toml`, with the keys of
`config/runner.env`. After changing either: `docker compose up -d --force-recreate runner`.

ECHOLOT's standalone API listens on 127.0.0.1:5000 by default, which a container cannot reach.
Either:

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

Other ways to run scripts, set in `.env`:

- **A Prefect server and runner elsewhere** (for example next to ECHOLOT, set up per
  [docs/runner-host.md](../docs/runner-host.md)): `COMPOSE_PROFILES=` (empty),
  `ECHO_PREFECT_API_URL`, `PREFECT_AUTH_STRING` as that server expects, and `ECHO_PUBLIC_URL`
  set to this site's https address, which that runner must reach.
- **Inside the app container**, without Prefect:
  `COMPOSE_FILE=docker-compose.yml:docker-compose.local-runs.yml` and `COMPOSE_PROFILES=`. Only
  for a single user or people who trust each other: a script then runs as the app's user and can
  read the app's data and secrets, other users' model keys included.

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

Keep `ECHO_COOKIE_SECURE=true`: the browser talks https to the proxy. Set it to `false` only
for a plain-http trial from another machine (http://localhost works with it on).

## Operating it

| Task | Command (in `deploy/`) |
|---|---|
| Logs (JSON lines for the app) | `docker compose logs -f app` (or `web`, `runner`, `prefect`) |
| Update | `git pull && docker compose up -d --build` |
| Accounts from the shell | `docker compose exec app echolot-admin --help` (create, reset, disable, list) |
| The Prefect UI | `ssh -L 4200:localhost:4200 <host>`, then http://localhost:4200, with `PREFECT_AUTH_STRING` |
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
the whole stack with the test model on http://127.0.0.1:8080, and checks the pages, sign-in, a
streamed chat turn and a run through Prefect and the runner; then it removes the stack. CI runs
it on every push. It uses neither `.env` nor `config/`.
