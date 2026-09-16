"""Stream a large XML dictionary entry by entry and apply the mapping to each entry.

The whole-document converter needs the parsed tree in memory (a 150 MB export becomes a few GB).
Here ``iterparse`` yields entry elements as they close; each one is moved into a small replica of
its ancestor chain (root element, attributes and namespaces, intermediate wrappers) so that the
mapping's XPath expressions evaluate exactly as they would against the full document. Generated
ids share one counter across the stream, so the output equals a whole-document run.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, cast

from lxml import etree

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
    """How to stream a mapping: the element that closes an entry and the descriptors to apply."""

    resource_desc: TEltDesc | None
    entry_desc: TEltDesc
    entry_local_name: str
    entry_namespace: str | None  # None = any namespace (no prefix in the selector)
    resource_scalars: JsonDict = field(default_factory=dict)


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
    if mapping.outElement == "entry":
        resource_desc: TEltDesc | None = None
        entry_desc = mapping
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
    return EntryPlan(resource_desc, entry_desc, local, namespace)


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


def transform_wrapped(
    wrapped: etree._ElementTree, plan: EntryPlan, transformer: _Transformer
) -> list[JsonDict]:
    """Apply the entry descriptor inside a wrapped tree, mirroring the whole-document evaluation."""
    if plan.resource_desc is None:
        return transformer.process_elt(wrapped, plan.entry_desc, None)
    contexts = xpath_nodes(wrapped, plan.resource_desc.inSelector, plan.resource_desc.namespaces)
    out: list[JsonDict] = []
    for context in contexts:
        out.extend(transformer.process_elt(context, plan.entry_desc, None))
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
) -> Iterator[JsonDict]:
    """Yield converted entries one at a time. ``plan.resource_scalars`` is filled from the first
    entry's ancestors so callers can write the resource header."""
    plan = plan or plan_for(mapping)
    transformer = transformer or new_transformer()
    produced = 0
    for chain, element in iter_entries(source, plan):
        if not plan.resource_scalars and plan.resource_desc is not None:
            plan.resource_scalars = resource_scalars(chain, plan, transformer)
        wrapped = _wrap(chain, element)
        for entry in transform_wrapped(wrapped, plan, transformer):
            yield entry
            produced += 1
            if limit is not None and produced >= limit:
                return


def stream_source_entries(
    source: str | Path | IO[bytes], plan: EntryPlan, *, limit: int | None = None
) -> Iterator[tuple[int, etree._Element]]:
    """Yield (ordinal, element) for indexing entries without converting them."""
    for ordinal, (_chain, element) in enumerate(iter_entries(source, plan)):
        yield ordinal, element
        if limit is not None and ordinal + 1 >= limit:
            return
