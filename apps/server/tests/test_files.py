from __future__ import annotations

import hashlib

import httpx

from echolot_app.state import AppState


async def _workspace(client: httpx.AsyncClient) -> str:
    response = await client.post("/api/workspaces", json={"name": "Basque heritage"})
    assert response.status_code == 201
    return response.json()["id"]


async def _upload(client: httpx.AsyncClient, wid: str, name: str, data: bytes) -> httpx.Response:
    return await client.post(f"/api/workspaces/{wid}/files", params={"name": name}, content=data)


async def test_workspace_crud_and_task(client: httpx.AsyncClient) -> None:
    wid = await _workspace(client)
    assert [w["id"] for w in (await client.get("/api/workspaces")).json()] == [wid]
    patched = await client.patch(f"/api/workspaces/{wid}", json={"description": "pilot"})
    assert patched.json()["description"] == "pilot"
    assert (await client.get(f"/api/workspaces/{wid}/task")).json()["markdown"] == ""
    put = await client.put(f"/api/workspaces/{wid}/task", json={"markdown": "# Task\nLink people"})
    assert put.status_code == 200
    assert (await client.get(f"/api/workspaces/{wid}/task")).json()["markdown"].startswith("# Task")
    assert (await client.delete(f"/api/workspaces/{wid}")).status_code == 204
    assert (await client.get(f"/api/workspaces/{wid}")).status_code == 404


async def test_csv_upload_is_normalized_and_paged(client: httpx.AsyncClient) -> None:
    wid = await _workspace(client)
    data = b"uri,name\n" + b"".join(f"u{i},Name {i}\n".encode() for i in range(120))
    response = await _upload(client, wid, "people.csv", data)
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "input"
    assert body["mime"] == "text/csv"
    assert body["sha256"] == hashlib.sha256(data).hexdigest()
    assert body["meta"]["record_count"] == 120
    assert body["meta"]["columns"] == ["uri", "name"]

    page = (
        await client.get(
            f"/api/workspaces/{wid}/files/{body['id']}/records", params={"offset": 100, "limit": 5}
        )
    ).json()
    assert page["total"] == 120
    assert page["records"][0] == {"row": 100, "record": {"uri": "u100", "name": "Name 100"}}
    assert len(page["records"]) == 5

    content = await client.get(f"/api/workspaces/{wid}/files/{body['id']}/content")
    assert content.content == data
    ranged = await client.get(
        f"/api/workspaces/{wid}/files/{body['id']}/download", headers={"Range": "bytes=0-2"}
    )
    assert ranged.status_code == 206
    assert ranged.content == b"uri"


async def test_other_files_are_stored_without_records(client: httpx.AsyncClient) -> None:
    wid = await _workspace(client)
    body = (await _upload(client, wid, "notes.txt", b"hello")).json()
    assert body["meta"] == {}
    records = await client.get(f"/api/workspaces/{wid}/files/{body['id']}/records")
    assert records.status_code == 409


async def test_broken_json_keeps_the_upload_with_an_error(client: httpx.AsyncClient) -> None:
    wid = await _workspace(client)
    body = (await _upload(client, wid, "bad.json", b"{not json")).json()
    assert body["meta"]["format"] is None
    assert "not valid JSON" in body["meta"]["error"]


async def test_upload_needs_a_name_and_content(client: httpx.AsyncClient) -> None:
    wid = await _workspace(client)
    assert (await client.post(f"/api/workspaces/{wid}/files", content=b"x")).status_code == 400
    assert (await _upload(client, wid, "empty.csv", b"")).status_code == 400


async def test_delete_removes_the_file_and_its_records(
    client: httpx.AsyncClient, state: AppState
) -> None:
    wid = await _workspace(client)
    body = (await _upload(client, wid, "a.csv", b"a\n1\n")).json()
    folder = state.storage.root / "workspaces" / wid / "files" / body["id"]
    assert (folder / ".records.jsonl").is_file()
    assert (await client.delete(f"/api/workspaces/{wid}/files/{body['id']}")).status_code == 204
    assert not folder.exists()
    assert (await client.get(f"/api/workspaces/{wid}/files")).json() == []
