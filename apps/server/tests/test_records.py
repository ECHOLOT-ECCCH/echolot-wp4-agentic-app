from __future__ import annotations

import json
from pathlib import Path

import pytest

from echolot_app.records import (
    INDEX_EVERY,
    RecordsError,
    RecordsReader,
    detect_format,
    normalize,
    records_path,
)


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _records(source: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in records_path(source).read_text("utf-8").splitlines()]


def test_detect_format() -> None:
    assert detect_format("A.CSV") == "csv"
    assert detect_format("x.tsv") == "tsv"
    assert detect_format("x.ndjson") == "jsonl"
    assert detect_format("x.json") == "json"
    assert detect_format("notes.txt") is None


def test_csv_with_bom_quotes_and_newlines(tmp_path: Path) -> None:
    source = _write(
        tmp_path,
        "a.csv",
        '﻿id,name,context\n1,"O\'Doherty, Brian","line one\nline two"\n2,Ana,\n'.encode(),
    )
    meta = normalize(source, "csv")
    assert meta.encoding == "utf-8-sig"
    assert meta.columns == ["id", "name", "context"]
    assert meta.record_count == 2
    assert _records(source)[0] == {
        "id": "1",
        "name": "O'Doherty, Brian",
        "context": "line one\nline two",
    }


def test_semicolon_csv_in_latin_1(tmp_path: Path) -> None:
    source = _write(tmp_path, "b.csv", "izena;herria\nJosé;Donostia\nAne;Bilbo\n".encode("latin-1"))
    meta = normalize(source, "csv")
    assert meta.encoding == "latin-1"
    assert meta.dialect == {"delimiter": ";", "quotechar": '"'}
    assert _records(source)[0] == {"izena": "José", "herria": "Donostia"}


def test_ragged_rows_are_kept_and_reported(tmp_path: Path) -> None:
    source = _write(tmp_path, "c.csv", b"a,b\n1\n2,3,4\n\n5,6\n")
    meta = normalize(source, "csv")
    assert meta.record_count == 3  # the blank line is skipped
    assert meta.ragged_rows == 2
    assert meta.ragged_lines == [2, 3]
    assert meta.columns == ["a", "b", "column_3"]
    assert _records(source) == [
        {"a": "1", "b": ""},
        {"a": "2", "b": "3", "column_3": "4"},
        {"a": "5", "b": "6"},
    ]


def test_duplicate_and_empty_headers(tmp_path: Path) -> None:
    source = _write(tmp_path, "d.tsv", b"name\tname\t\nx\ty\tz\n")
    meta = normalize(source, "tsv")
    assert meta.columns == ["name", "name_2", "column_3"]


def test_jsonl_reports_lines_that_are_not_objects(tmp_path: Path) -> None:
    source = _write(tmp_path, "e.jsonl", b'{"a": 1}\n[1, 2]\nnot json\n\n{"b": {"c": 2}}\n')
    meta = normalize(source, "jsonl")
    assert meta.record_count == 2
    assert meta.invalid_lines == [2, 3]
    assert meta.columns == ["a", "b"]


def test_json_object_wrapping_one_list(tmp_path: Path) -> None:
    source = _write(
        tmp_path, "f.json", json.dumps({"entities": [{"uri": "u1"}, {"uri": "u2"}]}).encode()
    )
    meta = normalize(source, "json")
    assert meta.json_path == "entities"
    assert meta.record_count == 2


def test_json_that_is_not_a_list_of_records(tmp_path: Path) -> None:
    source = _write(tmp_path, "g.json", b'{"a": [1], "b": [2]}')
    with pytest.raises(RecordsError):
        normalize(source, "json")


def test_reader_pages_samples_and_picks_rows(tmp_path: Path) -> None:
    total = INDEX_EVERY * 2 + 500
    lines = "n\n" + "".join(f"{i}\n" for i in range(total))
    source = _write(tmp_path, "h.csv", lines.encode())
    meta = normalize(source, "csv")
    reader = RecordsReader(source, meta.record_count)
    page = reader.page(INDEX_EVERY + 7, 3)
    assert [row for row, _ in page] == [1007, 1008, 1009]
    assert [r["n"] for _, r in page] == ["1007", "1008", "1009"]
    assert reader.page(total - 1, 10) == [(total - 1, {"n": str(total - 1)})]
    assert reader.page(total, 10) == []
    assert [row for row, _ in reader.rows([2400, 3, 2400, 99999])] == [2400, 3, 2400]
    spread = reader.sample(5, "spread")
    assert [row for row, _ in spread] == [0, 625, 1250, 1874, 2499]
    assert len(reader.sample(10, "random", seed=1)) == 10
