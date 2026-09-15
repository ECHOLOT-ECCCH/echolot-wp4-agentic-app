from __future__ import annotations

import httpx
from fastapi import FastAPI

from elexifier_api.state import AppState

from .conftest import LoggedIn, make_logged_in


async def _create(
    client: httpx.AsyncClient, name: str = "Slovar", source_type: str = "xml"
) -> dict:
    resp = await client.post(
        "/api/projects", json={"name": name, "source_type": source_type, "description": "d"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_list_get_update_delete(
    client: httpx.AsyncClient, logged_in: LoggedIn
) -> None:
    logged_in.apply(client)
    created = await _create(client)
    assert created["my_role"] == "owner"
    assert created["source_type"] == "xml"
    assert created["owner_id"] == str(logged_in.user.id)

    listed = await client.get("/api/projects")
    assert [p["id"] for p in listed.json()] == [created["id"]]

    got = await client.get(f"/api/projects/{created['id']}")
    assert got.status_code == 200 and got.json()["name"] == "Slovar"

    patched = await client.patch(
        f"/api/projects/{created['id']}",
        json={"name": "  Slovar 2  ", "default_model": {"provider": "test", "model_id": "test"}},
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Slovar 2"
    assert patched.json()["default_model"] == {"provider": "test", "model_id": "test"}

    deleted = await client.delete(f"/api/projects/{created['id']}")
    assert deleted.status_code == 204
    assert (await client.get(f"/api/projects/{created['id']}")).status_code == 404


async def test_validation(client: httpx.AsyncClient, logged_in: LoggedIn) -> None:
    logged_in.apply(client)
    assert (
        await client.post("/api/projects", json={"name": "", "source_type": "xml"})
    ).status_code == 422
    assert (
        await client.post("/api/projects", json={"name": "x", "source_type": "docx"})
    ).status_code == 422


async def test_projects_are_private_and_roles_enforced(
    app: FastAPI, client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    logged_in.apply(client)
    project = await _create(client)
    other = await make_logged_in(app_state, email="other@example.com")

    other.apply(client)
    assert (await client.get("/api/projects")).json() == []
    assert (await client.get(f"/api/projects/{project['id']}")).status_code == 404
    assert (
        await client.patch(f"/api/projects/{project['id']}", json={"name": "x"})
    ).status_code == 404

    # owner adds the other user as viewer
    logged_in.apply(client)
    added = await client.post(
        f"/api/projects/{project['id']}/members",
        json={"email": "other@example.com", "role": "viewer"},
    )
    assert added.status_code == 201 and added.json()["role"] == "viewer"
    members = (await client.get(f"/api/projects/{project['id']}/members")).json()
    assert {m["role"] for m in members} == {"owner", "viewer"}

    other.apply(client)
    assert (await client.get(f"/api/projects/{project['id']}")).json()["my_role"] == "viewer"
    assert (
        await client.patch(f"/api/projects/{project['id']}", json={"name": "x"})
    ).status_code == 403
    assert (await client.delete(f"/api/projects/{project['id']}")).status_code == 403
    assert (
        await client.post(f"/api/projects/{project['id']}/members", json={"email": "x@example.com"})
    ).status_code == 403

    # promote to editor: may update, may not delete or manage members
    logged_in.apply(client)
    await client.post(
        f"/api/projects/{project['id']}/members",
        json={"email": "other@example.com", "role": "editor"},
    )
    other.apply(client)
    assert (
        await client.patch(f"/api/projects/{project['id']}", json={"name": "y"})
    ).status_code == 200
    assert (await client.delete(f"/api/projects/{project['id']}")).status_code == 403

    # owner cannot be removed; unknown email is 404; removal works
    logged_in.apply(client)
    assert (
        await client.delete(f"/api/projects/{project['id']}/members/{logged_in.user.id}")
    ).status_code == 409
    assert (
        await client.post(
            f"/api/projects/{project['id']}/members", json={"email": "nobody@example.com"}
        )
    ).status_code == 404
    assert (
        await client.delete(f"/api/projects/{project['id']}/members/{other.user.id}")
    ).status_code == 204
    other.apply(client)
    assert (await client.get(f"/api/projects/{project['id']}")).status_code == 404


async def test_instance_admin_can_see_any_project(
    client: httpx.AsyncClient, logged_in: LoggedIn, admin: LoggedIn
) -> None:
    logged_in.apply(client)
    project = await _create(client)
    admin.apply(client)
    resp = await client.get(f"/api/projects/{project['id']}")
    assert resp.status_code == 200 and resp.json()["my_role"] == "owner"
