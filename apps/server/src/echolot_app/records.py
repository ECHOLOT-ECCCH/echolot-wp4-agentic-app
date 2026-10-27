"""Input files as records (docs/PLAN.md 7.3, M0.3).

An uploaded CSV, TSV, JSONL or JSON file is normalized once into ``.records.jsonl`` next to it:
one JSON object per line, the record, with its row number being the line number (0-based).
A sparse index (``.records.idx.json``, the byte offset of every ``INDEX_EVERY``-th line) makes
paging cheap on large files. Everything here is synchronous; callers run it in a thread.
"""

from __future__ import annotations

import csv
import json
import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RECORDS_NAME = ".records.jsonl"
INDEX_NAME = ".records.idx.json"
INDEX_EVERY = 1000
SNIFF_BYTES = 64 * 1024
MAX_JSON_BYTES = 200 * 1024 * 1024  # a JSON document is parsed whole; JSONL streams
MAX_REPORTED_LINES = 10
TABULAR_FORMATS = ("csv", "tsv")
FORMATS = ("csv", "tsv", "jsonl", "json")
_CSV_DELIMITERS = ",;\t|"

csv.field_size_limit(16 * 1024 * 1024)


class RecordsError(Exception):
    """The file cannot be read as records."""


@dataclass
class RecordsMeta:
    format: str
    record_count: int = 0
    columns: list[str] = field(default_factory=list)
    encoding: str | None = None
    dialect: dict[str, str] | None = None
    json_path: str | None = None  # the key holding the list when a JSON object wraps it
    ragged_rows: int = 0  # CSV rows with more or fewer cells than the header
    ragged_lines: list[int] = field(default_factory=list)
    invalid_lines: list[int] = field(default_factory=list)  # JSONL lines that are not objects
    invalid_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        data = {key: value for key, value in self.__dict__.items() if value not in (None, [], 0)}
        data["format"] = self.format
        data["record_count"] = self.record_count
        data["columns"] = self.columns
        return data


def detect_format(name: str) -> str | None:
    lowered = name.lower()
    for ext, fmt in ((".csv", "csv"), (".tsv", "tsv"), (".tab", "tsv"), (".jsonl", "jsonl")):
        if lowered.endswith(ext):
            return fmt
    if lowered.endswith(".ndjson"):
        return "jsonl"
    if lowered.endswith(".json"):
        return "json"
    return None


def records_path(source: Path) -> Path:
    return source.with_name(RECORDS_NAME)


def index_path(source: Path) -> Path:
    return source.with_name(INDEX_NAME)


# --- normalization -------------------------------------------------------------------------------


def normalize(source: Path, fmt: str) -> RecordsMeta:
    """Write the records of ``source`` next to it and return what was found."""
    if fmt in TABULAR_FORMATS:
        meta, rows = _read_tabular(source, fmt)
    elif fmt == "jsonl":
        meta = RecordsMeta(format="jsonl")
        rows = _read_jsonl(source, meta)
    elif fmt == "json":
        meta = RecordsMeta(format="json")
        rows = _read_json(source, meta)
    else:
        raise RecordsError(f"unsupported format {fmt!r}")
    _write(source, rows, meta)
    return meta


def _write(source: Path, rows: Iterator[dict[str, Any]], meta: RecordsMeta) -> None:
    target = records_path(source)
    offsets: list[int] = []
    seen: dict[str, None] = dict.fromkeys(meta.columns)
    count = 0
    with target.open("wb") as fh:
        for record in rows:
            if count % INDEX_EVERY == 0:
                offsets.append(fh.tell())
            fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            fh.write(b"\n")
            for key in record:
                if key not in seen:
                    seen[key] = None
            count += 1
    meta.record_count = count
    meta.columns = list(seen)
    index_path(source).write_text(json.dumps(offsets), encoding="utf-8")


def _sniff_encoding(head: bytes) -> str:
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        if exc.start < len(head) - 4:  # not merely a character cut at the end of the sample
            return "latin-1"
    return "utf-8"


def _unique_columns(header: list[str]) -> list[str]:
    columns: list[str] = []
    for index, raw in enumerate(header):
        name = raw.strip() or f"column_{index + 1}"
        candidate, n = name, 2
        while candidate in columns:
            candidate, n = f"{name}_{n}", n + 1
        columns.append(candidate)
    return columns


def _read_tabular(source: Path, fmt: str) -> tuple[RecordsMeta, Iterator[dict[str, Any]]]:
    with source.open("rb") as fh:
        head = fh.read(SNIFF_BYTES)
    encoding = _sniff_encoding(head)
    sample = head.decode(encoding, errors="ignore")
    if fmt == "tsv":
        dialect: type[csv.Dialect] | csv.Dialect = csv.excel_tab
    else:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=_CSV_DELIMITERS)
        except csv.Error:
            dialect = csv.excel
    meta = RecordsMeta(
        format=fmt,
        encoding=encoding,
        dialect={"delimiter": dialect.delimiter, "quotechar": dialect.quotechar or '"'},
    )

    def rows() -> Iterator[dict[str, Any]]:
        with source.open("r", encoding=encoding, newline="", errors="replace") as text:
            reader = csv.reader(text, dialect)
            header = next(reader, None)
            if header is None:
                return
            columns = _unique_columns(header)
            width = len(columns)
            meta.columns = list(columns)
            for cells in reader:
                if not cells or all(not c.strip() for c in cells):
                    continue
                if len(cells) != width:
                    meta.ragged_rows += 1
                    if len(meta.ragged_lines) < MAX_REPORTED_LINES:
                        meta.ragged_lines.append(reader.line_num)
                    while len(cells) > len(columns):
                        columns.append(f"column_{len(columns) + 1}")
                # short rows fill the header's columns; only long rows carry the extra ones
                names = columns[: max(width, len(cells))]
                yield {name: (cells[i] if i < len(cells) else "") for i, name in enumerate(names)}

    return meta, rows()


def _read_jsonl(source: Path, meta: RecordsMeta) -> Iterator[dict[str, Any]]:
    with source.open("r", encoding="utf-8-sig", errors="replace") as fh:
        for number, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                value = None
            if not isinstance(value, dict):
                meta.invalid_count += 1
                if len(meta.invalid_lines) < MAX_REPORTED_LINES:
                    meta.invalid_lines.append(number)
                continue
            yield value


def _read_json(source: Path, meta: RecordsMeta) -> Iterator[dict[str, Any]]:
    if source.stat().st_size > MAX_JSON_BYTES:
        raise RecordsError("JSON document too large to parse whole; upload it as JSONL")
    try:
        document = json.loads(source.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RecordsError(f"not valid JSON: {exc}") from exc
    items: Any = document
    if isinstance(document, dict):
        lists = [key for key, value in document.items() if isinstance(value, list)]
        if len(lists) != 1:
            raise RecordsError("a JSON object must hold exactly one list of records")
        meta.json_path = lists[0]
        items = document[lists[0]]
    if not isinstance(items, list):
        raise RecordsError("JSON must be a list of objects")
    for value in items:
        if isinstance(value, dict):
            yield value
        else:
            meta.invalid_count += 1


# --- reading -------------------------------------------------------------------------------------


class RecordsReader:
    """Reads the normalized records of one file."""

    def __init__(self, source: Path, record_count: int) -> None:
        self.path = records_path(source)
        self._index_path = index_path(source)
        self.record_count = record_count
        self._index: list[int] | None = None

    def _offsets(self) -> list[int]:
        if self._index is None:
            try:
                self._index = json.loads(self._index_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                self._index = []
        return self._index or [0]

    def iter_from(self, start: int = 0) -> Iterator[tuple[int, dict[str, Any]]]:
        """(row, record) from row ``start`` to the end."""
        if start >= self.record_count:
            return
        offsets = self._offsets()
        block = min(start // INDEX_EVERY, len(offsets) - 1)
        row = block * INDEX_EVERY
        with self.path.open("rb") as fh:
            fh.seek(offsets[block])
            for line in fh:
                if row >= start:
                    yield row, json.loads(line)
                row += 1

    def page(self, offset: int, limit: int) -> list[tuple[int, dict[str, Any]]]:
        out: list[tuple[int, dict[str, Any]]] = []
        for item in self.iter_from(offset):
            if len(out) >= limit:
                break
            out.append(item)
        return out

    def rows(self, wanted: list[int]) -> list[tuple[int, dict[str, Any]]]:
        """Specific rows, in the order of ``wanted``."""
        targets = sorted(set(r for r in wanted if 0 <= r < self.record_count))
        found: dict[int, dict[str, Any]] = {}
        for row in targets:
            if row in found:
                continue
            for got, record in self.iter_from(row):
                found[got] = record
                break
        return [(r, found[r]) for r in wanted if r in found]

    def sample(self, n: int, strategy: str, seed: int | None = None) -> list[tuple[int, Any]]:
        total = self.record_count
        if total == 0 or n <= 0:
            return []
        n = min(n, total)
        if strategy == "random":
            picks = sorted(random.Random(seed).sample(range(total), n))
        elif strategy == "spread":
            picks = sorted({round(i * (total - 1) / max(n - 1, 1)) for i in range(n)})
        else:
            return self.page(0, n)
        return self.rows(picks)


def runner_line(row: int, record: dict[str, Any]) -> bytes:
    """One line of a runner's input (docs/PLAN.md 7.3)."""
    line = json.dumps({"_row": row, "record": record}, ensure_ascii=False, separators=(",", ":"))
    return line.encode("utf-8") + b"\n"
