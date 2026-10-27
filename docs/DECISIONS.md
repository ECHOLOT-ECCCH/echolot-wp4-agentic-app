# Decisions

Newest at the bottom. Each entry: date, what was decided, why. Elexifier's log is in
`docs/archive/elexifier/DECISIONS.md`.

## 2026-10-27 The repository becomes a lightweight agentic workbench for ECHOLOT

- The Elexifier app (dictionary conversion to DMLex, last commit `98999b1`) is cut down to a POC
  for per-record tasks over datasets: entity reconciliation through the ECHOLOT service first,
  enrichment and similar tasks later.
- The workflow is: files and preview, a chat that agrees what to do with each record, a Python
  script the agent writes (`process(record, ctx)`), runs on a Prefect deployment next to the
  services, and results to review and export. Task knowledge lives in service docs and scripts,
  not in the platform.
- Kept from Elexifier: the Pydantic AI runtime and `VercelAIAdapter` streaming, UI-message
  persistence, the approval flow, the tool result contract, the assistant-ui chat, the
  three-pane shell and the file upload.
- Dropped: XML/PDF/DMLex pipeline, Keycloak and sessions, bring-your-own-key credentials and the
  model catalog, Postgres, Procrastinate, NOTIFY/SSE, Garage, Caddy, the Docker sandbox runner
  and custom steps, and the deployment bundle.
- Execution moves to Prefect because ECHOLOT already uses it, the services are reachable from
  its host (ECHOLOT binds to 127.0.0.1:5000), and the service keys can stay there. The app talks
  to the Prefect REST API with httpx, verified against Prefect 3.7.5.dev5:
  - `/deployments/name/{flow}/{deployment}`, `/deployments/{id}/create_flow_run`,
    `/flow_runs/{id}`, `/flow_runs/{id}/set_state`.
  - Basic auth through `PREFECT_API_AUTH_STRING` / `PREFECT_SERVER_API_AUTH_STRING`.
  - `PREFECT_RUNNER_POLL_FREQUENCY` defaults to 10 s.
- Prefect parameters carry the script and URLs, not data. The runner pulls input and pushes
  progress and results to the app with a per-run token.
- Accepted for the POC: scripts run unsandboxed on the runner host after approval, and the app
  has no login (it binds to 127.0.0.1). Hardening is listed in PLAN section 14.
