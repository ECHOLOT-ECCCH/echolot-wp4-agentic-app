"""Small sample dictionaries used by tests and smoke runs."""

from pathlib import Path

SAMPLE_DIR = Path(__file__).parent
SAMPLE_XML = SAMPLE_DIR / "sample-dictionary.xml"  # three-entry toy file used by the browser smoke
SAMPLE_PDF = SAMPLE_DIR / "sample-dictionary.pdf"
OXFORD_XML = SAMPLE_DIR / "oxford.xml"  # 102 entries, owner-provided legacy export
OXFORD_MAPPING = SAMPLE_DIR / "oxford-mapping.json"
KKS_XML = SAMPLE_DIR / "kks-sample.xml"  # 300-entry excerpt of the Karelian dictionary (CC BY 4.0)
KKS_MAPPING = SAMPLE_DIR / "kks-mapping.json"
COLUMNS_PDF = SAMPLE_DIR / "columns-dictionary.pdf"  # 3 two-column text pages + 1 without text
