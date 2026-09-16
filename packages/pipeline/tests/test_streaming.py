from __future__ import annotations

import io
import json

import pytest
from lxml import etree

from elexifier_pipeline.xml import dmlexifier as dx
from elexifier_pipeline.xml import streaming

NS = "http://elex.is/wp1/teiLex0Mapper/meta"


def _whole(mapping: dict, path) -> list[dict]:
    resource = dx.TransformEx(mappingJson=mapping, fnOrFileList=[path])[0]
    plural = next(k for k in resource if k in ("entries", "entrys"))
    return resource[plural]


@pytest.mark.parametrize(
    "sample,mapping_fixture", [("oxford", "oxford_mapping"), ("kks", "kks_mapping")]
)
def test_stream_equals_whole_document(
    sample: str, mapping_fixture: str, samples: dict, request
) -> None:
    mapping = request.getfixturevalue(mapping_fixture)
    expected = _whole(mapping, samples[sample])
    desc = dx.load_mapping(mapping)
    plan = streaming.plan_for(desc)
    streamed = list(streaming.stream_transform(samples[sample], desc, plan=plan, normalized=False))
    assert len(streamed) == len(expected)
    assert streamed == expected  # same content and the same generated ids
    # resource-level constants are recovered from the ancestor replica
    assert plan.resource_scalars.get("title")


def test_plan_detects_entry_element(kks_mapping: dict, oxford_mapping: dict) -> None:
    kks = streaming.plan_for(dx.load_mapping(kks_mapping))
    assert (kks.entry_local_name, kks.entry_namespace) == ("DictionaryEntry", None)
    oxford = streaming.plan_for(dx.load_mapping(oxford_mapping))
    assert oxford.entry_local_name == "se"
    assert streaming.simple_tag("/TEI/text/body/entry") == (None, "entry")
    assert streaming.simple_tag(".//tei:entry") == ("tei", "entry")
    assert streaming.simple_tag("se[hw]") == (None, "se")
    assert streaming.simple_tag("se[not(@hidden='y')][hg/ps]") == (None, "se")
    assert streaming.simple_tag("body/entry[form/orth='a/b']") == (None, "entry")
    for bad in ("se | pv", "*", "se/..", "node()", "se[hw", ".//se/@id"):
        assert streaming.simple_tag(bad) is None, bad
    with pytest.raises(dx.MappingError):
        streaming.plan_for(
            dx.load_mapping(
                {
                    "inSelector": "/d",
                    "outElement": "lexicographicResource",
                    "children": [{"inSelector": "e | f", "outElement": "entry"}],
                }
            )
        )


def test_entry_predicate_filters_while_streaming() -> None:
    xml = b"""<d><e hidden="y"><w>skip</w></e><e><w>keep</w></e><e hidden="n"><w>also</w></e></d>"""
    desc = dx.load_mapping(
        {
            "inSelector": "/d",
            "outElement": "lexicographicResource",
            "children": [
                {
                    "inSelector": "e[not(@hidden='y')]",
                    "outElement": "entry",
                    "textVals": [
                        {
                            "inSelector": "w",
                            "attribute": dx.ATTR_INNER_TEXT,
                            "outElement": "headword",
                        }
                    ],
                }
            ],
        }
    )
    entries = list(streaming.stream_transform(io.BytesIO(xml), desc))
    assert [e["headword"] for e in entries] == ["keep", "also"]


def test_nested_ancestors_and_namespaces() -> None:
    xml = b"""<?xml version="1.0"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:lang="sl">
  <teiHeader><title>Test</title></teiHeader>
  <text><body>
    <entry xml:id="e1"><form><orth>ena</orth></form><sense><def>one</def></sense></entry>
    <entry xml:id="e2"><form><orth>dva</orth></form>
      <sense><def>two</def></sense><sense><def>second</def></sense></entry>
  </body></text>
</TEI>"""
    mapping = {
        "inSelector": "/tei:TEI",
        "namespaces": {"tei": "http://www.tei-c.org/ns/1.0"},
        "outElement": "lexicographicResource",
        "textVals": [
            {"attribute": "{http://www.w3.org/XML/1998/namespace}lang", "outElement": "langCode"},
            {
                "inSelector": "tei:teiHeader/tei:title",
                "namespaces": {"tei": "http://www.tei-c.org/ns/1.0"},
                "attribute": f"{{{NS}}}innerText",
                "outElement": "title",
            },
        ],
        "children": [
            {
                "inSelector": "tei:text/tei:body/tei:entry",
                "namespaces": {"tei": "http://www.tei-c.org/ns/1.0"},
                "outElement": "entry",
                "jsonPlural": "entries",
                "textVals": [
                    {
                        "inSelector": "tei:form/tei:orth",
                        "namespaces": {"tei": "http://www.tei-c.org/ns/1.0"},
                        "attribute": f"{{{NS}}}innerText",
                        "outElement": "headword",
                    },
                    {"attribute": "{http://www.w3.org/XML/1998/namespace}id", "outElement": "id"},
                ],
                "children": [
                    {
                        "inSelector": "tei:sense",
                        "namespaces": {"tei": "http://www.tei-c.org/ns/1.0"},
                        "outElement": "sense",
                        "textVals": [{"attribute": f"{{{NS}}}autogenerated", "outElement": "id"}],
                        "children": [
                            {
                                "inSelector": "tei:def",
                                "namespaces": {"tei": "http://www.tei-c.org/ns/1.0"},
                                "outElement": "definition",
                                "textVals": [
                                    {"attribute": f"{{{NS}}}innerText", "outElement": "text"}
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    desc = dx.load_mapping(mapping)
    expected = dx.TransformEx(mappingJson=mapping, xmlStringList=[xml])[0]
    plan = streaming.plan_for(desc)
    assert (plan.entry_local_name, plan.entry_namespace) == ("entry", "http://www.tei-c.org/ns/1.0")
    streamed = list(streaming.stream_transform(io.BytesIO(xml), desc, plan=plan, normalized=False))
    assert streamed == expected["entries"]
    assert streamed[1]["senses"][1]["id"] == "e2.sense.id.2"
    assert plan.resource_scalars == {
        "langCode": "sl"
    }  # title text is not in the replica; lang attribute is


def test_stream_limit_and_source_entries(kks_mapping: dict, samples: dict) -> None:
    desc = dx.load_mapping(kks_mapping)
    plan = streaming.plan_for(desc)
    first_five = list(
        streaming.stream_transform(samples["kks"], desc, plan=plan, limit=5, normalized=False)
    )
    assert [e["headword"] for e in first_five][:3] == ["polttaija", "polttaja", "polttajaine"]
    elements = list(streaming.stream_source_entries(samples["kks"], plan, limit=4))
    assert [o for o, _ in elements] == [0, 1, 2, 3]
    assert elements[0][1].tag == "DictionaryEntry"
    assert json.dumps(first_five[0], ensure_ascii=False)


def test_secondary_entry_descriptors_and_fragment_conversion() -> None:
    xml = (
        b'<?xml version="1.0"?>\n<d xml:lang="en"><meta><title>T</title></meta>\n'
        b'<e id="1"><w>one</w><pv><lp>one up</lp></pv></e>\n'
        b'<e id="2"><w>two</w></e>\n</d>'
    )
    text = {"attribute": dx.ATTR_INNER_TEXT, "outElement": "headword"}
    mapping = {
        "inSelector": "/d",
        "outElement": "lexicographicResource",
        "textVals": [
            {"attribute": "{http://www.w3.org/XML/1998/namespace}lang", "outElement": "langCode"}
        ],
        "children": [
            {
                "inSelector": "e",
                "outElement": "entry",
                "textVals": [
                    dict(text, inSelector="w"),
                    {"attribute": dx.ATTR_AUTO, "outElement": "id"},
                ],
                "children": [
                    {
                        "inSelector": "pv",
                        "outElement": "label",
                        "jsonPlural": "labelz",
                        "textVals": [
                            {"attribute": dx.ATTR_CONSTANT, "constant": "pv", "outElement": "tag"}
                        ],
                    }
                ],
            },
            {
                "inSelector": "e/pv",
                "outElement": "entry",
                "textVals": [dict(text, inSelector="lp")],
            },
            {"inSelector": ".//elsewhere", "outElement": "entry", "textVals": [text]},
        ],
    }
    desc = dx.load_mapping(mapping)
    plan = streaming.plan_for(desc)
    assert len(plan.entry_descs) == 3 and plan.warnings == [
        "entries selected by './/elsewhere' are only found inside <e> elements while streaming"
    ]
    streamed = list(streaming.stream_transform(io.BytesIO(xml), desc, plan=plan))
    assert [e["headword"] for e in streamed] == ["one", "one up", "two"]
    assert streamed[0]["labels"] == [{"tag": "pv"}]  # normalized to the DMLex JSON name
    assert streamed[0]["id"] == "entry.id.1" and streamed[2]["id"] == "entry.id.2"

    # the same entries converted one at a time from their source bytes, as samples do
    first_start = xml.index(b"<e ")
    chain = streaming.head_ancestors(xml[:first_start], plan)
    assert [a.tag for a in chain] == ["d"]  # <meta> is closed before the first entry
    assert chain[0].attrib == {"{http://www.w3.org/XML/1998/namespace}lang": "en"}
    transformer = dx.new_transformer()
    assert streaming.resource_scalars(chain, plan, transformer) == {"langCode": "en"}
    second = xml[xml.index(b'<e id="2">') : xml.index(b"</e>\n</d>") + 4]
    streaming.seed_entry_counter(transformer, 2)
    converted = streaming.convert_fragment(chain, second, plan, transformer)
    assert converted == [{"headword": "two", "id": "entry.id.2"}]
    first = xml[first_start : xml.index(b"</e>") + 4]
    streaming.seed_entry_counter(transformer, 1)
    assert [e["headword"] for e in streaming.convert_fragment(chain, first, plan, transformer)] == [
        "one",
        "one up",
    ]


def test_fragment_conversion_keeps_default_namespace() -> None:
    xml = (
        b'<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader/><text><body>'
        b"<entry><form><orth>hi</orth></form></entry></body></text></TEI>"
    )
    mapping = {
        "inSelector": "/t:TEI",
        "outElement": "lexicographicResource",
        "namespaces": {"t": "http://www.tei-c.org/ns/1.0"},
        "children": [
            {
                "inSelector": "t:text/t:body/t:entry",
                "outElement": "entry",
                "namespaces": {"t": "http://www.tei-c.org/ns/1.0"},
                "textVals": [
                    {
                        "inSelector": "t:form/t:orth",
                        "namespaces": {"t": "http://www.tei-c.org/ns/1.0"},
                        "attribute": dx.ATTR_INNER_TEXT,
                        "outElement": "headword",
                    }
                ],
            }
        ],
    }
    plan = streaming.plan_for(dx.load_mapping(mapping))
    start = xml.index(b"<entry>")
    chain = streaming.head_ancestors(xml[:start], plan)
    assert [etree.QName(a.tag).localname for a in chain] == ["TEI", "text", "body"]
    fragment = xml[start : xml.index(b"</entry>") + 8]
    wrapped = streaming.wrap_fragment(chain, fragment)
    assert wrapped.startswith(b'<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body><entry>')
    converted = streaming.convert_fragment(chain, fragment, plan, dx.new_transformer())
    assert converted == [{"headword": "hi"}]
