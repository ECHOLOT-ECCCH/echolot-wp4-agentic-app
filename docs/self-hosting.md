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
- For production, install gVisor and set `SANDBOX_RUNTIME=runsc` so snippets run under a
  user-space kernel (the daemon must list `runsc` in its runtimes). With the default `runc`,
  isolation rests on namespaces, seccomp and the limits above; the socket mount means the
  runner container is as trusted as the daemon, keep it off public networks.
- Disk: outputs are refused beyond the run's `max_output_mb`; the work volume is removed with
  the container.

## Health

`GET /healthz` on the site reports database and event-bus status. Container health checks cover
the API and the sandbox runner; `docker compose ps` shows them.
