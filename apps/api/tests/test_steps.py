"""Custom conversion steps: create from a code artifact, list, update, reorder, delete."""

from __future__ import annotations

import uuid

import httpx

from elexifier_core.db.models import File
from elexifier_core.storage import make_key

from .conftest import AppState, LoggedIn


async def _project(client: httpx.AsyncClient) -> str:
    return (
        await client.post("/api/projects", json={"name": "Steps", "source_type": "xml"})
    ).json()["id"]


async def _artifact(app_state: AppState, pid: str, name: str, mime: str, body: bytes) -> str:
    """A code artifact the way run_code stores one."""
    file_id = uuid.uuid4()
    key = make_key(pid, "artifact", str(file_id), name)
    stat = await app_state.storage.put(key, body, content_type=mime)
    async with app_state.db.session() as db:
        db.add(
            File(
                id=file_id,
                project_id=uuid.UUID(pid),
                kind="artifact",
                name=name,
                s3_key=key,
                size=stat.size,
                sha256=stat.sha256 or "",
                mime=mime,
                meta={"code_run": str(uuid.uuid4()), "role": "code", "purpose": "test"},
            )
        )
        await db.commit()
    return str(file_id)


async def test_steps_crud_and_reorder(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    logged_in.apply(client)
    pid = await _project(client)
    base = f"/api/projects/{pid}/steps"
    assert (await client.get(base)).json() == []

    code_a = await _artifact(app_state, pid, "code-a.py", "text/x-python", b"print('a')")
    code_b = await _artifact(app_state, pid, "code-b.py", "text/x-python", b"print('b')")
    not_code = await _artifact(app_state, pid, "report.json", "application/json", b"{}")

    first = await client.post(
        base,
        json={"name": "Trim definitions", "code_artifact_id": code_a, "config": {"purpose": "x"}},
    )
    assert first.status_code == 201, first.text
    step_a = first.json()
    assert step_a["position"] == 0 and step_a["enabled"] is True and step_a["kind"] == "custom_code"
    assert step_a["config"]["stage"] == "entries" and step_a["config"]["purpose"] == "x"
    assert step_a["config"]["code_sha256"] and step_a["code_artifact_id"] == code_a
    second = await client.post(base, json={"name": "Number senses", "code_artifact_id": code_b})
    assert second.status_code == 201 and second.json()["position"] == 1
    step_b = second.json()

    refused = await client.post(base, json={"name": "Bad", "code_artifact_id": not_code})
    assert refused.status_code == 422 and "not Python" in refused.text
    missing = await client.post(base, json={"name": "Bad", "code_artifact_id": str(uuid.uuid4())})
    assert missing.status_code == 422

    listed = (await client.get(base)).json()
    assert [s["name"] for s in listed] == ["Trim definitions", "Number senses"]

    updated = await client.patch(
        f"{base}/{step_a['id']}", json={"enabled": False, "name": "Trim definitions (off)"}
    )
    assert updated.status_code == 200 and updated.json()["enabled"] is False
    assert updated.json()["name"] == "Trim definitions (off)"

    wrong = await client.post(f"{base}/reorder", json={"ids": [step_a["id"]]})
    assert wrong.status_code == 422
    reordered = await client.post(f"{base}/reorder", json={"ids": [step_b["id"], step_a["id"]]})
    assert reordered.status_code == 200
    assert [(s["name"], s["position"]) for s in reordered.json()] == [
        ("Number senses", 0),
        ("Trim definitions (off)", 1),
    ]

    assert (await client.delete(f"{base}/{step_b['id']}")).status_code == 204
    assert (await client.delete(f"{base}/{step_b['id']}")).status_code == 404
    remaining = (await client.get(base)).json()
    assert [s["id"] for s in remaining] == [step_a["id"]]

    # another project cannot see or touch them
    other = await _project(client)
    assert (await client.get(f"/api/projects/{other}/steps")).json() == []
    assert (await client.delete(f"/api/projects/{other}/steps/{step_a['id']}")).status_code == 404
