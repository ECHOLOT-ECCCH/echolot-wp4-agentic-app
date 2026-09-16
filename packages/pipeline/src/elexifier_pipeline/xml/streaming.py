"""Stream a large XML dictionary entry by entry and apply the mapping to each entry.

The whole-document converter needs the parsed tree in memory (a 150 MB export becomes a few GB).
Here ``iterparse`` yields entry elements as they close; each one is moved into a small replica of
its ancestor chain (root element, attributes and namespaces, intermediate wrappers) so that the
mapping's XPath expressions evaluate exactly as they would against the full document. Generated
ids share one counter across the stream, so the output equals a whole-document run.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, cast

from lxml import etree

from elexifier_pipeline.dmlex.writers import normalize
from elexifier_pipeline.xml.dmlexifier import (
    JsonDict,
    MappingError,
    TEltDesc,
    _Transformer,
    new_transformer,
    xpath_nodes,
)

_NAME = r"[A-Za-z_][\w.\-]*"
_SIMPLE_STEP = re.compile(rf"^(?:({_NAME}):)?({_NAME})$")


@dataclass
class Ancestor:
    tag: str
    attrib: dict[str, str]
    nsmap: dict[str | None, str]


@dataclass
class EntryPlan:
    """How to stream a mapping: the element that closes an entry and the descriptors to apply.

    ``entry_desc`` is the primary entry descriptor; it decides which element is streamed.
    ``entry_descs`` also holds further entry descriptors (multiword units, phrasal verbs as
    separate entries): they are evaluated on the same wrapped element, so they only find what
    sits inside a primary entry. ``warnings`` names the ones whose selector does not start there.
    """

    resource_desc: TEltDesc | None
    entry_desc: TEltDesc
    entry_local_name: str
    entry_namespace: str | None  # None = any namespace (no prefix in the selector)
    resource_scalars: JsonDict = field(default_factory=dict)
    entry_descs: list[TEltDesc] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.entry_descs:
            self.entry_descs = [self.entry_desc]


def _last_step(expr: str) -> str | None:
    """The final location step of an XPath, ignoring '/' inside predicates and strings; None for
    unions or other top-level operators that make the expression more than one path."""
    depth = 0
    quote: str | None = None
    start = 0
    for i, ch in enumerate(expr):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif depth == 0 and ch == "/":
            start = i + 1
        elif depth == 0 and ch == "|":
            return None
    return expr[start:] if depth == 0 and quote is None else None


def first_step_name(xpath: str) -> str | None:
    """Local name of the first location step (``se/pv/pvp`` -> ``se``, ``.//e`` -> ``e``)."""
    expr = re.sub(r"^\.?/{0,2}", "", xpath.strip())
    expr = re.sub(r"^(child::|descendant::|self::|descendant-or-self::)", "", expr)
    name = re.split(r"[/\[]", expr, maxsplit=1)[0]
    match = _SIMPLE_STEP.match(name)
    return match.group(2) if match else None


def simple_tag(xpath: str) -> tuple[str | None, str] | None:
    """Return (prefix, local name) when the last XPath step is a name test, optionally followed by
    predicates (``se``, ``.//tei:entry``, ``se[not(@hidden='y')]``), else None. Predicates are
    applied by the converter on the wrapped entry, so streaming only needs the element name."""
    expr = xpath.strip()
    if not expr:
        return None
    last = _last_step(expr)
    if last is None:
        return None
    last = re.sub(r"^(child::|descendant::|self::)", "", last)
    name_test = last.split("[", 1)[0]
    match = _SIMPLE_STEP.match(name_test)
    if not match:
        return None
    return match.group(1), match.group(2)


def plan_for(mapping: TEltDesc) -> EntryPlan:
    """Find the entry descriptor and the element it selects. Raises MappingError when the entry
    selector is too complex for streaming (then the caller falls back to whole-document mode)."""
    candidates: list[TEltDesc]
    if mapping.outElement == "entry":
        resource_desc: TEltDesc | None = None
        entry_desc = mapping
        candidates = [mapping]
    else:
        resource_desc = mapping
        candidates = [c for c in mapping.children or [] if c.outElement == "entry"]
        if not candidates:
            raise MappingError("the mapping has no 'entry' descriptor directly under the resource")
        entry_desc = candidates[0]
    parsed = simple_tag(entry_desc.inSelector)
    if parsed is None:
        raise MappingError(
            f"entry selector {entry_desc.inSelector!r} does not end in an element name; "
            "streaming needs a selector such as 'DictionaryEntry', './/entry', '/root/entry' "
            "or \"entry[not(@hidden='y')]\""
        )
    prefix, local = parsed
    namespace = None
    if prefix:
        namespace = (entry_desc.namespaces or {}).get(prefix) or (mapping.namespaces or {}).get(
            prefix
        )
        if namespace is None:
            raise MappingError(f"namespace prefix {prefix!r} is not declared in the mapping")
    warnings = [
        f"entries selected by {extra.inSelector!r} are only found inside <{local}> elements "
        "while streaming"
        for extra in candidates[1:]
        if first_step_name(extra.inSelector) != local
    ]
    return EntryPlan(
        resource_desc, entry_desc, local, namespace, entry_descs=candidates, warnings=warnings
    )


def _matches(tag: Any, plan: EntryPlan) -> bool:
    if not isinstance(tag, str):
        return False
    qname = etree.QName(tag)
    if qname.localname != plan.entry_local_name:
        return False
    return plan.entry_namespace is None or qname.namespace == plan.entry_namespace


def iter_entries(
    source: str | Path | IO[bytes], plan: EntryPlan
) -> Iterator[tuple[list[Ancestor], etree._Element]]:
    """Yield (ancestor chain, entry element) for every entry, freeing memory as it goes."""
    stack: list[Ancestor] = []
    parser_events = etree.iterparse(
        source,
        events=("start", "end"),
        load_dtd=False,
        no_network=True,
        resolve_entities=False,
        huge_tree=True,
    )
    depth_inside_entry = 0
    for event, element in parser_events:
        if event == "start":
            if depth_inside_entry:
                depth_inside_entry += 1
            elif _matches(element.tag, plan):
                depth_inside_entry = 1
            else:
                stack.append(Ancestor(element.tag, dict(element.attrib), dict(element.nsmap)))
            continue
        # end event
        if depth_inside_entry:
            depth_inside_entry -= 1
            if depth_inside_entry == 0:
                yield list(stack), element
                parent = element.getparent()
                if parent is not None:
                    parent.remove(element)  # also drops the element if the wrapper took it
                element.clear()
            continue
        if stack:
            stack.pop()
        # keep the partially built tree small: drop closed siblings we already passed
        if element.getparent() is not None:
            for previous in list(element.itersiblings(preceding=True)):
                element.getparent().remove(previous)


def _wrap(chain: list[Ancestor], entry: etree._Element) -> etree._ElementTree:
    """Rebuild the ancestor chain around ``entry`` (moving it out of the parse tree)."""
    root: etree._Element | None = None
    current: etree._Element | None = None
    for ancestor in chain:
        nsmap = cast(Any, ancestor.nsmap)  # lxml accepts None keys for the default namespace
        node = etree.Element(ancestor.tag, attrib=ancestor.attrib, nsmap=nsmap)
        if current is None:
            root = node
        else:
            current.append(node)
        current = node
    parent = entry.getparent()
    if parent is not None:
        parent.remove(entry)
    if current is None:
        return etree.ElementTree(entry)
    current.append(entry)
    assert root is not None
    return etree.ElementTree(root)


def wrap_element(chain: list[Ancestor], entry: etree._Element) -> etree._ElementTree:
    """Public name of :func:`_wrap` for other pipeline modules."""
    return _wrap(chain, entry)


def transform_wrapped(
    wrapped: etree._ElementTree, plan: EntryPlan, transformer: _Transformer
) -> list[tuple[TEltDesc, JsonDict]]:
    """Apply the entry descriptors inside a wrapped tree, mirroring the whole-document evaluation.
    Returns (descriptor, raw converter output) pairs; the descriptor is needed to normalize."""
    out: list[tuple[TEltDesc, JsonDict]] = []
    if plan.resource_desc is None:
        for desc in plan.entry_descs:
            out.extend((desc, e) for e in transformer.process_elt(wrapped, desc, None))
        return out
    contexts = xpath_nodes(wrapped, plan.resource_desc.inSelector, plan.resource_desc.namespaces)
    for context in contexts:
        for desc in plan.entry_descs:
            out.extend((desc, e) for e in transformer.process_elt(context, desc, None))
    return out


def resource_scalars(chain: list[Ancestor], plan: EntryPlan, transformer: _Transformer) -> JsonDict:
    """Resource-level text values (title, uri, langCode ...) evaluated on the ancestor replica."""
    if plan.resource_desc is None or not chain:
        return {}
    tree = _wrap(chain, etree.Element("placeholder"))
    root = tree.getroot()
    for placeholder in root.iter("placeholder"):
        parent = placeholder.getparent()
        if parent is not None:
            parent.remove(placeholder)
    out: JsonDict = {}
    for context in xpath_nodes(tree, plan.resource_desc.inSelector, plan.resource_desc.namespaces):
        for textDesc in plan.resource_desc.textVals or []:
            transformer.process_text(context, out, plan.resource_desc, textDesc, None)
        break
    return out


def stream_transform(
    source: str | Path | IO[bytes],
    mapping: TEltDesc,
    *,
    transformer: _Transformer | None = None,
    limit: int | None = None,
    plan: EntryPlan | None = None,
    normalized: bool = True,
) -> Iterator[JsonDict]:
    """Yield converted entries one at a time, with DMLex JSON names unless ``normalized`` is off.
    ``plan.resource_scalars`` is filled from the first entry's ancestors so callers can write the
    resource header."""
    plan = plan or plan_for(mapping)
    transformer = transformer or new_transformer()
    produced = 0
    for chain, element in iter_entries(source, plan):
        if not plan.resource_scalars and plan.resource_desc is not None:
            plan.resource_scalars = resource_scalars(chain, plan, transformer)
        wrapped = _wrap(chain, element)
        for desc, entry in transform_wrapped(wrapped, plan, transformer):
            yield normalize(entry, desc) if normalized else entry
            produced += 1
            if limit is not None and produced >= limit:
                return


# --- converting single indexed entries (samples) ---------------------------------------------

_SLOT = "elexifier-entry-slot"
_SLOT_RE = re.compile(rb"<elexifier-entry-slot[^>]*/>")
_ENTRY_COUNTER = "entry.id"


def head_ancestors(head: bytes, plan: EntryPlan) -> list[Ancestor]:
    """Ancestor chain of the first entry, from the bytes that precede it (root element, header
    wrappers). The head is cut where the entry starts, so parsing ends in an error by design."""
    stack: list[Ancestor] = []
    events = etree.iterparse(
        io.BytesIO(head),
        events=("start", "end"),
        load_dtd=False,
        no_network=True,
        resolve_entities=False,
        huge_tree=True,
    )
    try:
        for event, element in events:
            if event == "start":
                if _matches(element.tag, plan):
                    break
                stack.append(Ancestor(element.tag, dict(element.attrib), dict(element.nsmap)))
            elif stack:
                stack.pop()
    except etree.XMLSyntaxError:
        pass
    return stack


def wrap_fragment(chain: list[Ancestor], fragment: bytes) -> bytes:
    """Serialize the ancestor replica around an entry's source bytes, so namespaces and prefixes
    resolve as in the original document."""
    tree = _wrap(chain, etree.Element(_SLOT))
    serialized = etree.tostring(tree.getroot())
    return _SLOT_RE.sub(lambda _m: fragment, serialized, count=1)


def seed_entry_counter(transformer: _Transformer, ordinal: int) -> None:
    """Make the next generated top-level entry id ``entry.id.<ordinal>`` (1-based), so sample
    conversions carry the ids a full run gives to the same entries."""
    transformer.autogenCounters[_ENTRY_COUNTER] = ordinal - 1


def convert_fragment(
    chain: list[Ancestor],
    fragment: bytes,
    plan: EntryPlan,
    transformer: _Transformer,
    *,
    normalized: bool = True,
) -> list[JsonDict]:
    """Convert one indexed entry (its source bytes) exactly as the streaming run would."""
    parser = etree.XMLParser(
        load_dtd=False, no_network=True, resolve_entities=False, huge_tree=True
    )
    tree = etree.parse(io.BytesIO(wrap_fragment(chain, fragment)), parser)
    return [
        normalize(entry, desc) if normalized else entry
        for desc, entry in transform_wrapped(tree, plan, transformer)
    ]


def stream_source_entries(
    source: str | Path | IO[bytes], plan: EntryPlan, *, limit: int | None = None
) -> Iterator[tuple[int, etree._Element]]:
    """Yield (ordinal, element) for indexing entries without converting them."""
    for ordinal, (_chain, element) in enumerate(iter_entries(source, plan)):
        yield ordinal, element
        if limit is not None and ordinal + 1 >= limit:
            return
