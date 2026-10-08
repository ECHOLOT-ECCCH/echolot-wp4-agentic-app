# Self-hosting Elexifier

Everything runs from one Docker Compose file. The same stack serves the hosted instance and an
institution's own deployment.

## What runs

| Service | Image | Role |
|---|---|---|
| caddy | `ghcr.io/<owner>/elexifier-web` (`deploy/compose/Dockerfile.web`) | Serves the web app, proxies `/api`, `/auth`, `/healthz` to the API and `/idp` to Keycloak, automatic HTTPS for a domain |
| api | `ghcr.io/<owner>/elexifier-api` (`deploy/compose/Dockerfile.api`) | FastAPI application; applies database migrations on start |
| worker | same image as api | Background jobs (conversions, PDF processing, structuring) |
| sandbox-runner | `ghcr.io/<owner>/elexifier-sandbox-runner` | Runs agent-written snippets in sibling containers made from `elexifier-sandbox` |
| postgres | `postgres:16` | Application data, job queue, Keycloak database |
| garage | `dxflrs/garage` | S3-compatible object storage for uploaded and generated files |
| keycloak | `quay.io/keycloak/keycloak` | Identity provider under `/idp` of the site: local accounts, ORCID/GitHub login, institutional SAML/OIDC federation |

The app images are published by CI to the GitHub Container Registry on every push to `main`
(tags `main` and `sha-<commit>`) and for every git tag (`m3`, …), for `linux/amd64`; a fifth
image, `elexifier-deploy`, carries the deployment bundle (Compose file, Keycloak realm, scripts)
so that a server needs nothing but Docker and a registry token. An arm64 server builds from
source instead (*Running from source*).

## Requirements

Measured on the dev stack and the production Compose file (September 2026); the stack is seven
containers: Caddy (web + TLS + reverse proxy), API, worker, sandbox runner, PostgreSQL 16,
Garage (S3), Keycloak 26.

**Machine.** A Linux VM, x86_64 or arm64 (the images build for both), with Docker Engine 24+
and Compose v2; Ubuntu 24.04 LTS or Debian 12 are the tested bases. No GPU: nothing in the
current stack uses one (a GPU matters only for the local model planned in M4). Nested
virtualisation is not needed. The VM must allow Docker to run containers with cgroup limits
(any standard KVM/Xen/Hyper-V VM does; some container-based "VMs" such as LXC/OpenVZ do not).

| | Minimum (try-out, 2-3 users) | Recommended (institution, ~10 concurrent users) |
|---|---|---|
| vCPU | 2 | 4-8 |
| RAM | 4 GB | 8-16 GB |
| Disk | 40 GB SSD | 100 GB SSD, data on its own volume |

Where the numbers come from:
- Idle memory is about 1 GB: Keycloak ~600 MB, PostgreSQL ~100 MB, API ~150 MB, Garage,
  worker, runner and Caddy a few tens of MB each.
- Peaks are in the worker: 397 MB RSS while validating the full output of a 149 MB / 88,575-entry
  XML export (xmlschema holds the whole 28 MB result in memory); ~80 MB for PDF layout
  extraction; every sandbox snippet may take up to 2 GB and 2 CPUs (its own limits, one run at
  a time per worker). Uploads stream to storage at constant memory. So 4 GB runs everything one
  job at a time; 8 GB keeps the UI responsive while a full run, a structuring job and a snippet
  overlap; 16 GB if several lexicographers run heavy jobs at once.
- CPU: the heavy jobs are single-threaded per job: PDF layout about 110 ms per page (a 1,267-page
  dictionary in 2.3 minutes), XSD 1.1 validation of a large export 3-4 minutes, the conversion
  itself seconds. Model-driven structuring waits on the provider, not on the CPU. Two vCPUs
  serve a small team; more vCPUs help only with concurrent jobs (one worker process runs one job
  at a time: scale with worker replicas, `docker compose up -d --scale worker=2`).
- Disk: images about 3 GB (API/worker 750 MB, Keycloak 460 MB, PostgreSQL 470 MB, sandbox image
  365 MB, runner 260 MB, Garage and web under 100 MB) plus 1-2 GB of build cache when built on
  the VM. Data: uploads (up to `MAX_UPLOAD_MB`, default 2 GB per file), derived files (PDF page
  layouts: roughly the size of the PDF's text layer, 60 MB for the 1,267-page dictionary),
  outputs (two files per full run, kept per run), the PostgreSQL volume (entries, spans,
  converted entries as JSONB) and Keycloak. Budget about 5x the size of the dictionaries you
  expect to hold; the dev stack with the samples, the 149 MB export and the 1,267-page PDF uses
  about 0.6 GB of object storage.

**Network.**
- Inbound: 80 and 443 to Caddy (Let's Encrypt needs both reachable from the internet; behind an
  institutional proxy, terminate TLS there and set `SITE_ADDRESS=:80`). Keycloak needs its own
  hostname with TLS (`KEYCLOAK_URL`), either a second Caddy site or the same proxy; 8080 must
  not be exposed directly.
- DNS: an A/AAAA record for the app (`SITE_ADDRESS`) and one for Keycloak.
- Outbound HTTPS: to the model providers the lexicographers use (api.anthropic.com,
  api.openai.com, openrouter.ai, generativelanguage.googleapis.com, or an institutional
  OpenAI-compatible endpoint), to the image registries (Docker Hub, quay.io, ghcr.io) and to
  Let's Encrypt. Sandbox containers themselves have no network at all.
- Nothing else listens publicly; PostgreSQL, Garage and the runner stay on the Compose network.

**Docker specifics.**
- The runner mounts `/var/run/docker.sock` and creates the sandbox containers as siblings; build
  the sandbox image on the VM once (`make sandbox-image`, or
  `docker build -t elexifier/sandbox:local packages/sandbox/image`) and after every change to
  `packages/sandbox/image/Dockerfile`. See *Sandbox* below for gVisor (`runsc`) in production.
- Images are built from the repository (`docker compose ... up -d --build`); there is no public
  registry yet. Building needs about 2 GB of free disk and a few minutes; on a small VM build
  elsewhere and `docker save`/`docker load`, or add a registry to CI.
- Keep the Docker data root on the same SSD as the volumes; `postgres-data` and `garage-data`
  are the volumes to back up (see *Backups*).

**Secrets:** generate them before the first start, see *Secrets* below.

**A concrete starting point.** A 4 vCPU / 8 GB / 100 GB SSD cloud VM (the class that costs
EUR 10-20 a month) runs the whole stack for a department; grow RAM before CPU, and add a second
worker when full runs start queueing behind each other.

## Secrets

Ten values in `.env`, all generated, all set before the first `docker compose up`:

```bash
make secrets            # or: python3 deploy/compose/gen_secrets.py   (standard library only)
```

prints a ready-to-paste block. What each one is:

| Variable | Purpose | Notes |
|---|---|---|
| `ELX_SECRET_KEY` | Fernet key that encrypts the lexicographers' provider API keys at rest | **Never lose it**: without it the stored keys are unreadable. Comma-separated list for rotation (new key first). Back it up with the database. |
| `ELX_SESSION_SECRET` | Signs the session cookie | Changing it logs everyone out. |
| `POSTGRES_PASSWORD` | Database password | Set once; changing it later means changing it in PostgreSQL too. |
| `KEYCLOAK_ADMIN`, `KEYCLOAK_ADMIN_PASSWORD` | Keycloak's bootstrap admin | Used at `KEYCLOAK_URL`/admin. |
| `OIDC_CLIENT_SECRET` | The app's OIDC client secret | **Lives in two places:** `.env` and the Keycloak client `elexifier-web` (realm `elexifier` → Clients → Credentials). The realm import ships `dev-client-secret`; edit `deploy/compose/keycloak/realm-elexifier.json` before the first start or set it in the admin console right after. |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Credentials `garage-init` imports into Garage on the first start | Garage requires `GK` + 24 hex characters and a 64-hex secret, which the generator produces. |
| `GARAGE_ADMIN_TOKEN` | Garage's admin API, used by `garage-init` | Garage reads it from the environment (nothing in the config file). |
| `GARAGE_RPC_SECRET` | Garage's node-to-node secret, 32 bytes hex | Set before the first start; the single node is never reachable on that port from outside the Docker network. |
| `SANDBOX_RUNNER_TOKEN` | Shared token between the API and the sandbox runner | |

Only `ELX_SECRET_KEY` and `ELX_SESSION_SECRET` are enforced (the Compose file refuses to start
without them); the others silently fall back to the development defaults (`elexifier`,
`admin`, `dev-client-secret`, …) when missing, so set all of them deliberately. Then delete the
imported development users (`dev@example.com`, `admin@example.com`) from the realm, and set
`ELX_ENABLE_TEST_MODEL=0`.

## Deploying to a server

The step-by-step runbook for the operator, with what the server needs, the install, updates,
backups and troubleshooting, is [`deploy/vm/README.md`](../deploy/vm/README.md) (also unpacked
to `/opt/elexifier/README.md` on the server). In short:

Prerequisites: a VM as in *Requirements* with Ubuntu 24.04 or Debian 12; a DNS name pointing
at it with ports 80 and 443 reachable (or no DNS name: plain http on the server's address,
for a campus-internal try-out); outbound HTTPS; a GitHub account with access to the repository
and a **classic** personal access token with the `read:packages` scope (the images are private;
fine-grained tokens do not work with `ghcr.io`).

```bash
scp deploy/vm/install.sh you@server:          # once, from a machine with the repository
ssh -t you@server sudo ./install.sh
```

`install.sh` asks for the domain, the GitHub user and the token, then: installs Docker if
missing, logs in to `ghcr.io`, unpacks the deployment bundle into `/opt/elexifier`, writes
`/opt/elexifier/.env` with freshly generated secrets (see *Secrets*) and the site URLs, patches
the Keycloak realm for the site (client secret, redirect URIs, no development users), pulls the
images and starts the stack. Non-interactive:
`DOMAIN=dictionaries.example.org GHCR_USER=me GHCR_TOKEN=ghp_… sudo -E ./install.sh`.
Re-running it is safe: an existing `.env` is kept.

Afterwards: the site is at `https://<domain>`, Keycloak's admin console at
`https://<domain>/idp/admin/` (credentials `KEYCLOAK_ADMIN*` in `.env`); create user accounts
there or enable self-registration, and every user adds their model provider key under Settings
in the app. Keycloak's own port 8080 is bound to the server's loopback interface only.

Updating:

```bash
sudo /opt/elexifier/update.sh            # the current main
sudo /opt/elexifier/update.sh m4         # pin a release tag (or sha-<commit> to roll back)
```

`update.sh` fetches the bundle and the images for `IMAGE_TAG` (kept in `.env`), re-applies the
realm patch, restarts the services whose image changed (the API applies migrations on start)
and prunes old images. A weekly cron line (`0 5 * * 1 /opt/elexifier/update.sh`) keeps a
server on `main`; pin a tag instead if changes should be reviewed first.

What is where: `/opt/elexifier` holds `docker-compose.yml`, `.env`, `keycloak/`, `postgres/`,
`garage/` and the scripts; data lives in the Docker volumes `elexifier_postgres-data`,
`elexifier_garage-data`, `elexifier_garage-meta`, `elexifier_caddy-data`; logs with
`cd /opt/elexifier && docker compose logs -f --tail=100`.

## Running from source (local, http://localhost)

```bash
git clone <this repository> elexifier && cd elexifier
cp .env.example .env
make secrets            # paste the output into .env, replacing the development values
make images sandbox-image up        # = docker compose --env-file .env -f deploy/compose/docker-compose.yml ...
```

(Compose reads `.env` from the Compose file's directory, so from the repository root pass
`--env-file .env`, as the Makefile targets do.)

Open http://localhost and sign in
with `dev@example.com` / `dev` (users imported from `deploy/compose/keycloak/realm-elexifier.json`;
`deploy/vm/patch_realm.py` removes them and sets the client secret for a real deployment). The
Keycloak admin console is at http://localhost/idp/admin/ (`KEYCLOAK_ADMIN` /
`KEYCLOAK_ADMIN_PASSWORD`).

## A real domain

One hostname is enough: Caddy serves the app and proxies Keycloak under `/idp`, so one DNS name
and one certificate cover both. In `.env`:

```
SITE_URL=https://dictionaries.example.org
SITE_ADDRESS=dictionaries.example.org
KEYCLOAK_URL=https://dictionaries.example.org/idp
```

Caddy obtains the certificate automatically (ports 80 and 443 must be reachable from the
internet). Behind an institutional reverse proxy that terminates TLS, keep `SITE_URL` and
`KEYCLOAK_URL` as the public `https://` addresses and set `SITE_ADDRESS=:80`. Without a
domain the site runs on plain http; Keycloak then refuses logins from anything but localhost
and private networks ("HTTPS required"), so `deploy/vm/keycloak_ssl.sh` sets the realms'
*require SSL* to `none` for an http site and back to `external` for an https one
(`install.sh` and `update.sh` run it). The Keycloak client
needs the site in its redirect URIs and the real client secret: `install.sh`/`update.sh` do this
with `deploy/vm/patch_realm.py` before the first start; for a from-source deployment run it by
hand on `deploy/compose/keycloak/realm-elexifier.json` before the first `up`, or set both in the
admin console afterwards (realm `elexifier`, client `elexifier-web`).

## Identity

- Local accounts: registration is enabled in the realm; turn it off in the admin console if the
  instance is invitation only.
- ORCID or GitHub: add an Identity Provider in the realm (OpenID Connect v1.0 for ORCID, GitHub
  from the social list).
- Institutional SSO: add a SAML or OIDC identity provider per institution, or broker a federation
  (eduGAIN, CLARIN) through a SATOSA proxy configured as one SAML identity provider in Keycloak.
- Roles: users with the realm role `admin` are Elexifier administrators (they can manage every
  project); everyone else gets `user`.

## Storage

Garage is bootstrapped once by the `garage-init` service (single node, bucket `S3_BUCKET`, access
key from `S3_ACCESS_KEY`/`S3_SECRET_KEY`). Change the keys in `.env` before the first start; the
defaults are development values. For an existing S3-compatible service (Ceph RGW, AWS S3) point
`S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` and `S3_REGION` at it in the compose
file and remove the garage services.

## Models and keys

Users bring their own keys (OpenRouter, OpenAI, Anthropic, Google, DeepSeek, or any OpenAI-compatible
endpoint such as vLLM or Ollama) in Settings. Keys are encrypted with `ELX_SECRET_KEY`. To rotate it,
set `ELX_SECRET_KEY=<new>,<old>`, redeploy, and re-encrypt stored credentials; then drop the old key.

## Upgrades

On a server installed with `install.sh`: `sudo /opt/elexifier/update.sh` (see *Deploying to a
server*). From source:

```bash
git pull
make images up          # docker compose --env-file .env -f deploy/compose/docker-compose.yml up -d --build
make sandbox-image      # when packages/sandbox/image changed
```

The API applies Alembic migrations and creates the job-queue schema on start (`RUN_MIGRATIONS=1`).
When a Procrastinate upgrade ships new SQL migrations, apply them with
`docker compose exec api procrastinate --app elexifier_core.jobs.app schema --migrations-path`
before restarting the worker.

## Moving a project between instances

`python -m elexifier_core.portability export <project> --out file.tgz` on one instance and
`… import file.tgz --owner <e-mail>` on another move one project with its rows and stored
objects, ids preserved, under a new owner; provider credentials and memberships do not travel
and the project's model choice is cleared. The schema revisions must match. On a server:
`docker compose exec -T api python -m elexifier_core.portability import - --owner … < file.tgz`.
The runbook (`deploy/vm/README.md`, *Moving a project between instances*) has the steps.

## Backups

- Database: `docker compose exec postgres pg_dump -U elexifier elexifier | gzip > elexifier-$(date +%F).sql.gz`
  (also back up the `keycloak` database).
- Files: sync the Garage bucket with any S3 client, for example
  `rclone sync garage:elexifier /backups/elexifier-files` using the access key from `.env`.
- Secrets: keep `.env` safe; without `ELX_SECRET_KEY` stored provider keys cannot be decrypted.
- A VM snapshot (Proxmox vzdump, cloud snapshots) of the whole server is crash-consistent and
  fine as a second line; the database dump is the clean restore path.

## Sandbox

Agent-written snippets run in containers created from the sandbox image, one container per
run: no network, an unprivileged user, a read-only root filesystem, a size-capped `/tmp`, all
capabilities dropped, `no-new-privileges`, and limits on processes, memory and CPU; a run is
killed at its timeout. The runner (`sandbox-runner`) is the only container that reaches the
Docker socket; it never bind-mounts host paths (inputs go in and outputs come out through the
daemon's archive API), so it works as a sibling container.

- Build the image on the Docker host before the first run: `make sandbox-image` (or
  `docker build -t elexifier/sandbox:local packages/sandbox/image`); `SANDBOX_IMAGE` names it.
  Rebuild it after changing `packages/sandbox/image/Dockerfile` (the libraries snippets may
  import: lxml, pdfplumber, pypdfium2, jsonschema, xmlschema, saxonche).
- `GET http://sandbox-runner:8010/healthz` reports whether the daemon answers and the image is
  present.
- Limits per run (the API sets them; defaults in `elexifier_core.sandbox_contract`): 300 s
  (600 s for conversion steps), 2 CPUs, 2 GB of memory with no swap, 256 processes, 256 MB of
  `/tmp`, 200 MB of outputs. A snippet that exceeds the memory limit is killed by the kernel
  (exit code 137), one that exceeds the timeout is killed by the runner (exit code 137 and a
  `[runner] killed after N s` note), outputs beyond the cap are refused as a whole, `/tmp`
  beyond its size fails with `OSError`, and a process bomb stops at the pid limit.
- What is not limited: disk writes to `out/` up to the output cap, and CPU time within the
  share. A snippet cannot see other runs, the host, or the network.
- With the default `runc`, isolation rests on Linux namespaces, seccomp and the limits above;
  the socket mount means the runner container is as trusted as the daemon, so keep it off
  public networks and give the host no other tenants.
- gVisor for production: install `runsc` from the gVisor apt repository
  (https://gvisor.dev/docs/user_guide/install/), register it with the daemon
  (`/etc/docker/daemon.json`: `{"runtimes": {"runsc": {"path": "/usr/bin/runsc"}}}`),
  restart Docker, check `docker info --format '{{json .Runtimes}}'` lists `runsc` and that
  `docker run --rm --runtime=runsc elexifier/sandbox:local python -c 'print(1)'` prints 1, then
  set `SANDBOX_RUNTIME=runsc` in `.env` and restart the runner. Every run then gets a user-space
  kernel between the snippet and the host. Docker Desktop (macOS, Windows) has no `runsc`;
  development stays on `runc`.
- Verify an installation: `uv run pytest packages/sandbox -m docker` runs real containers
  (hello world with an input and an output, no network, timeout, read-only root, memory,
  processes, output cap, `/tmp`); the tests skip when the daemon or the image is missing.

## Health

`GET /healthz` on the site reports database and event-bus status. Container health checks cover
the API and the sandbox runner; `docker compose ps` shows them.
