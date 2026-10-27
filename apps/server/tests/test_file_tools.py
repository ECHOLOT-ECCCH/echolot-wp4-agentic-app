from __future__ import annotations

import httpx

from echolot_app.agent.agent import workspace_block

from .conftest import Workspace, call_tool, upload

PEOPLE = (
    b"uri,name,entity_type,context\n"
    b"q1,Brian O'Doherty,person,Inside the white cube\n"
    b"q2,Eduardo Chillida,person,Sculptor from Donostia\n"
    b"q3,Bilbao Fine Arts Museum,organization,\n"
    b"q4,Jorge Oteiza,person,Sculptor and painter\n"
)


async def test_list_and_workspace_block(client: httpx.AsyncClient, workspace: Workspace) -> None:
    fid = await upload(client, workspace.id, "people.csv", PEOPLE)
    await upload(client, workspace.id, "notes.txt", b"free text")
    result = await call_tool(workspace.deps, "list_files", {})
    files = {f["name"]: f for f in result["data"]["files"]}
    assert files["people.csv"]["record_count"] == 4
    assert files["people.csv"]["columns"] == ["uri", "name", "entity_type", "context"]
    assert files["notes.txt"]["format"] is None
    block = await workspace_block(workspace.deps)
    assert f"people.csv (4 records), file_id {fid}" in block
    assert "Task notes: not written yet" in block


async def test_profile(client: httpx.AsyncClient, workspace: Workspace) -> None:
    fid = await upload(client, workspace.id, "people.csv", PEOPLE)
    result = await call_tool(workspace.deps, "profile_file", {"file_id": fid})
    assert result["summary"] == "people.csv: 4 records, 4 columns"
    columns = {c["column"]: c for c in result["data"]["columns"]}
    assert columns["context"]["fill_rate"] == 0.75
    assert columns["entity_type"]["distinct"] == 2
    assert columns["entity_type"]["top_values"] == [{"value": "person", "count": 3}]
    assert "top_values" not in columns["uri"]  # all distinct: nothing repeats


async def test_samples_and_clipping(client: httpx.AsyncClient, workspace: Workspace) -> None:
    long_context = "word " * 2000
    data = PEOPLE + f'q5,Long,person,"{long_context}"\n'.encode()
    fid = await upload(client, workspace.id, "people.csv", data)
    first = await call_tool(
        workspace.deps, "sample_records", {"file_id": fid, "strategy": "first", "n": 2}
    )
    assert [r["row"] for r in first["data"]["records"]] == [0, 1]
    assert first["data"]["records"][1]["record"]["name"] == "Eduardo Chillida"
    last = await call_tool(
        workspace.deps, "sample_records", {"file_id": fid, "strategy": "first", "offset": 4}
    )
    record = last["data"]["records"][0]["record"]
    assert len(record["context"]) < 4000
    assert last["data"]["note"] == "long values were shortened"
    capped = await call_tool(workspace.deps, "sample_records", {"file_id": fid, "n": 500})
    assert len(capped["data"]["records"]) == 5


async def test_search_and_distinct(client: httpx.AsyncClient, workspace: Workspace) -> None:
    fid = await upload(client, workspace.id, "people.csv", PEOPLE)
    found = await call_tool(workspace.deps, "search_records", {"file_id": fid, "query": "SCULPTOR"})
    assert found["data"]["matches"] == 2
    assert [r["row"] for r in found["data"]["records"]] == [1, 3]
    in_name = await call_tool(
        workspace.deps, "search_records", {"file_id": fid, "query": "sculptor", "column": "name"}
    )
    assert in_name["data"]["matches"] == 0
    bad_column = await call_tool(
        workspace.deps, "search_records", {"file_id": fid, "query": "x", "column": "nope"}
    )
    assert bad_column["summary"].startswith("Error: no column")
    distinct = await call_tool(
        workspace.deps, "distinct_values", {"file_id": fid, "column": "entity_type"}
    )
    assert distinct["data"]["values"] == [
        {"value": "person", "count": 3},
        {"value": "organization", "count": 1},
    ]


async def test_bad_file_ids_are_errors_the_model_can_read(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    txt = await upload(client, workspace.id, "notes.txt", b"free text")
    not_records = await call_tool(workspace.deps, "profile_file", {"file_id": txt})
    assert "has no records" in not_records["summary"]
    junk = await call_tool(workspace.deps, "profile_file", {"file_id": "people.csv"})
    assert "not a file id" in junk["summary"]
