"""Serialise converter output (JSON-like dicts) as DMLex XML and JSON.

The converter produces nested dicts whose list keys are the mapping's ``jsonPlural`` names.
``normalize`` renames those to the DMLex JSON names ("entries", "senses", "partsOfSpeech" ...) using
the mapping; the XML writer then emits namespace-qualified elements with children in XSD order,
attributes where the XSD has attributes, text content where it is mixed or simple, and inline
markers (headword/collocate/placeholder) rebuilt from character offsets.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import IO, Any

from lxml import etree

from elexifier_pipeline.dmlex import schema
from elexifier_pipeline.dmlex.schema import DMLEX_NS, MARKER_ELEMENTS, plural_of
from elexifier_pipeline.xml.dmlexifier import TEltDesc

JsonDict = dict[str, Any]

# Which text member the markers of an element apply to (the JSON keeps markers beside the text).
MARKER_TARGETS: dict[str, str] = {
    "entry": "headword",
    "headwordTranslation": "text",
    "definition": "text",
    "example": "text",
    "exampleTranslation": "text",
    "headwordExplanation": "text",
}
MARKER_KEYS = {m: plural_of(m) for m in MARKER_ELEMENTS}  # headwordMarker -> headwordMarkers


# --- normalisation ---------------------------------------------------------------------------


def normalize(obj: JsonDict, desc: TEltDesc) -> JsonDict:
    """Rename list keys from the mapping's jsonPlural to the DMLex JSON names, recursively."""
    out: JsonDict = {}
    child_by_plural = {c.jsonPlural: c for c in desc.children or []}
    for key, value in obj.items():
        child = child_by_plural.get(key)
        if child is not None and isinstance(value, list):
            out[plural_of(child.outElement)] = [
                normalize(item, child) if isinstance(item, dict) else item for item in value
            ]
        else:
            out[key] = value
    return out


def entries_of(resource: JsonDict, desc: TEltDesc) -> list[JsonDict]:
    entry_desc = next((c for c in desc.children or [] if c.outElement == "entry"), None)
    if entry_desc is None:
        return []
    return [normalize(e, entry_desc) for e in resource.get(entry_desc.jsonPlural, [])]


def resource_scalars(resource: JsonDict, desc: TEltDesc) -> JsonDict:
    plurals = {c.jsonPlural for c in desc.children or []}
    return {k: v for k, v in resource.items() if k not in plurals}


# --- XML -----------------------------------------------------------------------------------


@dataclass
class WriterReport:
    entries: int = 0
    warnings: list[str] = field(default_factory=list)
    dropped_duplicates: dict[str, int] = field(default_factory=dict)
    _seen: set[str] = field(default_factory=set)

    def warn(self, message: str) -> None:
        if message not in self._seen:
            self._seen.add(message)
            self.warnings.append(message)


def qname(name: str) -> str:
    return f"{{{DMLEX_NS}}}{name}"


def _text(value: Any) -> str:
    return "" if value is None else str(value)


@dataclass
class _Marker:
    name: str
    start: int
    end: int
    payload: JsonDict
    nested: list[_Marker] = field(default_factory=list)


def _build_marked_text(
    element: etree._Element,
    text: str,
    markers: list[tuple[str, JsonDict]],
    tag: Callable[[str], str],
) -> None:
    """Set ``text`` on ``element`` with markers as nested inline children."""
    items: list[_Marker] = []
    for name, payload in markers:
        try:
            start, end = int(payload["startIndex"]), int(payload["endIndex"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= start <= end <= len(text):
            continue
        items.append(_Marker(name, start, end, payload))
    items.sort(key=lambda m: (m.start, -m.end))
    root = _Marker("", 0, len(text), {})
    stack = [root]
    for marker in items:
        while len(stack) > 1 and stack[-1].end <= marker.start:
            stack.pop()
        if stack[-1].start <= marker.start and marker.end <= stack[-1].end:
            stack[-1].nested.append(marker)
            stack.append(marker)

    def populate(node: etree._Element, marker: _Marker) -> None:
        if not marker.nested:
            node.text = text[marker.start : marker.end]
            return
        cursor = marker.start
        last: etree._Element | None = None
        for child in marker.nested:
            segment = text[cursor : child.start]
            if last is None:
                node.text = segment
            else:
                last.tail = segment
            child_el = etree.SubElement(node, tag(child.name))
            for key, value in child.payload.items():
                if key in ("startIndex", "endIndex"):
                    continue
                if key == plural_of("label") and isinstance(value, list):
                    for label in value:
                        if isinstance(label, dict) and label.get("tag"):
                            etree.SubElement(child_el, tag("label"), tag=str(label["tag"]))
                elif value is not None and not isinstance(value, list | dict):
                    child_el.set(key, str(value))
            populate(child_el, child)
            cursor = child.end
            last = child_el
        if last is not None:
            last.tail = text[cursor : marker.end]

    populate(element, root)


class DmlexXmlBuilder:
    """Builds lxml elements. With ``namespaced=False`` tags are plain so that fragments written
    inside a root that declares the default namespace inherit it without repeating ``xmlns``."""

    def __init__(self, report: WriterReport | None = None, *, namespaced: bool = True) -> None:
        self.report = report or WriterReport()
        self.namespaced = namespaced

    def tag(self, name: str) -> str:
        return qname(name) if self.namespaced else name

    def _new(self, name: str, root: bool = False) -> etree._Element:
        if self.namespaced and root:
            return etree.Element(self.tag(name), nsmap={None: DMLEX_NS})  # type: ignore[dict-item]
        return etree.Element(self.tag(name))

    def build(
        self, name: str, obj: JsonDict, parent: str | None = None, *, root: bool = False
    ) -> etree._Element:
        spec = schema.element_spec(name, parent)
        element = self._new(name, root)
        if spec is None:
            self.report.warn(f"unknown DMLex element '{name}' written without schema guidance")
            for key, value in obj.items():
                if isinstance(value, list | dict):
                    continue
                element.set(key, _text(value))
            return element
        used: set[str] = set()
        for attr in spec.attributes:
            if attr in obj and obj[attr] is not None and obj[attr] != "":
                element.set(attr, _text(obj[attr]))
            used.add(attr)
        marker_lists = self._markers_for(name, obj, used)
        # own text content (mixed or simple types)
        if spec.is_text_bearing() and "text" in obj:
            used.add("text")
            if spec.mixed:
                _build_marked_text(element, _text(obj["text"]), marker_lists, self.tag)
            else:
                element.text = _text(obj["text"])
        for child in spec.children:
            if child.name in MARKER_ELEMENTS:
                continue
            plural = plural_of(child.name)
            if plural in obj and isinstance(obj[plural], list):
                used.add(plural)
                seen: list[Any] = []
                for item in obj[plural]:
                    if item in seen:
                        # DMLex requires siblings to be unique; identical repeats carry nothing
                        self.report.dropped_duplicates[child.name] = (
                            self.report.dropped_duplicates.get(child.name, 0) + 1
                        )
                        continue
                    seen.append(item)
                    if isinstance(item, dict):
                        element.append(self.build(child.name, item, name))
                    else:
                        element.append(self._scalar_child(child.name, item, name, []))
                continue
            if child.name in obj:
                used.add(child.name)
                value = obj[child.name]
                values = value if isinstance(value, list) else [value]
                target_markers = marker_lists if MARKER_TARGETS.get(name) == child.name else []
                for item in values:
                    if isinstance(item, dict):
                        element.append(self.build(child.name, item, name))
                    else:
                        element.append(self._scalar_child(child.name, item, name, target_markers))
        for key in obj:
            if key not in used and key not in MARKER_KEYS.values():
                self.report.warn(f"'{key}' is not a member of '{name}' in DMLex; value dropped")
        return element

    def _markers_for(self, name: str, obj: JsonDict, used: set[str]) -> list[tuple[str, JsonDict]]:
        markers: list[tuple[str, JsonDict]] = []
        for marker_name, key in MARKER_KEYS.items():
            if key in obj and isinstance(obj[key], list):
                used.add(key)
                markers.extend((marker_name, m) for m in obj[key] if isinstance(m, dict))
        return markers

    def _scalar_child(
        self, name: str, value: Any, parent: str, markers: list[tuple[str, JsonDict]]
    ) -> etree._Element:
        child = etree.Element(self.tag(name))
        spec = schema.element_spec(name, parent)
        if markers and spec is not None and spec.mixed:
            _build_marked_text(child, _text(value), markers, self.tag)
        else:
            child.text = _text(value)
        return child


def resource_element(scalars: JsonDict, report: WriterReport | None = None) -> etree._Element:
    """The <lexicographicResource> element with its attributes (no entries)."""
    builder = DmlexXmlBuilder(report)
    attrs = {k: v for k, v in scalars.items() if not isinstance(v, list)}
    return builder.build("lexicographicResource", attrs, root=True)


def entry_element(entry: JsonDict, report: WriterReport | None = None) -> etree._Element:
    return DmlexXmlBuilder(report).build("entry", entry, "lexicographicResource", root=True)


def write_xml(
    out: IO[bytes],
    scalars: JsonDict,
    entries: Iterable[JsonDict],
    report: WriterReport | None = None,
) -> WriterReport:
    """Stream a DMLex XML document: header, one entry at a time, footer."""
    report = report or WriterReport()
    root = resource_element(scalars, report)
    start = etree.tostring(root, encoding="unicode")
    assert start.endswith("/>")
    out.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
    out.write((start[:-2] + ">\n").encode("utf-8"))
    builder = DmlexXmlBuilder(report, namespaced=False)  # inherits the root's default namespace
    for entry in entries:
        element = builder.build("entry", entry, "lexicographicResource")
        etree.indent(element, space="  ", level=1)
        out.write(b"  " + etree.tostring(element, encoding="utf-8") + b"\n")
        report.entries += 1
    # the XSD sequence puts translationLanguage and the tag definitions after the entries
    for key, value in scalars.items():
        if not isinstance(value, list) or key == plural_of("entry"):
            continue
        name = _singular_of(key)
        if name is None:
            report.warn(f"'{key}' is not a member of 'lexicographicResource' in DMLex; dropped")
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            element = builder.build(name, item, "lexicographicResource")
            etree.indent(element, space="  ", level=1)
            out.write(b"  " + etree.tostring(element, encoding="utf-8") + b"\n")
    out.write(b"</lexicographicResource>\n")
    return report


def _singular_of(plural: str) -> str | None:
    for name in schema.object_names():
        if plural_of(name) == plural:
            return name
    return None


def write_json(out: IO[str], scalars: JsonDict, entries: Iterable[JsonDict]) -> int:
    """Stream DMLex JSON: the resource object with an ``entries`` array."""
    out.write("{\n")
    first = True
    for key, value in scalars.items():
        if key == plural_of("entry"):
            continue
        out.write(f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)},\n")
    out.write('  "entries": [\n')
    count = 0
    for entry in entries:
        if not first:
            out.write(",\n")
        first = False
        out.write("    " + json.dumps(entry, ensure_ascii=False))
        count += 1
    out.write("\n  ]\n}\n")
    return count


def iter_normalized(entries: Iterable[JsonDict], entry_desc: TEltDesc) -> Iterator[JsonDict]:
    for entry in entries:
        yield normalize(entry, entry_desc)
