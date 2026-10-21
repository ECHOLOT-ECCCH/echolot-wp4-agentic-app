# Deploying Elexifier to a server

A runbook for the person setting up the machine. The server runs the published container
images; it never needs the source code, a build toolchain, Git, Python packages or Node.
Sizing, ports and the design of the stack are in [`docs/self-hosting.md`](../../docs/self-hosting.md).

## 1. What the server needs

- **OS:** Ubuntu 24.04 LTS or Debian 12, x86_64 (the images are built for `linux/amd64`), a
  standard installation with a user that can `sudo`. On Proxmox: a KVM VM (not an LXC
  container), CPU type `host`, 4-8 vCPU, 16384 MB RAM, 100 GB SSD; `apt install
  qemu-guest-agent` is optional but convenient.
- **Installed software: nothing beyond the standard system.** `install.sh` installs Docker
  Engine with the Compose plugin itself (from get.docker.com) and uses only `curl` and
  `python3`, which it installs if a minimal image lacks them.
- **Network, outbound HTTPS to:** `ghcr.io` (our images), `registry-1.docker.io` and `quay.io`
  (PostgreSQL, Garage, Keycloak), Let's Encrypt (when a domain is used), and the model providers
  the lexicographers will use (`api.anthropic.com`, `api.openai.com`, `openrouter.ai`,
  `generativelanguage.googleapis.com`, or an institutional endpoint). Sandbox containers
  themselves get no network.
- **Network, inbound:** ports 80 and 443 from wherever the users are (and from the internet,
  if Let's Encrypt is to issue the certificate). Nothing else listens publicly.
- **A DNS name** pointing at the server, e.g. `dictionaries.example.org`. Without one the
  install falls back to plain `http://<server address>`, good enough for a first try inside
  the campus network (the scripts then switch off Keycloak's "HTTPS required" rule for the
  realms, since Keycloak refuses plain http from anything but localhost and private
  networks; passwords travel unencrypted, so keep that to a pilot). Add the name later by
  editing `.env` (see 6); the rule is switched back on.

## 2. What you need at hand

- A GitHub account with access to the repository and a **classic** personal access token with
  the single scope **`read:packages`**: GitHub → Settings → Developer settings → Personal
  access tokens → **Tokens (classic)** → Generate new token (classic). The page opens on
  *Fine-grained tokens* by default; those start with `github_pat_` and `ghcr.io` answers
  `denied: denied` to them at login. A classic token starts with `ghp_`. The images are
  private, so the scope is needed to pull them (a token without it still *logs in*, and then
  every pull says `denied`). The token is stored on the server by `docker login` (root's
  `~/.docker/config.json`); it can only read packages. Check it on the server before anything
  else:

  ```bash
  sudo docker login ghcr.io -u <github user>      # paste the token as the password
  sudo docker manifest inspect ghcr.io/andrazrepar/elexifier-deploy:main > /dev/null && echo token OK
  ```
- The domain, if any.

## 3. Install

From a machine that has the repository (only this one file is copied; password logins are
fine, both commands prompt for it):

```bash
scp deploy/vm/install.sh <user>@<server>:        # note the trailing colon
ssh -t <user>@<server> sudo ./install.sh          # -t: a terminal for sudo and the questions
```

Three things that look like "nothing happens": `scp` prints *nothing* when it succeeds (check
with `ssh <user>@<server> ls -l install.sh`); without the trailing colon it quietly copies the
script to a *local* file named `<user>@<server>` instead; and if the server is reachable only
from the institution's network, `scp`/`ssh` hang for a minute before "Operation timed out",
so connect to the VPN first (or use the console, below). If `scp` itself misbehaves (some
servers disable the SFTP subsystem it uses), send the file through `ssh` instead; this works
with a password too and confirms:

```bash
ssh <user>@<server> 'cat > install.sh && chmod +x install.sh && echo copied' < deploy/vm/install.sh
```

**Without copying anything** (the simplest path when file transfer is awkward; works in an
`ssh` session as well as at the Proxmox console): the script is also inside the deployment
bundle image, so four lines typed *on the server* bootstrap everything:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo docker login ghcr.io -u <github user>          # paste the read:packages token
sudo docker create --name bundle ghcr.io/andrazrepar/elexifier-deploy:main /none && sudo docker cp bundle:/bundle/install.sh . && sudo docker rm bundle
sudo ./install.sh
```

The script asks for the domain (or empty), the GitHub user and the token, then:

1. installs Docker if it is missing;
2. logs in to `ghcr.io`;
3. unpacks the deployment bundle (Compose file, Keycloak realm, configuration, these scripts)
   into `/opt/elexifier`;
4. writes `/opt/elexifier/.env` with freshly generated secrets and the site URLs;
5. patches the Keycloak realm for the site: the app's client secret, the redirect URIs, and no
   development users;
6. pulls the images, starts the stack and waits until the API is healthy.

About five minutes, most of it downloading. Non-interactive form (for automation):

```bash
DOMAIN=dictionaries.example.org GHCR_USER=me GHCR_TOKEN=ghp_... sudo -E ./install.sh
```

Re-running `install.sh` is safe: an existing `.env` is kept.

## 4. After the install

| | |
|---|---|
| The app | `https://<domain>` (or `http://<address>`) |
| Keycloak admin console | `https://<domain>/idp/admin/` — user and password: `KEYCLOAK_ADMIN` / `KEYCLOAK_ADMIN_PASSWORD` in `/opt/elexifier/.env` |
| Health | `https://<domain>/healthz` → `{"status":"ok","database":"ok","events":"ok"}` |

Then:

- Create user accounts in Keycloak (realm **elexifier** → Users), or enable self-registration
  (Realm settings → Login). Users with the realm role `admin` are Elexifier administrators.
- Every user adds their own model provider API key in the app under **Settings**; the server
  holds no provider key of its own.
- **Back up `/opt/elexifier/.env` now.** It holds `ELX_SECRET_KEY`, the key that encrypts the
  users' provider keys; without it they cannot be recovered.

## 5. Updating

Every push to the repository's `main` branch publishes new images; git tags (`m3`, `m4`, …)
publish images with that tag.

```bash
sudo /opt/elexifier/update.sh            # move to the current main
sudo /opt/elexifier/update.sh m4         # pin a release tag
sudo /opt/elexifier/update.sh sha-2645ab1   # or an exact commit, e.g. to roll back
```

`update.sh` fetches the bundle and the images for the tag (remembered in `.env` as
`IMAGE_TAG`), restarts only what changed (the API applies database migrations on start) and
removes old images. Updating takes a minute; users see the app unavailable for a few seconds.
For unattended updates on `main`: `echo '0 5 * * 1 /opt/elexifier/update.sh' | sudo tee
/etc/cron.d/elexifier-update` (one line, weekly, Monday 05:00).

## 6. Where things are and everyday commands

```
/opt/elexifier/.env                 configuration and secrets (back it up)
/opt/elexifier/docker-compose.yml   the stack (overwritten by update.sh; edit .env, not this)
/opt/elexifier/keycloak/, postgres/, garage/   configuration the containers read
Docker volumes elexifier_postgres-data, elexifier_garage-data, elexifier_garage-meta,
elexifier_caddy-data                the data
```

```bash
cd /opt/elexifier
docker compose ps                           # what runs, health
docker compose logs -f --tail=100           # all logs (add a service name: api, worker, caddy, keycloak)
docker compose restart api worker           # after editing .env
docker compose down && docker compose up -d # full restart
```

Changing the address later: edit `SITE_URL`, `SITE_ADDRESS`, `KEYCLOAK_URL` (and, behind a
reverse proxy, `TRUSTED_PROXY_CIDR`) in `.env` and run `sudo /opt/elexifier/update.sh`; it
restarts what the change touches and tells Keycloak about the new site (require-SSL rule and
the client's redirect URIs). The two ways to give the site a name:

**A. The server is reachable from the internet on ports 80 and 443.** A DNS record points at
it, `.env` gets `SITE_URL=https://<name>`, `SITE_ADDRESS=<name>`,
`KEYCLOAK_URL=https://<name>/idp`, and Caddy obtains the certificate itself.

**B. The server is reachable only from the campus or a VPN** (the usual case): the name is
served by the institution's public reverse proxy, which holds the certificate and forwards to
the server. Ask the proxy's administrator for a virtual host for the name; for nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name beta.elexifier.ijs.si;                 # the site's name
    ssl_certificate     /etc/letsencrypt/live/beta.elexifier.ijs.si/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/beta.elexifier.ijs.si/privkey.pem;
    client_max_body_size 2g;                           # dictionary uploads
    location / {
        proxy_pass http://194.249.231.48:80;           # the Elexifier server
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Connection "";
        proxy_read_timeout 3600s;                      # chat streams and job events stay open
        proxy_send_timeout 3600s;
        proxy_buffering off;                           # streamed answers arrive as produced
        proxy_request_buffering off;                   # uploads stream through
    }
}
server {
    listen 80;
    server_name beta.elexifier.ijs.si;
    return 301 https://$host$request_uri;
}
```

If you administer that nginx yourself (CentOS/Rocky): save only the first `server` block as
`/etc/nginx/conf.d/<name>.conf`, with `listen 80;` in place of the `listen 443 …` line and
without the two `ssl_` lines, run `nginx -t && systemctl reload nginx`, and check that
`curl -s http://<name>/healthz` shows the app's JSON (a 502 on a host with SELinux enforcing
means `setsebool -P httpd_can_network_connect 1` is missing). Then
`certbot --nginx -d <name> --redirect` obtains the certificate and turns the block into the
https one above plus the redirect (`dnf install certbot python3-certbot-nginx` if absent);
check `curl -s https://<name>/healthz`. Switch the server's `.env` below only after that works.

(Apache or HAProxy need the same: the whole host forwarded to the server's port 80, the
`X-Forwarded-*` headers, long read timeouts, no response buffering, a 2 GB body limit.) Then
on the server, in `/opt/elexifier/.env`:

```
SITE_URL=https://beta.elexifier.ijs.si
SITE_ADDRESS=:80
KEYCLOAK_URL=https://beta.elexifier.ijs.si/idp
TRUSTED_PROXY_CIDR=194.249.231.96/32      # the proxy's address: its X-Forwarded-* are trusted
```

and `sudo /opt/elexifier/update.sh`. Check with `curl -s https://<name>/healthz` and a login.
`TRUSTED_PROXY_CIDR` makes Caddy pass the proxy's `X-Forwarded-*` headers through, so Keycloak,
the API and the logs see the visitor's address and the https scheme (login cookies marked
Secure); without it every visitor appears to come from the proxy. After the switch the server
answers by its name: a visit to the bare address is sent to the name as soon as it logs in.

## 7. Moving a project between instances

A project (its files, documents, spans, mappings, runs, steps, conversations) can be exported
on one instance and imported on another, for instance from a laptop's development stack to the
server, with its ids preserved. Provider keys do not travel (they are encrypted with the
exporting instance's key; the owner enters theirs once and picks the model per project again),
and the importing user becomes the owner.

On the exporting side (a development checkout):

```bash
uv run python -m elexifier_core.portability export "Šega poslovni slovar" --out sega.tgz
```

Copy the file to the server (`scp sega.tgz andrazr@<server>:`), then on the server, as the
user who will own it (they must have logged in to the site at least once):

```bash
cd /opt/elexifier
sudo docker compose exec -T api python -m elexifier_core.portability import - --owner you@example.org < ~/sega.tgz
```

Both sides must run the same database schema revision (update the older one first); an
archive is refused when the project already exists on the target.

## 8. Backups

- Database: `cd /opt/elexifier && docker compose exec -T postgres pg_dumpall -U elexifier | gzip > /var/backups/elexifier-$(date +%F).sql.gz`
  (both the app and the Keycloak database).
- Files: `docker run --rm -v elexifier_garage-data:/data -v /var/backups:/out alpine tar czf /out/elexifier-files-$(date +%F).tgz -C /data .`
  (also `elexifier_garage-meta`), or `rclone sync` against the bucket with the keys in `.env`.
- `.env`, as above.
- A Proxmox VM backup of the whole machine is a good second line (crash-consistent); the dumps
  are the clean restore path.

## 9. If something goes wrong

| Symptom | Cause and fix |
|---|---|
| `docker login` says `denied: denied` | The token is a fine-grained one (`github_pat_…`), or the GitHub password was pasted; create a classic token (`ghp_…`). |
| Login succeeds but every pull says `denied` | The classic token lacks the `read:packages` scope, or the account has no access to the repository. |
| `manifest unknown` for a tag | That tag was never published: CI has not finished for the latest push, or the git tag was not pushed. `update.sh main` goes back to the latest `main`. |
| No certificate / browser warning | DNS does not point at the server, or 80/443 are not reachable from the internet. `docker compose logs caddy`. Behind an institutional proxy that does TLS, set `SITE_ADDRESS=:80` and keep the `https://` URLs. |
| Keycloak shows "We are sorry… HTTPS required" | The site runs on plain http at a public address and the realm still requires SSL: `sudo /opt/elexifier/keycloak_ssl.sh` (install.sh and update.sh run it; bundles before 2026-10-08 did not). Behind a reverse proxy with an https site it means `KEYCLOAK_URL` in `.env` is still the http address: set `https://<name>/idp` and run `update.sh`. |
| The name shows another server's page or a 404 (`Server: nginx` while the app runs Caddy) | DNS points at the institution's proxy and that proxy has no virtual host for the name yet (section 6, B). `dig +short <name>` shows where the name goes. |
| Login fails with "Invalid parameter: redirect_uri" | The site's URL is missing from the Keycloak client; `sudo /opt/elexifier/keycloak_ssl.sh` adds `SITE_URL/*`. |
| Keycloak accepts the password but the app says "Login could not be completed" | `docker compose logs api` shows `oidc.exchange_failed` with `unauthorized_client`: Keycloak's client secret differs from `OIDC_CLIENT_SECRET` in `.env` (for example after the realm was imported again); `sudo /opt/elexifier/keycloak_ssl.sh` sets it from `.env`. |
| `garage-init` stays `Waiting` for two minutes and `api` never starts | `docker compose logs garage-init`: `garage admin API not reachable` with 401s means `GARAGE_ADMIN_TOKEN` in `.env` does not match what Garage runs with (bundles before 2026-10-08 had the token fixed in the config file; set `GARAGE_ADMIN_TOKEN=dev-garage-admin-token` in `.env` or update). |
| The app says the sandbox is unavailable | `docker compose logs sandbox-runner`; the sandbox image is pulled by `install.sh`/`update.sh`, `docker images | grep sandbox` shows it. |
| Everything else | `docker compose ps` (health column), `docker compose logs <service>`, and `curl -s https://<domain>/healthz`. Send the output with the report. |

## 10. Removing

`cd /opt/elexifier && docker compose down -v` stops everything and **deletes the data
volumes**; `rm -rf /opt/elexifier` removes the configuration. Leave out `-v` to keep the data.
