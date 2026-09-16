from __future__ import annotations

import json
from pathlib import Path

from lxml import etree

from elexifier_pipeline.dmlex.schema import DMLEX_NS
from elexifier_pipeline.xml import convert
from elexifier_pipeline.xml import dmlexifier as dx


def test_full_conversion_writes_validates_and_reports(
    oxford_mapping: dict, samples: dict, tmp_path: Path
) -> None:
    progress: list[tuple[str, int, int | None]] = []
    result = convert.convert_document(
        samples["oxford"],
        dx.load_mapping(oxford_mapping),
        tmp_path,
        progress=lambda phase, done, total: progress.append((phase, done, total)),
        total_hint=102,
    )
    assert result.source_entries == 102 and result.output_entries == 102
    assert result.xml_path.exists() and result.json_path.exists()
    assert not (tmp_path / "entries.jsonl").exists()  # spool removed
    assert [p[0] for p in progress][:1] == ["converting"] and progress[-1][0] == "validating"
    assert {"convert_s", "write_s", "validate_s"} <= set(result.timings)

    root = etree.parse(str(result.xml_path)).getroot()
    assert root.tag == f"{{{DMLEX_NS}}}lexicographicResource"
    assert len(root.findall(f"{{{DMLEX_NS}}}entry")) == 102
    data = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert len(data["entries"]) == 102 and data["langCode"] == "en"
    assert data["entries"][1]["headword"] == "b"

    # the owner's Oxford mapping validates apart from the validator's known limitation
    assert result.validation.valid, result.validation.errors[:3]
    assert result.issues == {} or all(
        i.source == "consistency" for issues in result.issues.values() for i in issues
    )
    assert result.resource_scalars["uri"]


def test_homograph_numbering_and_issue_attribution(tmp_path: Path) -> None:
    xml = (
        b"<d>"
        b"<e><w>bank</w><p>n</p></e>"
        b"<e><w>bank</w><p>n</p></e>"
        b"<e><w>bank</w><p>v</p></e>"
        b"<e><w>row</w><p>n</p><n>2</n></e>"
        b"<e><w>row</w><p>n</p></e>"
        b"<e><w></w><p>n</p></e>"
        b"</d>"
    )
    source = tmp_path / "d.xml"
    source.write_bytes(xml)
    ns = "{http://elex.is/wp1/teiLex0Mapper/meta}"
    mapping = {
        "inSelector": "/d",
        "outElement": "lexicographicResource",
        "textVals": [
            {"attribute": f"{ns}constant", "constant": "urn:t", "outElement": "uri"},
            {"attribute": f"{ns}constant", "constant": "en", "outElement": "langCode"},
        ],
        "children": [
            {
                "inSelector": "e",
                "outElement": "entry",
                "textVals": [
                    {"inSelector": "w", "attribute": f"{ns}innerText", "outElement": "headword"},
                    {
                        "inSelector": "n",
                        "attribute": f"{ns}innerText",
                        "outElement": "homographNumber",
                    },
                ],
                "children": [
                    {
                        "inSelector": "p",
                        "outElement": "partOfSpeech",
                        "textVals": [{"attribute": f"{ns}innerText", "outElement": "tag"}],
                    }
                ],
            }
        ],
    }
    out = tmp_path / "out"
    out.mkdir()
    result = convert.convert_document(source, dx.load_mapping(mapping), out)
    data = json.loads(result.json_path.read_text(encoding="utf-8"))
    numbers = [(e["headword"], e.get("homographNumber")) for e in data["entries"]]
    # bank/n twice -> 1, 2; bank/v alone -> none; row: numbered in the source -> untouched
    assert numbers[:3] == [("bank", 1), ("bank", 2), ("bank", None)]
    assert numbers[3:5] == [("row", "2"), ("row", None)]
    assert result.homographs_numbered == 2
    # the empty headword is an error attributed to output entry 5 (source ordinal 5)
    assert 5 in result.issues and result.ordinals[5] == 5
    codes = {i.code for i in result.issues[5]}
    assert "empty-headword" in codes
    assert result.issue_codes.get("empty-headword") == 1
    assert any(k.startswith("schema") for k in result.issue_codes)
    assert result.error_count >= 2 and result.warning_count == 0

    # without numbering the duplicates surface as schema errors
    out2 = tmp_path / "out2"
    out2.mkdir()
    plain = convert.convert_document(
        source, dx.load_mapping(mapping), out2, number_homographs=False
    )
    assert plain.homographs_numbered == 0
    assert any("duplicated value" in i.message for issues in plain.issues.values() for i in issues)
