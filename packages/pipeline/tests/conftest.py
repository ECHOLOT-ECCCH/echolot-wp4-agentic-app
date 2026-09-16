import json
from pathlib import Path

import pytest

from elexifier_pipeline.sample import SAMPLE_DIR

OXFORD_XML = SAMPLE_DIR / "oxford.xml"
KKS_XML = SAMPLE_DIR / "kks-sample.xml"
KKS_MAPPING = SAMPLE_DIR / "kks-mapping.json"
OXFORD_MAPPING = SAMPLE_DIR / "oxford-mapping.json"


@pytest.fixture(scope="session")
def kks_mapping() -> dict:
    return json.loads(KKS_MAPPING.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def oxford_mapping() -> dict:
    return json.loads(OXFORD_MAPPING.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def samples() -> dict[str, Path]:
    return {"oxford": OXFORD_XML, "kks": KKS_XML}
