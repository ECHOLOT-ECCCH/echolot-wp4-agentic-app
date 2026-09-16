"""What the DMLex XSD says about each element: attributes, ordered children, text content.

Derived once from the vendored ``schemas/dmlex.xsd`` so that the writer emits children in schema
order and the mapping checker knows the vocabulary. The JSON plural names follow the specification
("entries", "partsOfSpeech", "etymologies", "sameAs", everything else "+s").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from lxml import etree

XS = "{http://www.w3.org/2001/XMLSchema}"
DMLEX_NS = "http://docs.oasis-open.org/lexidma/ns/dmlex-1.0"
SCHEMA_PATH = Path(__file__).with_name("schemas") / "dmlex.xsd"

MARKER_ELEMENTS = frozenset({"placeholderMarker", "headwordMarker", "collocateMarker"})

_IRREGULAR_PLURALS = {
    "entry": "entries",
    "partOfSpeech": "partsOfSpeech",
    "forPartOfSpeech": "forPartsOfSpeech",
    "etymology": "etymologies",
    "sameAs": "sameAs",
}


def plural_of(element: str) -> str:
    return _IRREGULAR_PLURALS.get(element, element + "s")


@dataclass(frozen=True)
class ChildSpec:
    name: str
    min_occurs: int
    max_occurs: int | None  # None = unbounded


@dataclass(frozen=True)
class ElementSpec:
    name: str
    attributes: dict[str, bool]  # name -> required
    children: tuple[ChildSpec, ...]  # in schema order
    mixed: bool  # element text content allowed (definition, headword, ...)
    simple: bool  # simple content only (no children, text only)
    asserts: tuple[str, ...] = field(default_factory=tuple)

    @property
    def child_order(self) -> dict[str, int]:
        return {c.name: i for i, c in enumerate(self.children)}

    @property
    def child_names(self) -> set[str]:
        return {c.name for c in self.children}

    def is_text_bearing(self) -> bool:
        return self.mixed or self.simple

    def members(self) -> set[str]:
        """Scalar member names a mapping's textVals may produce for this element: attributes,
        text-bearing children without attributes of their own, and 'text' for the element's own
        text content. Markers are produced by ``markers`` on a text value, not by textVals."""
        names = set(self.attributes)
        for child in self.children:
            if child.name in MARKER_ELEMENTS:
                continue
            spec = element_spec(child.name)
            if spec is None or (
                spec.is_text_bearing() and not spec.attributes and not spec.children
            ):
                names.add(child.name)
            elif spec.is_text_bearing() and child.name == "headword":
                names.add(child.name)  # headword carries text plus optional placeholder markers
        if self.is_text_bearing():
            names.add("text")
        return names


def _occurs(node: etree._Element) -> tuple[int, int | None]:
    min_occurs = int(node.get("minOccurs", "1"))
    max_raw = node.get("maxOccurs", "1")
    return min_occurs, None if max_raw == "unbounded" else int(max_raw)


@lru_cache(maxsize=1)
def _load() -> dict[str, ElementSpec]:
    root = etree.parse(str(SCHEMA_PATH)).getroot()
    types = {ct.get("name"): ct for ct in root.findall(f"{XS}complexType") if ct.get("name")}
    simple_types = {st.get("name") for st in root.findall(f"{XS}simpleType") if st.get("name")}
    specs: dict[str, ElementSpec] = {}
    # global elements first, then local ones (headword, text, indicator ...) unless already known
    elements = list(root.findall(f"{XS}element")) + [
        e for e in root.iter(f"{XS}element") if e.getparent() is not root and e.get("name")
    ]
    for element in elements:
        name = element.get("name")
        if not name or name in specs:
            continue
        type_name = element.get("type")
        complex_type = element.find(f"{XS}complexType")
        if type_name and type_name in types:
            complex_type = types[type_name]
        if complex_type is None:
            specs[name] = ElementSpec(name, {}, (), mixed=False, simple=True)
            continue
        simple_content = complex_type.find(f"{XS}simpleContent")
        attributes: dict[str, bool] = {}
        for attr in complex_type.iter(f"{XS}attribute"):
            attr_name = attr.get("name") or attr.get("ref")
            if attr_name:
                attributes[attr_name.split(":")[-1]] = attr.get("use") == "required"
        children: list[ChildSpec] = []
        for child in complex_type.iter(f"{XS}element"):
            child_name = child.get("ref") or child.get("name")
            if child_name:
                lo, hi = _occurs(child)
                children.append(ChildSpec(child_name.split(":")[-1], lo, hi))
        asserts = tuple(a.get("test") or "" for a in complex_type.iter(f"{XS}assert"))
        specs[name] = ElementSpec(
            name,
            attributes,
            tuple(children),
            mixed=complex_type.get("mixed") == "true",
            simple=simple_content is not None
            or (type_name in simple_types if type_name else False),
            asserts=asserts,
        )
    return specs


def element_spec(name: str) -> ElementSpec | None:
    return _load().get(name)


def element_names() -> set[str]:
    return set(_load())


SCALAR_ONLY = frozenset({"headword", "text", "indicator", "description", "displayName", "note"})


def object_names() -> set[str]:
    """Elements that a mapping element descriptor may produce: complex objects with children or
    attributes. Scalars (headword, text, indicator ...) and markers come from textVals."""
    return {
        n
        for n, s in _load().items()
        if (s.children or s.attributes) and n not in SCALAR_ONLY and n not in MARKER_ELEMENTS
    }
