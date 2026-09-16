from __future__ import annotations

import io
import json

import pytest

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
    streamed = list(streaming.stream_transform(samples[sample], desc, plan=plan))
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
    streamed = list(streaming.stream_transform(io.BytesIO(xml), desc, plan=plan))
    assert streamed == expected["entries"]
    assert streamed[1]["senses"][1]["id"] == "e2.sense.id.2"
    assert plan.resource_scalars == {
        "langCode": "sl"
    }  # title text is not in the replica; lang attribute is


def test_stream_limit_and_source_entries(kks_mapping: dict, samples: dict) -> None:
    desc = dx.load_mapping(kks_mapping)
    plan = streaming.plan_for(desc)
    first_five = list(streaming.stream_transform(samples["kks"], desc, plan=plan, limit=5))
    assert [e["headword"] for e in first_five][:3] == ["polttaija", "polttaja", "polttajaine"]
    elements = list(streaming.stream_source_entries(samples["kks"], plan, limit=4))
    assert [o for o, _ in elements] == [0, 1, 2, 3]
    assert elements[0][1].tag == "DictionaryEntry"
    assert json.dumps(first_five[0], ensure_ascii=False)
