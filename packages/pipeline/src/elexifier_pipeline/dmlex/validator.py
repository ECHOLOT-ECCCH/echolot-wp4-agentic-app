"""Validate DMLex XML against the XSD (1.1) and check consistency of converted entries."""

from __future__ import annotations

import io
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import IO, Any

import xmlschema

from elexifier_pipeline.dmlex.schema import SCHEMA_PATH
from elexifier_pipeline.dmlex.writers import WriterReport, write_xml

_ENTRY_INDEX = re.compile(r"/entry\[(\d+)\]")


# xmlschema cannot evaluate some of the XSD's uniqueness constraints (a field such as
# partOfSpeech/@tag selects several values when an entry has two parts of speech). Those are
# reported as limitations, not as errors in the document.
LIMITATION_MARKERS = ("field selects multiple values",)


@dataclass
class ValidationError:
    path: str
    reason: str
    entry_index: int | None = None  # zero-based position of the entry in the document
    limitation: bool = False


@dataclass
class ValidationReport:
    valid: bool
    error_count: int
    errors: list[ValidationError] = field(default_factory=list)
    truncated: bool = False
    limitation_count: int = 0

    def summary(self) -> str:
        base = "valid DMLex XML" if self.valid else f"{self.error_count} validation error(s)"
        if self.truncated:
            base += f" (showing {len(self.errors)})"
        if self.limitation_count:
            base += f"; {self.limitation_count} uniqueness check(s) the validator cannot evaluate"
        return base


@lru_cache(maxsize=1)
def dmlex_schema() -> xmlschema.XMLSchema11:
    return xmlschema.XMLSchema11(str(SCHEMA_PATH))


def _clean_reason(error: Any) -> str:
    reason = getattr(error, "reason", None) or str(error).splitlines()[0]
    return re.sub(r"<xmlschema\.[^>]+>", "field", reason)


def validate_xml(source: str | Path | IO[bytes], *, max_errors: int = 200) -> ValidationReport:
    """Validate a document; the report lists errors with their path and the entry they belong to.

    Identity constraints (unique sense ids) need the whole document, so validation is not lazy.
    Files of a few hundred MB are still fine in memory for xmlschema; the caller decides when to
    validate a sample instead.
    """
    schema = dmlex_schema()
    resource = xmlschema.XMLResource(str(source) if isinstance(source, Path) else source)
    errors: list[ValidationError] = []
    count = 0
    limitations = 0
    for error in schema.iter_errors(resource):
        reason = _clean_reason(error)
        limitation = any(marker in reason for marker in LIMITATION_MARKERS)
        if limitation:
            limitations += 1
            continue
        count += 1
        if len(errors) < max_errors:
            path = str(getattr(error, "path", "") or "")
            match = _ENTRY_INDEX.search(path)
            errors.append(
                ValidationError(
                    path=path,
                    reason=reason,
                    entry_index=int(match.group(1)) - 1 if match else None,
                )
            )
    return ValidationReport(
        valid=count == 0,
        error_count=count,
        errors=errors,
        truncated=count > len(errors),
        limitation_count=limitations,
    )


@dataclass
class ConsistencyIssue:
    entry_index: int
    entry_id: str | None
    code: str
    message: str


def check_consistency(entries: Iterable[dict[str, Any]]) -> list[ConsistencyIssue]:
    """Cheap checks on normalized entries that the XSD does not express well."""
    issues: list[ConsistencyIssue] = []
    seen_entry_ids: dict[str, int] = {}
    seen_sense_ids: dict[str, int] = {}
    for index, entry in enumerate(entries):
        entry_id = entry.get("id")
        headword = str(entry.get("headword") or "").strip()
        if not headword:
            issues.append(
                ConsistencyIssue(index, entry_id, "empty-headword", "entry has no headword")
            )
        if entry_id:
            if entry_id in seen_entry_ids:
                issues.append(
                    ConsistencyIssue(
                        index,
                        entry_id,
                        "duplicate-entry-id",
                        f"entry id also used by entry {seen_entry_ids[entry_id]}",
                    )
                )
            seen_entry_ids.setdefault(str(entry_id), index)
        for pos in entry.get("partsOfSpeech", []) or []:
            if isinstance(pos, dict) and not str(pos.get("tag") or "").strip():
                issues.append(
                    ConsistencyIssue(index, entry_id, "empty-pos", "partOfSpeech without tag")
                )
        for sense in entry.get("senses", []) or []:
            if not isinstance(sense, dict):
                continue
            sense_id = sense.get("id")
            if sense_id:
                if sense_id in seen_sense_ids:
                    issues.append(
                        ConsistencyIssue(
                            index, entry_id, "duplicate-sense-id", f"sense id {sense_id!r} reused"
                        )
                    )
                seen_sense_ids.setdefault(str(sense_id), index)
            content_keys = (
                "indicator",
                "definitions",
                "examples",
                "headwordTranslations",
                "headwordExplanations",
                "labels",
            )
            if not any(sense.get(k) for k in content_keys):
                issues.append(
                    ConsistencyIssue(index, entry_id, "empty-sense", "sense has no content")
                )
            for definition in sense.get("definitions", []) or []:
                if isinstance(definition, dict) and not str(definition.get("text") or "").strip():
                    issues.append(
                        ConsistencyIssue(
                            index, entry_id, "empty-definition", "definition without text"
                        )
                    )
            for example in sense.get("examples", []) or []:
                if isinstance(example, dict) and not str(example.get("text") or "").strip():
                    issues.append(
                        ConsistencyIssue(index, entry_id, "empty-example", "example without text")
                    )
    return issues


# --- per-entry validation of converted entries (sample runs, run reports) -------------------


@dataclass
class EntryIssue:
    severity: str  # error | warning
    source: str  # schema | consistency | writer
    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "source": self.source,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }


@dataclass
class EntryValidation:
    report: ValidationReport
    per_entry: dict[int, list[EntryIssue]]  # index into the entries given
    resource_issues: list[EntryIssue]  # not attributable to one entry

    def issues_for(self, index: int) -> list[EntryIssue]:
        return self.per_entry.get(index, [])

    @property
    def entries_with_issues(self) -> int:
        return len(self.per_entry)


_CONSISTENCY_ERRORS = ("duplicate-entry-id", "duplicate-sense-id", "empty-headword")


def _relative_path(path: str) -> str:
    # "/lexicographicResource/entry[3]/sense[2]/x" -> "sense[2]/x"
    return re.sub(r"^/lexicographicResource/entry(\[\d+\])?/?", "", path) or "entry"


def validate_entries(
    scalars: dict[str, Any],
    entries: list[dict[str, Any]],
    *,
    max_errors: int = 1000,
) -> EntryValidation:
    """Write the entries as a temporary DMLex resource, validate it, and attribute every finding
    to its entry. Missing resource scalars (uri, langCode) get placeholders so that only entry
    problems surface; the placeholders are reported as resource issues."""
    resource_issues: list[EntryIssue] = []
    header = dict(scalars)
    for required, placeholder in (("uri", "urn:elexifier:sample"), ("langCode", "und")):
        if not header.get(required):
            header[required] = placeholder
            resource_issues.append(
                EntryIssue(
                    "warning",
                    "writer",
                    "missing-resource-value",
                    "lexicographicResource",
                    f"the mapping produces no resource '{required}'; placeholder used",
                )
            )
    writer_report = WriterReport()
    buffer = io.BytesIO()
    write_xml(buffer, header, entries, writer_report)
    for warning in writer_report.warnings:
        resource_issues.append(EntryIssue("warning", "writer", "dropped-value", "", warning))
    for kind, count in writer_report.dropped_duplicates.items():
        resource_issues.append(
            EntryIssue(
                "warning",
                "writer",
                "duplicate-sibling",
                "",
                f"{count} identical '{kind}' sibling(s) dropped (DMLex requires unique siblings)",
            )
        )
    buffer.seek(0)
    report = validate_xml(buffer, max_errors=max_errors)
    per_entry: dict[int, list[EntryIssue]] = {}
    for error in report.errors:
        issue = EntryIssue("error", "schema", "schema", _relative_path(error.path), error.reason)
        if error.entry_index is None:
            resource_issues.append(issue)
        else:
            per_entry.setdefault(error.entry_index, []).append(issue)
    for finding in check_consistency(entries):
        severity = "error" if finding.code in _CONSISTENCY_ERRORS else "warning"
        per_entry.setdefault(finding.entry_index, []).append(
            EntryIssue(severity, "consistency", finding.code, "entry", finding.message)
        )
    return EntryValidation(report=report, per_entry=per_entry, resource_issues=resource_issues)
