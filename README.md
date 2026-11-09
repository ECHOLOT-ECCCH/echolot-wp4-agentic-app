# ECHOLOT Agentic Workbench

A proof-of-concept web app for per-record tasks over datasets, such as entity reconciliation
against the ECHOLOT reconciliation service, and later enrichment:

1. Upload input files (CSV, TSV, JSON, JSONL) and preview them.
2. Agree with an LLM agent in the chat on what should happen to each record.
3. The agent writes the function as a Python script, which you can read and edit.
4. The script runs on a sample, then on the whole dataset, as a job on a Prefect deployment
   that sits next to the services.
5. The results come back as a table to review and export.

Three panes: files, task, scripts and runs on the left, a preview in the middle, the chat on
the right. Each tester signs in with an account an admin made, works in private workspaces and
chooses the agent's model: the server's, or one from Anthropic, OpenAI, Google, Mistral,
OpenRouter or DeepSeek with their own key.

## Status

Cut down from the Elexifier app (dictionary conversion; last commit `98999b1`). Built and
tested: workspaces, file upload with a paged records view, task notes, the agent chat with
approvals, versioned scripts, runs on a local executor or a Prefect deployment (the runner in
`runner/`), results with reconciliation candidates, per-row decisions and CSV/JSONL export, accounts with
private workspaces, and bring-your-own model keys (PLAN 7.11).
Still open: the checks with a real model and the owner's own data (M1.7, M3.1, M3.4). See
[docs/PLAN.md](docs/PLAN.md) for the architecture, contracts, milestones and current status,
[docs/DECISIONS.md](docs/DECISIONS.md) for the decision log, and
[docs/runner-host.md](docs/runner-host.md) for setting up the runner next to the services.

## Requirements

- Python 3.12 (managed by [uv](https://docs.astral.sh/uv/))
- Node.js 22+ and pnpm (via `corepack enable`)
- GNU Make
- For real runs: a Prefect 3 server and a runner host that can reach the services (PLAN M2)
- For reconciliation: the ECHOLOT service,
  [ECHOLOT-ECCCH/echolot-wp4-api-service](https://github.com/ECHOLOT-ECCCH/echolot-wp4-api-service)

## Quick start (development)

```bash
make setup             # uv sync, pnpm install, playwright browsers
cp .env.example .env   # the test model needs no key; set ECHO_MODEL_* for a real one
# to call services from scripts: list them in ./services.toml (runner/services.example.toml)
# and put their keys in ./runner.env, e.g. ECHOLOT_API_KEY=... (both gitignored)
uv run echolot-admin create-user you@example.org --name "You" --admin   # prints a password
make api               # FastAPI on 127.0.0.1:8000, SQLite and files under ./data
make web               # Vite on :5173, sign in with the account above
make check             # lint, types, unit tests
make e2e               # browser smoke tests (start their own servers)
```

## Running it for testers

- **Accounts.** There is no sign-up. Create the first admin on the server with
  `uv run echolot-admin create-user <e-mail> --admin`; then add testers under Settings → Users (each
  gets a temporary password, shown once, that they change under Settings → Account). The same
  command resets passwords and disables accounts (`echolot-admin --help`).
- **https only.** Put the app behind a reverse proxy with TLS and set `ECHO_PUBLIC_URL` to the
  https address: the session cookie is then marked Secure. Run uvicorn with
  `--proxy-headers --forwarded-allow-ips=<proxy address>`, so sign-in throttling sees the
  testers' addresses, not the proxy's. The chat streams: the proxy must not buffer
  `/api/…/chat` (nginx: the app already sends `X-Accel-Buffering: no`).
- **Secrets.** Set `ECHO_SECRET_KEY` (a Fernet key: `python -c "from cryptography.fernet import
  Fernet; print(Fernet.generate_key().decode())"`); it encrypts the testers' model keys. Without
  it a key is generated into the data directory, next to the database it protects. Changing it
  makes stored keys unreadable (testers re-enter them); to rotate, list the new key first,
  comma-separated with the old one.
- **Models.** Testers bring their own keys under Settings → Model keys. If `ECHO_MODEL_API_KEY`
  is set, the server's `ECHO_MODEL_PROVIDER`/`ECHO_MODEL_ID` is offered to everyone as "Server
  default", on your bill; leave it unset to require own keys. `ECHO_ALLOW_CUSTOM_ENDPOINTS=1`
  lets testers add their own OpenAI-compatible endpoint (the server then calls URLs they enter).

## License

[MIT](LICENSE).
