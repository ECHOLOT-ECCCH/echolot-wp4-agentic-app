"""Profile an XML dictionary and index its entries by byte offset (one expat pass each).

The profile tells the agent what the document looks like (element counts, attributes, depth,
where entries probably are). The index gives every entry an ordinal, a headword guess and the byte
range of its source XML, so entries can be fetched from storage with a range read instead of
re-parsing the file.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any
from xml.parsers import expat

from lxml import etree
from pydantic import BaseModel, Field

CHUNK = 1 << 20
HEADWORD_TAGS = re.compile(
    r"^(hw|headword|hwd|head|orth|lemma|form|key|word|title|lex|entry_?head|hd|lem|ke[yr]word)$",
    re.I,
)
HEADWORD_ATTRS = ("identifier", "id", "lemma", "headword", "key", "sortKey", "n")
SHORT_TEXT = 80


class ElementStat(BaseModel):
    tag: str
    count: int
    depth: int  # minimum depth at which the element occurs (root = 0)
    parents: dict[str, int] = Field(default_factory=dict)
    attributes: dict[str, int] = Field(default_factory=dict)
    has_text: bool = False
    sample_values: list[str] = Field(default_factory=list)


class EntryCandidate(BaseModel):
    xpath: str
    tag: str
    count: int
    depth: int
    score: float
    reason: str


class XmlProfile(BaseModel):
    size_bytes: int
    root_tag: str
    namespaces: dict[str, str] = Field(default_factory=dict)
    total_elements: int
    element_stats: list[ElementStat]
    entry_candidates: list[EntryCandidate]
    doctype: str | None = None


class EntryRef(BaseModel):
    ordinal: int
    start: int
    end: int
    headword: str | None = None
    snippet: str = ""


@dataclass
class _Scan:
    counts: Counter[str] = field(default_factory=Counter)
    parents: Counter[tuple[str, str]] = field(default_factory=Counter)
    attributes: dict[str, Counter[str]] = field(default_factory=dict)
    depth_min: dict[str, int] = field(default_factory=dict)
    has_text: set[str] = field(default_factory=set)
    samples: dict[str, list[str]] = field(default_factory=dict)
    root_tag: str | None = None
    doctype: str | None = None
    stack: list[str] = field(default_factory=list)
    text_buffer: list[str] = field(default_factory=list)


def _local(name: str) -> str:
    return name.rsplit(":", 1)[-1]


def _all_text(element: etree._Element) -> str:
    return " ".join("".join(str(t) for t in element.itertext()).split())


def _feed(parser: Any, source: str | Path | IO[bytes]) -> None:
    if isinstance(source, str | Path):
        with open(source, "rb") as fh:
            while chunk := fh.read(CHUNK):
                parser.Parse(chunk, False)
    else:
        while chunk := source.read(CHUNK):
            parser.Parse(chunk, False)
    parser.Parse(b"", True)


def profile_xml(source: str | Path | IO[bytes], *, size_bytes: int | None = None) -> XmlProfile:
    """Element statistics and entry candidates for the whole document (streaming)."""
    scan = _Scan()
    parser = expat.ParserCreate()
    parser.buffer_text = True
    sample_budget = 5

    def start(name: str, attrs: dict[str, str]) -> None:
        if scan.root_tag is None:
            scan.root_tag = name
        scan.counts[name] += 1
        if scan.stack:
            scan.parents[(name, scan.stack[-1])] += 1
        depth = len(scan.stack)
        if name not in scan.depth_min or depth < scan.depth_min[name]:
            scan.depth_min[name] = depth
        if attrs:
            bucket = scan.attributes.setdefault(name, Counter())
            for key in attrs:
                bucket[key] += 1
        scan.stack.append(name)
        scan.text_buffer.clear()

    def chars(data: str) -> None:
        if data.strip():
            scan.text_buffer.append(data)

    def end(name: str) -> None:
        scan.stack.pop()
        if scan.text_buffer:
            scan.has_text.add(name)
            text = " ".join("".join(scan.text_buffer).split())
            samples = scan.samples.setdefault(name, [])
            if (
                text
                and len(text) <= SHORT_TEXT
                and len(samples) < sample_budget
                and text not in samples
            ):
                samples.append(text)
            scan.text_buffer.clear()

    def doctype(name: str, sysid: str | None, pubid: str | None, _has_internal: bool) -> None:
        scan.doctype = f"{name} {sysid or ''}".strip()

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    parser.StartDoctypeDeclHandler = doctype
    _feed(parser, source)

    if size_bytes is None and isinstance(source, str | Path):
        size_bytes = Path(source).stat().st_size
    stats = [
        ElementStat(
            tag=tag,
            count=count,
            depth=scan.depth_min.get(tag, 0),
            parents={p: n for (child, p), n in scan.parents.items() if child == tag},
            attributes=dict(scan.attributes.get(tag, {})),
            has_text=tag in scan.has_text,
            sample_values=scan.samples.get(tag, []),
        )
        for tag, count in scan.counts.most_common(200)
    ]
    root = scan.root_tag or ""
    return XmlProfile(
        size_bytes=size_bytes or 0,
        root_tag=root,
        namespaces=_namespaces_from_root(source) if isinstance(source, str | Path) else {},
        total_elements=sum(scan.counts.values()),
        element_stats=stats,
        entry_candidates=entry_candidates(scan, root),
        doctype=scan.doctype,
    )


def _namespaces_from_root(source: str | Path) -> dict[str, str]:
    try:
        for _event, element in etree.iterparse(
            str(source), events=("start",), load_dtd=False, no_network=True, resolve_entities=False
        ):
            return {(k or ""): v for k, v in element.nsmap.items()}
    except etree.XMLSyntaxError:
        return {}
    return {}


def entry_candidates(scan: _Scan, root: str) -> list[EntryCandidate]:
    """Rank elements that look like dictionary entries: many repetitions of one element under one
    parent, shallow in the tree, containing text somewhere below (checked by the caller later)."""
    candidates: list[EntryCandidate] = []
    for tag, count in scan.counts.items():
        if tag == root or count < 2:
            continue
        depth = scan.depth_min.get(tag, 99)
        parents = {p: n for (child, p), n in scan.parents.items() if child == tag}
        main_parent, under_parent = (
            max(parents.items(), key=lambda kv: kv[1]) if parents else ("", 0)
        )
        if depth > 4:
            continue
        # entries: dominant single parent, close to the root, many of them
        concentration = under_parent / count if count else 0
        score = count * concentration / (1 + depth * 0.5)
        if depth == 1:
            score *= 2
        reason = f"{count} occurrences, {under_parent} under <{main_parent}>, depth {depth}"
        xpath = f"/{root}/{tag}" if depth == 1 else f"//{tag}"
        candidates.append(
            EntryCandidate(
                xpath=xpath, tag=tag, count=count, depth=depth, score=score, reason=reason
            )
        )
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:8]


def iter_entry_offsets(
    source: str | Path | IO[bytes], entry_tag: str, *, limit: int | None = None
) -> Iterator[tuple[int, int, int]]:
    """Yield (ordinal, start_byte, end_hint) for each top-most element whose local name matches.

    ``end_hint`` is expat's byte index at the end event: the position of the closing tag, or the
    position right after an empty-element tag. ``read_fragment`` resolves the exact end.
    """
    parser = expat.ParserCreate()
    state: dict[str, Any] = {"depth": 0, "open": None, "ordinal": 0, "out": []}
    out: list[tuple[int, int, int]] = state["out"]
    stop = False

    def start(name: str, _attrs: dict[str, str]) -> None:
        if state["open"] is None and _local(name) == entry_tag:
            state["open"] = (parser.CurrentByteIndex, state["depth"], name)
        state["depth"] += 1

    def end(name: str) -> None:
        nonlocal stop
        state["depth"] -= 1
        opened = state["open"]
        if opened is not None and state["depth"] == opened[1] and name == opened[2]:
            out.append((state["ordinal"], opened[0], parser.CurrentByteIndex))
            state["ordinal"] += 1
            state["open"] = None
            if limit is not None and state["ordinal"] >= limit:
                stop = True

    parser.StartElementHandler = start
    parser.EndElementHandler = end

    def drain() -> Iterator[tuple[int, int, int]]:
        for item in out:
            if limit is None or item[0] < limit:
                yield item
        out.clear()

    def feed() -> Iterator[tuple[int, int, int]]:
        if isinstance(source, str | Path):
            fh: IO[bytes] = open(source, "rb")  # noqa: SIM115 - closed below
            close = True
        else:
            fh, close = source, False
        try:
            while not stop:
                chunk = fh.read(CHUNK)
                if not chunk:
                    parser.Parse(b"", True)
                    break
                try:
                    parser.Parse(chunk, False)
                except expat.ExpatError:
                    if not stop:
                        raise
                yield from drain()
        finally:
            if close:
                fh.close()
        yield from drain()

    yield from feed()


def read_fragment(data: bytes | IO[bytes], start: int, end_hint: int) -> bytes:
    """Bytes of one entry from its start and expat's end hint (exact end resolved here)."""
    window = end_hint - start + 512  # room for the closing tag
    if isinstance(data, bytes):
        fragment = data[start : start + window]
    else:
        data.seek(start)
        fragment = data.read(window)
    cut = end_hint - start
    if fragment[cut : cut + 2] == b"</":
        gt = fragment.find(b">", cut)
        return fragment[: gt + 1] if gt >= 0 else fragment[:cut]
    return fragment[:cut]  # empty-element tag: the hint is already past it


def parse_fragment(fragment: bytes, namespaces: dict[str, str] | None = None) -> etree._Element:
    """Parse an entry fragment; undeclared prefixes are declared from the document's namespaces."""
    parser = etree.XMLParser(
        load_dtd=False, no_network=True, resolve_entities=False, huge_tree=True
    )
    try:
        return etree.fromstring(fragment, parser)
    except etree.XMLSyntaxError:
        if not namespaces:
            raise
        decl = " ".join(
            f'xmlns:{p}="{u}"' if p else f'xmlns="{u}"' for p, u in namespaces.items()
        ).encode()
        wrapped = b"<w " + decl + b">" + fragment + b"</w>"
        return etree.fromstring(wrapped, parser)[0]


def headword_guess(element: etree._Element) -> str | None:
    """Best-effort headword: a headword-like descendant, else a headword-like attribute, else the
    first short text in document order."""
    for node in element.iter():
        if not isinstance(node.tag, str):
            continue
        if HEADWORD_TAGS.match(etree.QName(node).localname):
            text = _all_text(node)
            if text:
                return text[:200]
    for attr in HEADWORD_ATTRS:
        value = element.get(attr)
        if value and not value.isdigit():
            return value[:200]
    for node in element.iter():
        if isinstance(node.tag, str) and node.text and node.text.strip():
            text = " ".join(node.text.split())
            if len(text) <= SHORT_TEXT:
                return text
    return None


def snippet_of(element: etree._Element, limit: int = 200) -> str:
    return _all_text(element)[:limit]


def index_entries(
    source: str | Path,
    entry_tag: str,
    *,
    namespaces: dict[str, str] | None = None,
    limit: int | None = None,
    headword_xpath: str | None = None,
) -> Iterator[EntryRef]:
    """Stream EntryRefs (offsets, headword guess, snippet) for a file on disk."""
    compiled = (
        etree.XPath(headword_xpath, namespaces=namespaces or None) if headword_xpath else None
    )
    with open(source, "rb") as fh:
        for ordinal, start, end in iter_entry_offsets(source, entry_tag, limit=limit):
            fragment = read_fragment(fh, start, end)
            try:
                element = parse_fragment(fragment, namespaces)
            except etree.XMLSyntaxError:
                yield EntryRef(
                    ordinal=ordinal, start=start, end=start + len(fragment), headword=None
                )
                continue
            headword: str | None = None
            if compiled is not None:
                result = compiled(element)
                if isinstance(result, list) and result:
                    first = result[0]
                    headword = (
                        _all_text(first) if isinstance(first, etree._Element) else str(first)
                    ) or None
                elif isinstance(result, str | float | bool):
                    headword = str(result) or None
            if headword is None:
                headword = headword_guess(element)
            yield EntryRef(
                ordinal=ordinal,
                start=start,
                end=start + len(fragment),
                headword=headword,
                snippet=snippet_of(element),
            )
