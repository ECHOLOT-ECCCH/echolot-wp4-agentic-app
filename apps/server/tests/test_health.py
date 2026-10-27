from __future__ import annotations

import httpx

from echolot_app.settings import Settings


async def test_healthz_reports_the_database(client: httpx.AsyncClient, settings: Settings) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
    assert (settings.data_dir / "app.db").is_file()
