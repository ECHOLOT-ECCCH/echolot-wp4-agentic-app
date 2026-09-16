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
            spec = element_spec(child.name, self.name)
            if spec is None:
                names.add(child.name)
                continue
            only_markers = all(c.name in MARKER_ELEMENTS for c in spec.children)
            if spec.is_text_bearing() and not spec.attributes and only_markers:
                names.add(child.name)  # a string member, possibly with inline markers
        if self.is_text_bearing():
            names.add("text")
        return names


def _occurs(node: etree._Element) -> tuple[int, int | None]:
    min_occurs = int(node.get("minOccurs", "1"))
    max_raw = node.get("maxOccurs", "1")
    return min_occurs, None if max_raw == "unbounded" else int(max_raw)


def _direct_children(complex_type: etree._Element) -> list[ChildSpec]:
    """Child elements declared in this type's own content model (nested local types excluded)."""
    found: list[ChildSpec] = []

    def walk(node: etree._Element) -> None:
        for child in node:
            if (
                child.tag in (f"{XS}sequence", f"{XS}choice", f"{XS}all", f"{XS}extension")
                or child.tag == f"{XS}complexContent"
            ):
                walk(child)
            elif child.tag == f"{XS}element":
                name = child.get("ref") or child.get("name")
                if name:
                    lo, hi = _occurs(child)
                    found.append(ChildSpec(name.split(":")[-1], lo, hi))

    walk(complex_type)
    return found


def _direct_attributes(complex_type: etree._Element) -> dict[str, bool]:
    attributes: dict[str, bool] = {}

    def walk(node: etree._Element) -> None:
        for child in node:
            if child.tag == f"{XS}attribute":
                name = child.get("name") or child.get("ref")
                if name:
                    attributes[name.split(":")[-1]] = child.get("use") == "required"
            elif child.tag in (f"{XS}simpleContent", f"{XS}complexContent", f"{XS}extension"):
                walk(child)

    walk(complex_type)
    return attributes


def _spec_from(
    name: str, element: etree._Element, types: dict[str, etree._Element], simple_types: set[str]
) -> ElementSpec:
    type_name = element.get("type")
    complex_type = element.find(f"{XS}complexType")
    if type_name and type_name in types:
        complex_type = types[type_name]
    if complex_type is None:
        return ElementSpec(name, {}, (), mixed=False, simple=True)
    simple_content = complex_type.find(f"{XS}simpleContent")
    asserts = tuple(a.get("test") or "" for a in complex_type.findall(f"{XS}assert"))
    return ElementSpec(
        name,
        _direct_attributes(complex_type),
        tuple(_direct_children(complex_type)),
        mixed=complex_type.get("mixed") == "true",
        simple=simple_content is not None or (type_name in simple_types if type_name else False),
        asserts=asserts,
    )


@lru_cache(maxsize=1)
def _load() -> dict[str, ElementSpec]:
    root = etree.parse(str(SCHEMA_PATH)).getroot()
    types: dict[str, etree._Element] = {
        str(ct.get("name")): ct for ct in root.findall(f"{XS}complexType") if ct.get("name")
    }
    simple_types: set[str] = {
        str(st.get("name")) for st in root.findall(f"{XS}simpleType") if st.get("name")
    }
    specs: dict[str, ElementSpec] = {}
    global_elements: dict[str, etree._Element] = {
        str(e.get("name")): e for e in root.findall(f"{XS}element") if e.get("name")
    }
    for name, element in global_elements.items():
        specs[name] = _spec_from(name, element, types, simple_types)
    # Local elements (headword, text, indicator ...) are keyed as "parent.name" and, when not
    # shadowing a global element, also by bare name.
    for element in root.iter(f"{XS}element"):
        name = element.get("name")
        if not name or element.getparent() is root:
            continue
        owner: etree._Element | None = element.getparent()
        parent_name = None
        while owner is not None:
            if owner.tag == f"{XS}complexType" and owner.get("name"):
                parent_name = _type_owner(owner.get("name") or "", global_elements)
                break
            if owner.tag == f"{XS}element" and owner.get("name"):
                parent_name = owner.get("name")
                break
            owner = owner.getparent()
        spec = _spec_from(name, element, types, simple_types)
        if parent_name:
            specs[f"{parent_name}.{name}"] = spec
        specs.setdefault(name, spec)
    return specs


def _type_owner(type_name: str, global_elements: dict[str, etree._Element]) -> str | None:
    for name, element in global_elements.items():
        if element.get("type") == type_name:
            return name
    return None


def element_spec(name: str, parent: str | None = None) -> ElementSpec | None:
    """Spec of an element, preferring the local declaration inside ``parent`` when given."""
    specs = _load()
    if parent and f"{parent}.{name}" in specs:
        return specs[f"{parent}.{name}"]
    return specs.get(name)


def element_names() -> set[str]:
    return {n for n in _load() if "." not in n}


SCALAR_ONLY = frozenset({"headword", "text", "indicator", "description", "displayName", "note"})


def object_names() -> set[str]:
    """Elements that a mapping element descriptor may produce: complex objects with children or
    attributes. Scalars (headword, text, indicator ...) and markers come from textVals."""
    return {
        n
        for n, s in _load().items()
        if "." not in n
        and (s.children or s.attributes)
        and n not in SCALAR_ONLY
        and n not in MARKER_ELEMENTS
    }
