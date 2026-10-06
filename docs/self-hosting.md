# Self-hosting Elexifier

Everything runs from one Docker Compose file. The same stack serves the hosted instance and an
institution's own deployment.

## What runs

| Service | Image | Role |
|---|---|---|
| caddy | built from `deploy/compose/Dockerfile.web` | Serves the web app, proxies `/api`, `/auth`, `/healthz` to the API, automatic HTTPS for a domain |
| api | built from `deploy/compose/Dockerfile.api` | FastAPI application; applies database migrations on start |
| worker | same image as api | Background jobs (conversions, PDF processing) |
| sandbox-runner | built from `deploy/compose/Dockerfile.sandbox-runner` | Runs agent-written snippets in isolated containers (milestone 3) |
| postgres | `postgres:16` | Application data, job queue, Keycloak database |
| garage | `dxflrs/garage` | S3-compatible object storage for uploaded and generated files |
| keycloak | `quay.io/keycloak/keycloak` | Identity provider: local accounts, ORCID/GitHub login, institutional SAML/OIDC federation |

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
  `docker build -t elexifier-sandbox:local packages/sandbox/image`) and after every change to
  `packages/sandbox/image/Dockerfile`. See *Sandbox* below for gVisor (`runsc`) in production.
- Images are built from the repository (`docker compose ... up -d --build`); there is no public
  registry yet. Building needs about 2 GB of free disk and a few minutes; on a small VM build
  elsewhere and `docker save`/`docker load`, or add a registry to CI.
- Keep the Docker data root on the same SSD as the volumes; `postgres-data` and `garage-data`
  are the volumes to back up (see *Backups*).

**Secrets to generate before the first start** (`.env`): `ELX_SECRET_KEY` (Fernet),
`ELX_SESSION_SECRET`, `OIDC_CLIENT_SECRET` (set the same value on the Keycloak client),
`KEYCLOAK_ADMIN_PASSWORD`, `POSTGRES_PASSWORD`, `S3_ACCESS_KEY`/`S3_SECRET_KEY`,
`SANDBOX_RUNNER_TOKEN`. Delete the imported dev users (`dev@example.com`, `admin@example.com`)
from the realm.

**A concrete starting point.** A 4 vCPU / 8 GB / 100 GB SSD cloud VM (the class that costs
EUR 10-20 a month) runs the whole stack for a department; grow RAM before CPU, and add a second
worker when full runs start queueing behind each other.

## First start (local, http://localhost)

```bash
git clone <this repository> elexifier && cd elexifier
cp .env.example .env
# generate real secrets
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # -> ELX_SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(48))"                                 # -> ELX_SESSION_SECRET
docker compose -f deploy/compose/docker-compose.yml up -d --build
```

Open http://localhost and sign in with `dev@example.com` / `dev` (users imported from
`deploy/compose/keycloak/realm-elexifier.json`; delete them for a real deployment). The Keycloak
admin console is at http://localhost:8080 (`KEYCLOAK_ADMIN` / `KEYCLOAK_ADMIN_PASSWORD`).

## A real domain

In `.env`:

```
SITE_URL=https://dictionaries.example.org
SITE_ADDRESS=dictionaries.example.org
KEYCLOAK_URL=https://auth.example.org
```

Caddy obtains certificates for `SITE_ADDRESS` automatically (ports 80 and 443 must be reachable).
Put Keycloak behind TLS too: either run a second Caddy site for `auth.example.org` proxying to
`keycloak:8080` (Keycloak is configured with `KC_PROXY_HEADERS=xforwarded`), or terminate TLS with
your existing reverse proxy. Then add `https://dictionaries.example.org/*` to the client's redirect
URIs in the Keycloak admin console (realm `elexifier`, client `elexifier-web`) and set a new client
secret (`OIDC_CLIENT_SECRET`).

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

```bash
git pull
docker compose -f deploy/compose/docker-compose.yml up -d --build
```

The API applies Alembic migrations and creates the job-queue schema on start (`RUN_MIGRATIONS=1`).
When a Procrastinate upgrade ships new SQL migrations, apply them with
`docker compose exec api procrastinate --app elexifier_core.jobs.app schema --migrations-path`
before restarting the worker.

## Backups

- Database: `docker compose exec postgres pg_dump -U elexifier elexifier | gzip > elexifier-$(date +%F).sql.gz`
  (also back up the `keycloak` database).
- Files: sync the Garage bucket with any S3 client, for example
  `rclone sync garage:elexifier /backups/elexifier-files` using the access key from `.env`.
- Secrets: keep `.env` safe; without `ELX_SECRET_KEY` stored provider keys cannot be decrypted.

## Sandbox

Agent-written snippets run in containers created from the sandbox image, one container per
run: no network, an unprivileged user, a read-only root filesystem, a size-capped `/tmp`, all
capabilities dropped, `no-new-privileges`, and limits on processes, memory and CPU; a run is
killed at its timeout. The runner (`sandbox-runner`) is the only container that reaches the
Docker socket; it never bind-mounts host paths (inputs go in and outputs come out through the
daemon's archive API), so it works as a sibling container.

- Build the image on the Docker host before the first run: `make sandbox-image` (or
  `docker build -t elexifier-sandbox:local packages/sandbox/image`); `SANDBOX_IMAGE` names it.
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
  `docker run --rm --runtime=runsc elexifier-sandbox:local python -c 'print(1)'` prints 1, then
  set `SANDBOX_RUNTIME=runsc` in `.env` and restart the runner. Every run then gets a user-space
  kernel between the snippet and the host. Docker Desktop (macOS, Windows) has no `runsc`;
  development stays on `runc`.
- Verify an installation: `uv run pytest packages/sandbox -m docker` runs real containers
  (hello world with an input and an output, no network, timeout, read-only root, memory,
  processes, output cap, `/tmp`); the tests skip when the daemon or the image is missing.

## Health

`GET /healthz` on the site reports database and event-bus status. Container health checks cover
the API and the sandbox runner; `docker compose ps` shows them.
