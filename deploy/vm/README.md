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
  the campus network; add the name later by editing `.env` (see 6).

## 2. What you need at hand

- A GitHub account with access to the repository and a **classic** personal access token with
  the single scope **`read:packages`** (GitHub → Settings → Developer settings → Personal
  access tokens → Tokens (classic)). The images are private; fine-grained tokens do not work
  with `ghcr.io`. The token is stored on the server by `docker login` (root's
  `~/.docker/config.json`); it can only read packages.
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

**Without SSH access from your machine** (for instance only the Proxmox console): the script
is also inside the deployment bundle image, so four lines typed at the VM's console bootstrap
everything with nothing copied over:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo docker login ghcr.io -u <github user>          # paste the read:packages token
sudo docker create --name b ghcr.io/andrazrepar/elexifier-deploy:main /none && sudo docker cp b:/bundle/install.sh . && sudo docker rm b
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

Changing the address later: edit `SITE_URL`, `SITE_ADDRESS`, `KEYCLOAK_URL` in `.env`, run
`sudo /opt/elexifier/update.sh` (it re-patches the realm file, which only matters for a fresh
Keycloak database), and add the new URL to the Keycloak client's redirect URIs in the admin
console (realm elexifier → Clients → elexifier-web → Valid redirect URIs: `https://<new>/*`).

## 7. Backups

- Database: `cd /opt/elexifier && docker compose exec -T postgres pg_dumpall -U elexifier | gzip > /var/backups/elexifier-$(date +%F).sql.gz`
  (both the app and the Keycloak database).
- Files: `docker run --rm -v elexifier_garage-data:/data -v /var/backups:/out alpine tar czf /out/elexifier-files-$(date +%F).tgz -C /data .`
  (also `elexifier_garage-meta`), or `rclone sync` against the bucket with the keys in `.env`.
- `.env`, as above.
- A Proxmox VM backup of the whole machine is a good second line (crash-consistent); the dumps
  are the clean restore path.

## 8. If something goes wrong

| Symptom | Cause and fix |
|---|---|
| `docker login` fails, or `pull access denied` | The token is not a classic token, or lacks `read:packages`, or the account has no access to the repository. |
| `manifest unknown` for a tag | That tag was never published: CI has not finished for the latest push, or the git tag was not pushed. `update.sh main` goes back to the latest `main`. |
| No certificate / browser warning | DNS does not point at the server, or 80/443 are not reachable from the internet. `docker compose logs caddy`. Behind an institutional proxy that does TLS, set `SITE_ADDRESS=:80` and keep the `https://` URLs. |
| Login ends in an error from Keycloak about the redirect URI | `SITE_URL` in `.env` and the client's redirect URIs in Keycloak disagree; fix the URIs in the admin console. |
| The app says the sandbox is unavailable | `docker compose logs sandbox-runner`; the sandbox image is pulled by `install.sh`/`update.sh`, `docker images | grep sandbox` shows it. |
| Everything else | `docker compose ps` (health column), `docker compose logs <service>`, and `curl -s https://<domain>/healthz`. Send the output with the report. |

## 9. Removing

`cd /opt/elexifier && docker compose down -v` stops everything and **deletes the data
volumes**; `rm -rf /opt/elexifier` removes the configuration. Leave out `-v` to keep the data.
