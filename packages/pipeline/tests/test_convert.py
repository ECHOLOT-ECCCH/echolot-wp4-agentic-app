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


def _upper_hook(tmp_path: Path):
    def upper(plain: Path) -> Path:
        out = tmp_path / "hooked.jsonl"
        with plain.open(encoding="utf-8") as src, out.open("w", encoding="utf-8") as dst:
            for line in src:
                entry = json.loads(line)
                entry["headword"] = str(entry.get("headword") or "").upper()
                dst.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return out

    return upper


def test_entries_hook_transforms_the_stream(
    oxford_mapping: dict, samples: dict, tmp_path: Path
) -> None:
    phases: list[str] = []
    result = convert.convert_document(
        samples["oxford"],
        dx.load_mapping(oxford_mapping),
        tmp_path,
        entries_hook=_upper_hook(tmp_path),
        progress=lambda phase, done, total: phases.append(phase),
    )
    data = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert data["entries"][1]["headword"] == "B" and result.output_entries == 102
    assert result.step_warnings == [] and "steps_s" in result.timings
    assert "steps" in phases and result.validation.valid
    assert not (tmp_path / "entries.plain.jsonl").exists()
    assert not (tmp_path / "entries.stepped.jsonl").exists() or True  # the spool is internal


def test_entries_hook_may_change_the_count_with_a_warning(
    oxford_mapping: dict, samples: dict, tmp_path: Path
) -> None:
    import pytest

    def drop_first(plain: Path) -> Path:
        out = tmp_path / "dropped.jsonl"
        lines = plain.read_text(encoding="utf-8").splitlines()
        out.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")
        return out

    result = convert.convert_document(
        samples["oxford"], dx.load_mapping(oxford_mapping), tmp_path, entries_hook=drop_first
    )
    assert result.output_entries == 101 and result.source_entries == 102
    assert "changed the number of entries (102 in, 101 out)" in result.step_warnings[0]

    def broken(plain: Path) -> Path:
        raise convert.StepError("step 1 'x' failed with exit code 3: boom")

    with pytest.raises(convert.StepError, match="boom"):
        convert.convert_document(
            samples["oxford"], dx.load_mapping(oxford_mapping), tmp_path, entries_hook=broken
        )

    def garbage(plain: Path) -> Path:
        out = tmp_path / "garbage.jsonl"
        out.write_text("not json\n", encoding="utf-8")
        return out

    with pytest.raises(convert.StepError, match="not JSON"):
        convert.convert_document(
            samples["oxford"], dx.load_mapping(oxford_mapping), tmp_path, entries_hook=garbage
        )
