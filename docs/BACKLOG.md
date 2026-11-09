# Backlog

Ideas and improvements that are out of scope for the current task. One line each, newest at the bottom.
Move an item into docs/PLAN.md when it is scheduled. Elexifier's backlog is in `docs/archive/elexifier/BACKLOG.md`.

- Prefect Docker work pool so a script cannot read the runner host's environment or disk; a service gateway so keys are not in the script's process at all.
- Object-store data plane (presigned GET/PUT URLs) for when the runner host cannot reach the app; the harness stays the same.
- Single sign-on (the OIDC BFF from commit 98999b1) next to local accounts, for testers whose institution has an identity provider.
- Password reset by e-mail and invitations by link, instead of an admin handing out temporary passwords.
- Sharing a workspace with other users (read-only or editing); an admin view of everyone's workspaces.
- Usage and cost per user and model from `agent_runs.usage`; spending limits on the server's own model.
- Enrichment as the second use case: verify reconciliation candidates against Wikidata's public API (wbgetentities: description, birth and death dates) as a second service.
- Dataset-level scripts (`process_all(records, ctx)`) for deduplication and clustering.
- A result cache keyed by service, request body and script hash, so re-runs do not repeat identical service calls.
- XLSX input (openpyxl), Alembic migrations, a one-container deployment (server with the built web), conversation compaction, cost per turn.
