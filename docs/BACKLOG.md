# Backlog

Ideas and improvements that are out of scope for the current task. One line each, newest at the bottom.
Move an item into docs/PLAN.md when it is scheduled. Elexifier's backlog is in `docs/archive/elexifier/BACKLOG.md`.

- Prefect Docker work pool so a script cannot read the runner host's environment or disk; a service gateway so keys are not in the script's process at all.
- Object-store data plane (presigned GET/PUT URLs) for when the runner host cannot reach the app; the harness stays the same.
- Login (OIDC BFF from commit 98999b1) once more than a trusted group uses the app.
- Enrichment as the second use case: verify reconciliation candidates against Wikidata's public API (wbgetentities: description, birth and death dates) as a second service.
- Dataset-level scripts (`process_all(records, ctx)`) for deduplication and clustering.
- A result cache keyed by service, request body and script hash, so re-runs do not repeat identical service calls.
- XLSX input (openpyxl), Alembic migrations, a one-container deployment (server with the built web), conversation compaction, cost per turn.
