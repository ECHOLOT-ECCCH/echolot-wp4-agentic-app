from __future__ import annotations

import pytest
from lxml import etree

from elexifier_pipeline.xml import dmlexifier as dx

NS = "http://elex.is/wp1/teiLex0Mapper/meta"


def test_oxford_whole_document(oxford_mapping: dict, samples: dict) -> None:
    out = dx.TransformEx(mappingJson=oxford_mapping, fnOrFileList=[samples["oxford"]])
    assert len(out) == 1
    resource = out[0]
    assert resource["title"] == "Oxford English-Slovene sample"
    entries = resource["entries"]
    assert len(entries) == 102
    first, second = entries[0], entries[1]
    assert first["headword"] == "azyme"  # <p>y</p> inline element is included by innerTextRec
    assert first["id"] == "entry.id.1"
    assert first["partsOfSpeech"] == [{"tag": "noun"}]
    assert second["headword"] == "b"
    assert len(second["senses"]) == 10
    # hierarchical generated ids: unique document-wide and readable
    assert second["senses"][0]["id"] == "entry.id.2.sense.id.1"
    assert second["senses"][1]["id"] == "entry.id.2.sense.id.2"
    # regex strips whitespace around the definition text; labels are extracted separately
    sense = second["senses"][1]
    assert sense["definitions"][0]["text"] == "prav dobro, štirica"
    assert sense["labels"] == [{"tag": "šol."}]
    assert sense["examples"][0]["text"] == "I got a B in biology"


def test_kks_sample_with_owner_mapping(kks_mapping: dict, samples: dict) -> None:
    out = dx.TransformEx(mappingJson=kks_mapping, fnOrFileList=[samples["kks"]])
    resource = out[0]
    entries = resource["entrys"]  # the owner's mapping relies on the default plural
    assert len(entries) == 300
    assert entries[0]["headword"] == "polttaija"
    assert entries[0]["partOfSpeechs"] == [{"tag": "verb"}]
    assert entries[2]["headword"] == "polttajaine"
    assert entries[2]["senses"][0]["definitions"][0]["text"] == "polttiainen (Culicoides)."
    assert resource["language"] == "language"  # constant text values on the resource


def test_extract_entries_and_text_values(kks_mapping: dict, samples: dict) -> None:
    mapping = dx.load_mapping(kks_mapping)
    tree = dx.parse_document(samples["kks"])
    serialized = dx.ExtractEntries(tree, mapping, "entry")
    assert len(serialized) == 300
    assert serialized[0].lstrip().startswith("<DictionaryEntry")
    values: dict[tuple[str, str], set[str] | None] = {("partOfSpeech", "tag"): None}
    dx.ExtractTextValues(tree, mapping, values)
    tags = values[("partOfSpeech", "tag")]
    assert tags is not None
    assert {"verb", "noun"} <= tags


def test_regex_group_xlat_constant_and_attribute_sources() -> None:
    xml = b"""<d><e n="7"><w>Head (obs.)</w><g>n</g></e></d>"""
    mapping = {
        "inSelector": "/d",
        "outElement": "lexicographicResource",
        "children": [
            {
                "inSelector": "e",
                "outElement": "entry",
                "jsonPlural": "entries",
                "textVals": [
                    {
                        "inSelector": "w",
                        "attribute": f"{{{NS}}}innerText",
                        "outElement": "headword",
                        "regex": r"^(?P<hw>[^(]+?)\s*\(",
                        "regexGroup": "hw",
                    },
                    {"attribute": "n", "outElement": "homographNumber"},
                    {"attribute": f"{{{NS}}}constant", "constant": "x", "outElement": "id"},
                ],
                "children": [
                    {
                        "inSelector": "g",
                        "outElement": "partOfSpeech",
                        "jsonPlural": "partsOfSpeech",
                        "textVals": [
                            {
                                "attribute": f"{{{NS}}}innerText",
                                "outElement": "tag",
                                "xlat": {"n": "noun"},
                            }
                        ],
                    }
                ],
            }
        ],
    }
    out = dx.TransformEx(mappingJson=mapping, xmlStringList=[xml])
    entry = out[0]["entries"][0]
    assert entry == {
        "headword": "Head",
        "homographNumber": "7",
        "id": "x",
        "partsOfSpeech": [{"tag": "noun"}],
    }


def test_markers_with_selector_and_regex() -> None:
    xml = b"<d><e><def>to <hw>run</hw> quickly, run again</def></e></d>"
    mapping = {
        "inSelector": "/d",
        "outElement": "lexicographicResource",
        "children": [
            {
                "inSelector": "e",
                "outElement": "entry",
                "jsonPlural": "entries",
                "children": [
                    {
                        "inSelector": "def",
                        "outElement": "definition",
                        "textVals": [
                            {
                                "attribute": f"{{{NS}}}innerTextRec",
                                "outElement": "text",
                                "markers": [
                                    {"inSelector": "hw", "outElement": "headwordMarker"},
                                    {"outElement": "collocateMarker", "regex": "again"},
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    definition = dx.TransformEx(mappingJson=mapping, xmlStringList=[xml])[0]["entries"][0][
        "definitions"
    ][0]
    assert definition["text"] == "to run quickly, run again"
    assert definition["headwordMarkers"] == [{"startIndex": 3, "endIndex": 6}]
    assert definition["collocateMarkers"] == [{"startIndex": 20, "endIndex": 25}]


def test_mapping_errors() -> None:
    with pytest.raises(dx.MappingError):
        dx.TEltDesc({"outElement": "entry"})  # missing inSelector
    with pytest.raises(dx.MappingError):
        dx.TEltDesc(
            {
                "inSelector": "e",
                "outElement": "entry",
                "textVals": [{"attribute": "x", "outElement": "y", "regex": "("}],
            }
        )
    with pytest.raises(dx.MappingError):
        dx.TransformEx(xmlStringList=[b"<a/>"])


def test_parse_document_is_safe() -> None:
    evil = b"""<!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]><d>&x;</d>"""
    tree = dx.parse_document(evil)
    assert (tree.getroot().text or "") == ""
    assert isinstance(tree, etree._ElementTree)
