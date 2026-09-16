from __future__ import annotations

import copy
import json

import httpx

from elexifier_pipeline.sample import OXFORD_MAPPING

from .conftest import LoggedIn


async def _project(client: httpx.AsyncClient) -> str:
    return (await client.post("/api/projects", json={"name": "Maps", "source_type": "xml"})).json()[
        "id"
    ]


async def test_mapping_versions_check_and_errors(
    client: httpx.AsyncClient, logged_in: LoggedIn
) -> None:
    logged_in.apply(client)
    pid = await _project(client)
    assert (await client.get(f"/api/projects/{pid}/mappings")).json() == []
    assert (await client.get(f"/api/projects/{pid}/mappings/latest")).status_code == 404

    spec = json.loads(OXFORD_MAPPING.read_text(encoding="utf-8"))
    checked = (await client.post(f"/api/projects/{pid}/mappings/check", json={"spec": spec})).json()
    assert checked["ok"] is True and checked["summary"].startswith("lexicographicResource <- ")

    created = await client.post(
        f"/api/projects/{pid}/mappings", json={"spec": spec, "note": "typed in the editor"}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["version"] == 1 and body["source"] == "user" and body["note"].startswith("typed")
    assert body["spec"] == spec and "entry <- se" in body["summary"]

    broken = copy.deepcopy(spec)
    entry = next(c for c in broken["children"] if c["outElement"] == "entry")
    entry["textVals"] = [t for t in entry["textVals"] if t["outElement"] != "headword"]
    check = (await client.post(f"/api/projects/{pid}/mappings/check", json={"spec": broken})).json()
    assert check["ok"] is False
    assert any("headword" in i["message"] for i in check["issues"])
    refused = await client.post(f"/api/projects/{pid}/mappings", json={"spec": broken})
    assert refused.status_code == 422
    assert any("headword" in i["message"] for i in refused.json()["detail"]["issues"])

    malformed = await client.post(
        f"/api/projects/{pid}/mappings/check", json={"spec": {"inSelector": "/x", "bogus": 1}}
    )
    assert malformed.json()["ok"] is False and malformed.json()["issues"][0]["path"] == "<root>"

    second = await client.post(f"/api/projects/{pid}/mappings", json={"spec": spec, "note": "v2"})
    assert second.json()["version"] == 2
    versions = (await client.get(f"/api/projects/{pid}/mappings")).json()
    assert [v["version"] for v in versions] == [2, 1]
    latest = (await client.get(f"/api/projects/{pid}/mappings/latest")).json()
    assert latest["version"] == 2
    first = (await client.get(f"/api/projects/{pid}/mappings/1")).json()
    assert first["note"].startswith("typed")
    assert (await client.get(f"/api/projects/{pid}/mappings/9")).status_code == 404


async def test_spec_round_trip(client: httpx.AsyncClient, logged_in: LoggedIn) -> None:
    logged_in.apply(client)
    pid = await _project(client)
    assert (await client.get(f"/api/projects/{pid}/spec")).status_code == 404
    saved = await client.put(f"/api/projects/{pid}/spec", json={"markdown": "# Spec\n\n- one"})
    assert saved.status_code == 200 and saved.json()["version"] == 1
    again = await client.put(f"/api/projects/{pid}/spec", json={"markdown": "# Spec v2"})
    assert again.json()["version"] == 2
    current = (await client.get(f"/api/projects/{pid}/spec")).json()
    assert current["markdown"] == "# Spec v2" and current["version"] == 2
