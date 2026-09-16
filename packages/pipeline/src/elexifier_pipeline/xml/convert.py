"""Full conversion of a document: stream, convert, write DMLex XML and JSON, validate.

Memory stays flat regardless of the dictionary size: the converted entries go to a temporary
JSONL file in the first pass (one line per output entry, with its source ordinal), and the XML
and JSON writers read that file back. Validation of the XML needs the whole document in memory
(identity constraints), which xmlschema handles for files of a few hundred MB.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from elexifier_pipeline.dmlex.validator import (
    ConsistencyIssue,
    EntryIssue,
    ValidationReport,
    check_consistency,
    validate_xml,
)
from elexifier_pipeline.dmlex.writers import WriterReport, normalize, write_json, write_xml
from elexifier_pipeline.xml import streaming
from elexifier_pipeline.xml.dmlexifier import JsonDict, TEltDesc, new_transformer

ProgressCallback = Callable[[str, int, int | None], None]  # phase, done, total

PROGRESS_EVERY = 500
MAX_ISSUE_ENTRIES = 5000
_CONSISTENCY_ERRORS = ("duplicate-entry-id", "duplicate-sense-id", "empty-headword")


@dataclass
class FullRunResult:
    xml_path: Path
    json_path: Path
    source_entries: int
    output_entries: int
    resource_scalars: JsonDict
    validation: ValidationReport
    issues: dict[int, list[EntryIssue]]  # output index -> issues (capped to MAX_ISSUE_ENTRIES)
    ordinals: dict[int, int]  # output index -> source ordinal, for the entries in ``issues``
    resource_issues: list[EntryIssue] = field(default_factory=list)
    homographs_numbered: int = 0
    plan_warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    issue_codes: dict[str, int] = field(default_factory=dict)
    error_count: int = 0  # all errors, including those beyond the per-entry cap
    warning_count: int = 0


def _pos_key(entry: JsonDict) -> tuple[str, ...]:
    tags = []
    for pos in entry.get("partsOfSpeech") or []:
        if isinstance(pos, dict):
            tags.append(str(pos.get("tag") or ""))
    return tuple(sorted(tags))


def _read_jsonl(path: Path) -> Iterator[tuple[int, JsonDict]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            record = json.loads(line)
            yield record["o"], record["e"]


def convert_document(
    source: Path,
    mapping: TEltDesc,
    out_dir: Path,
    *,
    progress: ProgressCallback | None = None,
    total_hint: int | None = None,
    number_homographs: bool = True,
    max_errors: int = 10_000,
) -> FullRunResult:
    """Convert ``source`` with ``mapping`` into ``out_dir/dmlex.xml`` and ``out_dir/dmlex.json``."""
    timings: dict[str, float] = {}
    plan = streaming.plan_for(mapping)
    transformer = new_transformer()
    jsonl_path = out_dir / "entries.jsonl"
    xml_path = out_dir / "dmlex.xml"
    json_path = out_dir / "dmlex.json"

    def report(phase: str, done: int, total: int | None) -> None:
        if progress is not None:
            progress(phase, done, total)

    # pass 1: convert, spool to JSONL, count headwords for homograph numbering
    started = time.perf_counter()
    source_entries = output_entries = 0
    groups: Counter[tuple[str, tuple[str, ...]]] = Counter()
    numbered_groups: set[tuple[str, tuple[str, ...]]] = set()
    with jsonl_path.open("w", encoding="utf-8") as spool:
        for source_ordinal, (chain, element) in enumerate(streaming.iter_entries(source, plan)):
            if not plan.resource_scalars and plan.resource_desc is not None:
                plan.resource_scalars = streaming.resource_scalars(chain, plan, transformer)
            wrapped = streaming.wrap_element(chain, element)
            for desc, raw in streaming.transform_wrapped(wrapped, plan, transformer):
                entry = normalize(raw, desc)
                if number_homographs:
                    key = (str(entry.get("headword") or ""), _pos_key(entry))
                    groups[key] += 1
                    if entry.get("homographNumber") not in (None, ""):
                        numbered_groups.add(key)
                spool.write(json.dumps({"o": source_ordinal, "e": entry}, ensure_ascii=False))
                spool.write("\n")
                output_entries += 1
            source_entries += 1
            if source_entries % PROGRESS_EVERY == 0:
                report("converting", source_entries, total_hint)
    report("converting", source_entries, total_hint)
    timings["convert_s"] = round(time.perf_counter() - started, 3)

    # pass 2: write XML and JSON (homograph numbers assigned on the way)
    started = time.perf_counter()
    duplicates = {key for key, count in groups.items() if count > 1 and key not in numbered_groups}
    assigned: Counter[tuple[str, tuple[str, ...]]] = Counter()
    homographs_numbered = 0

    def entries() -> Iterator[JsonDict]:
        nonlocal homographs_numbered
        assigned.clear()
        counted = 0
        for _ordinal, entry in _read_jsonl(jsonl_path):
            if duplicates:
                key = (str(entry.get("headword") or ""), _pos_key(entry))
                if key in duplicates:
                    assigned[key] += 1
                    entry = {**entry, "homographNumber": assigned[key]}
                    counted += 1
            yield entry
        homographs_numbered = counted

    writer_report = WriterReport()
    with xml_path.open("wb") as out:
        write_xml(out, plan.resource_scalars, entries(), writer_report)
    with json_path.open("w", encoding="utf-8") as out_json:
        write_json(out_json, plan.resource_scalars, entries())
    timings["write_s"] = round(time.perf_counter() - started, 3)
    report("writing", output_entries, output_entries)

    # pass 3: validate the XML and run the consistency checks
    started = time.perf_counter()
    report("validating", 0, None)  # no per-entry progress from the validator
    validation = validate_xml(xml_path, max_errors=max_errors)
    consistency: list[ConsistencyIssue] = check_consistency(e for _o, e in _read_jsonl(jsonl_path))
    timings["validate_s"] = round(time.perf_counter() - started, 3)

    issues: dict[int, list[EntryIssue]] = {}
    resource_issues: list[EntryIssue] = []
    issue_codes: Counter[str] = Counter()
    totals: Counter[str] = Counter()

    def add(index: int | None, issue: EntryIssue) -> None:
        issue_codes[issue.code if issue.code != "schema" else _schema_code(issue.message)] += 1
        totals[issue.severity] += 1
        if index is None:
            resource_issues.append(issue)
        elif index in issues or len(issues) < MAX_ISSUE_ENTRIES:
            issues.setdefault(index, []).append(issue)

    for error in validation.errors:
        add(
            error.entry_index,
            EntryIssue("error", "schema", "schema", _relative(error.path), error.reason),
        )
    for finding in consistency:
        severity = "error" if finding.code in _CONSISTENCY_ERRORS else "warning"
        add(
            finding.entry_index,
            EntryIssue(severity, "consistency", finding.code, "entry", finding.message),
        )
    for warning in writer_report.warnings:
        add(None, EntryIssue("warning", "writer", "dropped-value", "", warning))
    for kind, count in writer_report.dropped_duplicates.items():
        add(
            None,
            EntryIssue(
                "warning",
                "writer",
                "duplicate-sibling",
                "",
                f"{count} identical '{kind}' sibling(s) dropped",
            ),
        )

    ordinals: dict[int, int] = {}
    if issues:
        wanted = set(issues)
        for index, (ordinal, _entry) in enumerate(_read_jsonl(jsonl_path)):
            if index in wanted:
                ordinals[index] = ordinal
                if len(ordinals) == len(wanted):
                    break
    jsonl_path.unlink(missing_ok=True)
    return FullRunResult(
        xml_path=xml_path,
        json_path=json_path,
        source_entries=source_entries,
        output_entries=output_entries,
        resource_scalars=plan.resource_scalars,
        validation=validation,
        issues=issues,
        ordinals=ordinals,
        resource_issues=resource_issues,
        homographs_numbered=homographs_numbered,
        plan_warnings=plan.warnings,
        timings=timings,
        issue_codes=dict(issue_codes.most_common(30)),
        # schema errors past max_errors are counted by the validator but not listed
        error_count=totals["error"] + max(0, validation.error_count - len(validation.errors)),
        warning_count=totals["warning"],
    )


def _relative(path: str) -> str:
    return re.sub(r"^/lexicographicResource/entry(\[\d+\])?/?", "", path) or "entry"


def _schema_code(reason: str) -> str:
    """A short bucket for schema errors so the report can count them by kind."""
    lowered = reason.lower()
    if "duplicated value" in lowered:
        return "schema:duplicate"
    if "assertion" in lowered:
        return "schema:assertion"
    if "unexpected child" in lowered or "not allowed" in lowered:
        return "schema:structure"
    if "missing" in lowered or "required" in lowered:
        return "schema:missing"
    return "schema:other"


__all__ = ["FullRunResult", "ProgressCallback", "convert_document"]
