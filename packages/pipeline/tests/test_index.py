from __future__ import annotations

import io

from elexifier_pipeline.xml import index as ix


def test_profile_finds_entries(samples: dict) -> None:
    oxford = ix.profile_xml(samples["oxford"])
    assert oxford.root_tag == "oxford"
    assert oxford.entry_candidates[0].tag == "se"
    assert oxford.entry_candidates[0].xpath == "/oxford/se"
    assert oxford.total_elements > 2000
    se = next(s for s in oxford.element_stats if s.tag == "se")
    assert se.count == 102 and se.depth == 1 and se.parents == {"oxford": 102}
    hw = next(s for s in oxford.element_stats if s.tag == "hw")
    assert hw.has_text and hw.sample_values

    kks = ix.profile_xml(samples["kks"])
    assert kks.root_tag == "Dictionary"
    assert kks.entry_candidates[0].tag == "DictionaryEntry"
    assert kks.doctype is None  # the fixture excerpt carries no DOCTYPE
    assert kks.namespaces.get("xlink") == "http://www.w3.org/TR/xlink"
    pos = next(s for s in kks.element_stats if s.tag == "PartOfSpeech")
    assert set(pos.attributes) == {"display", "freeValue", "value"}


def test_index_entries_offsets_and_headwords(samples: dict) -> None:
    refs = list(ix.index_entries(samples["oxford"], "se"))
    assert len(refs) == 102
    assert [r.headword for r in refs[:3]] == ["azyme", "b", "BA"]
    data = samples["oxford"].read_bytes()
    first = data[refs[0].start : refs[0].end]  # EntryRef offsets are exact
    assert first.startswith(b"<se") and first.endswith(b"</se>")
    assert refs[1].start > refs[0].end
    limited = list(ix.index_entries(samples["oxford"], "se", limit=2))
    assert [r.ordinal for r in limited] == [0, 1]

    profile = ix.profile_xml(samples["kks"])
    kks = list(
        ix.index_entries(samples["kks"], "DictionaryEntry", namespaces=profile.namespaces, limit=5)
    )
    assert [r.headword for r in kks[:3]] == ["polttaija", "polttaja", "polttajaine"]
    assert kks[0].snippet.startswith("polttaija")


def test_headword_xpath_override(samples: dict) -> None:
    namespaces = ix.profile_xml(samples["kks"]).namespaces
    refs = list(
        ix.index_entries(
            samples["kks"],
            "DictionaryEntry",
            namespaces=namespaces,
            headword_xpath="@identifier",
            limit=2,
        )
    )
    assert [r.headword for r in refs] == ["polttaija", "polttaja"]


def test_empty_element_and_nested_entries() -> None:
    xml = (
        b'<d><e id="1"/><e id="2"><w>two</w><e id="inner"><w>x</w></e></e>'
        b'<e id="3"><w>three</w></e></d>'
    )
    offsets = list(ix.iter_entry_offsets(io.BytesIO(xml), "e"))
    assert len(offsets) == 3  # nested <e> inside an entry is not a separate entry
    frags = [ix.read_fragment(xml, s, e) for _, s, e in offsets]
    assert frags[0] == b'<e id="1"/>'
    assert frags[1].startswith(b'<e id="2">') and frags[1].endswith(b"</e>")
    assert ix.headword_guess(ix.parse_fragment(frags[1])) == "two"
    assert ix.headword_guess(ix.parse_fragment(frags[0])) is None


def test_namespaced_fragment_parses() -> None:
    xml = b"""<r xmlns:t="urn:t"><t:e><t:orth>word</t:orth></t:e></r>"""
    offsets = list(ix.iter_entry_offsets(io.BytesIO(xml), "e"))
    frag = ix.read_fragment(xml, offsets[0][1], offsets[0][2])
    element = ix.parse_fragment(frag, {"t": "urn:t"})
    assert ix.headword_guess(element) == "word"
