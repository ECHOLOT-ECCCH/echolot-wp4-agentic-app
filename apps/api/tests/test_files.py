from __future__ import annotations

import hashlib
import os

import httpx

from elexifier_api.state import AppState
from elexifier_core.storage import LocalStorage

from .conftest import LoggedIn


async def _project(client: httpx.AsyncClient) -> str:
    resp = await client.post("/api/projects", json={"name": "P", "source_type": "xml"})
    return resp.json()["id"]


async def test_raw_upload_download_and_ranges(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    logged_in.apply(client)
    pid = await _project(client)
    payload = os.urandom(300_000)
    digest = hashlib.sha256(payload).hexdigest()

    resp = await client.post(
        f"/api/projects/{pid}/files",
        params={"name": "dictionary (1).XML"},
        content=payload,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert resp.status_code == 201, resp.text
    meta = resp.json()
    assert meta["name"] == "dictionary (1).XML"
    assert meta["size"] == len(payload)
    assert meta["sha256"] == digest
    assert meta["mime"] == "application/xml"
    assert meta["kind"] == "source"

    # stored under the key scheme projects/{project}/{kind}/{file}/{name}
    storage = app_state.storage
    assert isinstance(storage, LocalStorage)
    stored = list(storage.root.rglob("dictionary (1).XML"))
    assert len(stored) == 1
    assert stored[0].relative_to(storage.root).parts[:3] == ("projects", pid, "source")

    listed = await client.get(f"/api/projects/{pid}/files")
    assert [f["id"] for f in listed.json()] == [meta["id"]]
    assert (await client.get(f"/api/projects/{pid}/files/{meta['id']}")).json()["sha256"] == digest

    full = await client.get(f"/api/projects/{pid}/files/{meta['id']}/download")
    assert full.status_code == 200
    assert full.content == payload
    assert full.headers["content-length"] == str(len(payload))
    assert "attachment" in full.headers["content-disposition"]
    assert full.headers["etag"] == f'"{digest}"'

    part = await client.get(
        f"/api/projects/{pid}/files/{meta['id']}/download", headers={"Range": "bytes=100-199"}
    )
    assert part.status_code == 206
    assert part.content == payload[100:200]
    assert part.headers["content-range"] == f"bytes 100-199/{len(payload)}"

    tail = await client.get(
        f"/api/projects/{pid}/files/{meta['id']}/download", headers={"Range": "bytes=-10"}
    )
    assert tail.status_code == 206 and tail.content == payload[-10:]

    bad = await client.get(
        f"/api/projects/{pid}/files/{meta['id']}/download", headers={"Range": "bytes=999999-"}
    )
    assert bad.status_code == 416

    deleted = await client.delete(f"/api/projects/{pid}/files/{meta['id']}")
    assert deleted.status_code == 204
    assert not list(storage.root.rglob("dictionary (1).XML"))
    assert (await client.get(f"/api/projects/{pid}/files/{meta['id']}")).status_code == 404


async def test_multipart_upload(client: httpx.AsyncClient, logged_in: LoggedIn) -> None:
    logged_in.apply(client)
    pid = await _project(client)
    resp = await client.post(
        f"/api/projects/{pid}/files",
        files={"file": ("../evil/../notes.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "notes.pdf"
    assert resp.json()["mime"] == "application/pdf"
    assert resp.json()["size"] == 13


async def test_upload_validation_and_limits(
    client: httpx.AsyncClient, logged_in: LoggedIn, settings, app_state: AppState
) -> None:
    logged_in.apply(client)
    pid = await _project(client)
    no_name = await client.post(f"/api/projects/{pid}/files", content=b"abc")
    assert no_name.status_code == 400
    empty = await client.post(f"/api/projects/{pid}/files", params={"name": "e.xml"}, content=b"")
    assert empty.status_code == 400

    app_state.settings.max_upload_mb = 1
    too_big = await client.post(
        f"/api/projects/{pid}/files", params={"name": "big.bin"}, content=b"x" * (1024 * 1024 + 1)
    )
    assert too_big.status_code == 413
    storage = app_state.storage
    assert isinstance(storage, LocalStorage)
    assert not list(storage.root.rglob("big.bin"))
    assert (await client.get(f"/api/projects/{pid}/files")).json() == []


async def test_content_endpoint_respects_preview_cap(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    logged_in.apply(client)
    pid = await _project(client)
    body = b"<dictionary><entry>a</entry></dictionary>"
    up = await client.post(f"/api/projects/{pid}/files", params={"name": "d.xml"}, content=body)
    fid = up.json()["id"]

    full = await client.get(f"/api/projects/{pid}/files/{fid}/content")
    assert full.status_code == 200 and full.content == body
    assert full.headers["content-type"].startswith("application/xml")
    assert full.headers["x-file-size"] == str(len(body))

    sliced = await client.get(
        f"/api/projects/{pid}/files/{fid}/content", params={"offset": 12, "length": 5}
    )
    assert sliced.content == body[12:17]

    app_state.settings.max_preview_bytes = 10
    refused = await client.get(f"/api/projects/{pid}/files/{fid}/content")
    assert refused.status_code == 413
    small_slice = await client.get(
        f"/api/projects/{pid}/files/{fid}/content", params={"offset": 0, "length": 8}
    )
    assert small_slice.status_code == 200 and small_slice.content == body[:8]


async def test_viewer_cannot_upload_or_delete(
    client: httpx.AsyncClient, logged_in: LoggedIn, app_state: AppState
) -> None:
    from .conftest import make_logged_in

    logged_in.apply(client)
    pid = await _project(client)
    up = await client.post(f"/api/projects/{pid}/files", params={"name": "a.xml"}, content=b"<a/>")
    fid = up.json()["id"]
    viewer = await make_logged_in(app_state, email="viewer@example.com")
    await client.post(
        f"/api/projects/{pid}/members", json={"email": "viewer@example.com", "role": "viewer"}
    )

    viewer.apply(client)
    assert (await client.get(f"/api/projects/{pid}/files/{fid}/download")).status_code == 200
    assert (
        await client.post(f"/api/projects/{pid}/files", params={"name": "b.xml"}, content=b"<b/>")
    ).status_code == 403
    assert (await client.delete(f"/api/projects/{pid}/files/{fid}")).status_code == 403
