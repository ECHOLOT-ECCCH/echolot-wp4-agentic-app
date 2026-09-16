"""Strict model of the mapping format (docs/PLAN.md 7.8b) with checks the agent can rely on.

The converter itself is permissive; this module is what ``mapping_write`` and the UI validate
against: unknown keys are rejected, XPath and regex must compile, output names must exist in DMLex,
and structural requirements of the XSD (headword, resource uri/langCode, sense ids) are flagged.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from lxml import etree
from pydantic import BaseModel, ConfigDict, ValidationError

from elexifier_pipeline.dmlex import schema
from elexifier_pipeline.xml.dmlexifier import (
    ATTR_AUTO,
    ATTR_CONSTANT,
    ATTR_INNER_TEXT,
    ATTR_INNER_TEXT_REC,
    NS_META,
    MappingError,
    TEltDesc,
)
from elexifier_pipeline.xml.streaming import simple_tag

Severity = Literal["error", "warning", "info"]


class Issue(BaseModel):
    severity: Severity
    path: str  # descriptor path such as "lexicographicResource/entry/sense[textVals:id]"
    message: str


class MarkerDesc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inSelector: str | None = None
    outElement: str
    jsonPlural: str | None = None
    regex: str | None = None
    regexGroup: int | str | None = None
    namespaces: dict[str, str] | None = None


class TextValDesc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inSelector: str | None = None
    namespaces: dict[str, str] | None = None
    attribute: str
    constant: str = ""
    regex: str | None = None
    regexGroup: int | str | None = None
    outElement: str
    xlat: dict[str, str] | None = None
    markers: list[MarkerDesc] | None = None
    excludeSelector: str | None = None
    normalizeWhitespace: bool = False


class EltDesc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inSelector: str
    outElement: str
    jsonPlural: str | None = None
    namespaces: dict[str, str] | None = None
    children: list[EltDesc] | None = None
    textVals: list[TextValDesc] | None = None
    copyToOutElt: dict[str, Any] | None = None

    def to_desc(self) -> TEltDesc:
        return TEltDesc(self.model_dump(exclude_none=True))


MappingSpec = EltDesc


def load_mapping_spec(data: dict[str, Any]) -> MappingSpec:
    """Validate the raw JSON shape. Raises MappingError with readable messages."""
    try:
        return EltDesc.model_validate(data)
    except ValidationError as exc:
        lines = []
        for error in exc.errors():
            location = "/".join(str(p) for p in error["loc"]) or "<root>"
            lines.append(f"{location}: {error['msg']}")
        raise MappingError("invalid mapping: " + "; ".join(lines[:12])) from exc


def mapping_json_schema() -> dict[str, Any]:
    return EltDesc.model_json_schema()


def _plain_attribute(name: str) -> bool:
    return not name.startswith("{")


CROSSLINGUAL_WITH_LANG = ("headwordTranslation", "headwordExplanation", "exampleTranslation")


def check_mapping(spec: MappingSpec) -> list[Issue]:
    """Structural checks beyond the JSON shape; errors mean the output cannot be valid DMLex."""
    issues: list[Issue] = []
    known_objects = schema.object_names()
    # XSD: translations need @langCode. The schema's assertion allows omitting it when the
    # resource declares exactly one translationLanguage, but XSD 1.1 assertions cannot see
    # ancestors, so validators reject it anyway; always require the attribute.

    def check_xpath(
        expr: str | None, namespaces: dict[str, str] | None, path: str, label: str
    ) -> None:
        if not expr:
            return
        try:
            etree.XPath(expr, namespaces=namespaces or None)
        except etree.XPathSyntaxError as exc:
            issues.append(
                Issue(severity="error", path=path, message=f"{label} XPath is invalid: {exc}")
            )

    def check_regex(pattern: str | None, path: str) -> None:
        if pattern is None:
            return
        try:
            re.compile(pattern)
        except re.error as exc:
            issues.append(Issue(severity="error", path=path, message=f"regex is invalid: {exc}"))

    def visit(desc: EltDesc, path: str, parent: EltDesc | None) -> None:
        check_xpath(desc.inSelector, desc.namespaces, path, "inSelector")
        element = schema.element_spec(desc.outElement)
        if desc.outElement not in known_objects:
            issues.append(
                Issue(
                    severity="error",
                    path=path,
                    message=f"'{desc.outElement}' is not a DMLex object element; "
                    f"scalar values (headword, text, indicator) belong in textVals",
                )
            )
        elif parent is not None:
            parent_spec = schema.element_spec(parent.outElement)
            if parent_spec is not None and desc.outElement not in parent_spec.child_names:
                issues.append(
                    Issue(
                        severity="error",
                        path=path,
                        message=f"'{desc.outElement}' cannot be a child of "
                        f"'{parent.outElement}' in DMLex",
                    )
                )
        members = element.members() if element else set()
        produced: set[str] = set()
        for i, text in enumerate(desc.textVals or []):
            tpath = f"{path}[textVals:{text.outElement or i}]"
            produced.add(text.outElement)
            check_xpath(text.inSelector, text.namespaces or desc.namespaces, tpath, "inSelector")
            check_xpath(
                text.excludeSelector, text.namespaces or desc.namespaces, tpath, "excludeSelector"
            )
            check_regex(text.regex, tpath)
            if element and text.outElement not in members:
                issues.append(
                    Issue(
                        severity="error",
                        path=tpath,
                        message=f"'{text.outElement}' is not a member of '{desc.outElement}' "
                        f"(known: {', '.join(sorted(members))})",
                    )
                )
            if text.attribute == ATTR_CONSTANT and not text.constant:
                issues.append(Issue(severity="warning", path=tpath, message="constant is empty"))
            if (
                text.attribute.startswith("{")
                and text.attribute
                not in (
                    ATTR_INNER_TEXT,
                    ATTR_INNER_TEXT_REC,
                    ATTR_CONSTANT,
                    ATTR_AUTO,
                )
                and text.attribute.startswith(f"{{{NS_META}}}")
            ):
                issues.append(
                    Issue(
                        severity="error",
                        path=tpath,
                        message=f"unknown meta attribute {text.attribute}",
                    )
                )
            if text.markers and text.normalizeWhitespace:
                issues.append(
                    Issue(
                        severity="warning",
                        path=tpath,
                        message="normalizeWhitespace is ignored when markers are present",
                    )
                )
            for marker in text.markers or []:
                mpath = f"{tpath}[marker:{marker.outElement}]"
                if marker.outElement not in schema.MARKER_ELEMENTS:
                    issues.append(
                        Issue(
                            severity="error",
                            path=mpath,
                            message=f"'{marker.outElement}' is not a marker element",
                        )
                    )
                check_xpath(
                    marker.inSelector, marker.namespaces or text.namespaces, mpath, "inSelector"
                )
                check_regex(marker.regex, mpath)
        for child in desc.children or []:
            visit(child, f"{path}/{child.outElement}", desc)
        # requirements from the XSD
        if desc.outElement == "entry" and simple_tag(desc.inSelector) is None:
            issues.append(
                Issue(
                    severity="error",
                    path=path,
                    message="the entry selector must end in an element name, optionally with "
                    "predicates (e.g. \"entry[not(@hidden='y')]\"), so large files can be streamed",
                )
            )
        if desc.outElement == "entry" and "headword" not in produced:
            issues.append(
                Issue(severity="error", path=path, message="an entry needs a 'headword' text value")
            )
        if desc.outElement == "lexicographicResource":
            for required in ("uri", "langCode"):
                if required not in produced:
                    issues.append(
                        Issue(
                            severity="error",
                            path=path,
                            message=f"lexicographicResource needs a '{required}' text value "
                            "(a constant is fine)",
                        )
                    )
            if not any(c.outElement == "entry" for c in desc.children or []):
                issues.append(
                    Issue(
                        severity="error",
                        path=path,
                        message="no 'entry' descriptor under the resource",
                    )
                )
        if desc.outElement in CROSSLINGUAL_WITH_LANG and "langCode" not in produced:
            issues.append(
                Issue(
                    severity="error",
                    path=path,
                    message=f"'{desc.outElement}' needs a 'langCode' text value (a constant is "
                    "fine); the schema validator rejects translations without one",
                )
            )
        if desc.outElement == "sense" and "id" not in produced:
            issues.append(
                Issue(
                    severity="error",
                    path=path,
                    message="a sense needs an 'id' (use the autogenerated attribute)",
                )
            )
        if desc.outElement == "entry" and "id" not in produced:
            issues.append(
                Issue(
                    severity="warning",
                    path=path,
                    message="entries without an 'id' cannot be referenced; "
                    "consider autogenerated ids",
                )
            )
        if (
            desc.outElement == "pronunciation"
            and not any(c.outElement == "transcription" for c in desc.children or [])
            and "soundFile" not in produced
        ):
            issues.append(
                Issue(
                    severity="error",
                    path=path,
                    message="a pronunciation needs a transcription child or a soundFile",
                )
            )
        if element is None and desc.outElement in known_objects:
            pass
        if desc.jsonPlural and desc.jsonPlural != schema.plural_of(desc.outElement):
            issues.append(
                Issue(
                    severity="info",
                    path=path,
                    message=f"jsonPlural '{desc.jsonPlural}' differs from the DMLex JSON name "
                    f"'{schema.plural_of(desc.outElement)}'; output uses the DMLex name",
                )
            )

    visit(spec, spec.outElement, None)
    return issues


def has_errors(issues: list[Issue]) -> bool:
    return any(i.severity == "error" for i in issues)


def summarize(spec: MappingSpec, indent: str = "") -> str:
    """Plain-language rendering of the mapping tree for the chat and the spec document."""
    lines = [f"{indent}{spec.outElement} <- {spec.inSelector}"]
    for text in spec.textVals or []:
        source = {
            ATTR_INNER_TEXT: "text",
            ATTR_INNER_TEXT_REC: "text (with descendants)",
            ATTR_CONSTANT: f"constant {text.constant!r}",
            ATTR_AUTO: "generated id",
        }.get(
            text.attribute,
            f"@{text.attribute}" if _plain_attribute(text.attribute) else text.attribute,
        )
        where = f" of {text.inSelector}" if text.inSelector else ""
        extras = []
        if text.regex:
            extras.append(f"regex {text.regex!r}")
        if text.xlat:
            extras.append(f"{len(text.xlat)} value translations")
        if text.excludeSelector:
            extras.append(f"excluding {text.excludeSelector}")
        suffix = f" ({', '.join(extras)})" if extras else ""
        lines.append(f"{indent}  .{text.outElement} = {source}{where}{suffix}")
    for child in spec.children or []:
        lines.append(summarize(child, indent + "  "))
    return "\n".join(lines)


__all__ = [
    "EltDesc",
    "Issue",
    "MappingSpec",
    "MarkerDesc",
    "TextValDesc",
    "check_mapping",
    "has_errors",
    "load_mapping_spec",
    "mapping_json_schema",
    "summarize",
]
