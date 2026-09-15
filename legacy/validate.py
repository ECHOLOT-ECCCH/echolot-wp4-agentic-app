#!/usr/bin/env python3
"""Validate a DMLex XML document against the DMLex XSD (1.1).

Usage:
    python tools/validate.py --xml projects/oxford/output.dmlex.xml
    python tools/validate.py --xml out.xml --xsd schema/dmlex.xsd --max-errors 50

Exit codes:
    0  valid
    1  invalid (validation errors printed)
    2  usage / environment error (bad paths, parse failure, missing dependency)

Designed to drive the convert->validate->fix loop: errors are printed one per line
with their location (path/line) so they can be fed straight back into a fix pass.
"""
import argparse
import sys
from pathlib import Path

# Repo root is the parent of this file's directory (tools/).
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_XSD = REPO_ROOT / "schema" / "dmlex.xsd"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate DMLex XML against the DMLex XSD.")
    parser.add_argument("--xml", required=True, help="Path to the DMLex XML document to validate.")
    parser.add_argument(
        "--xsd",
        default=str(DEFAULT_XSD),
        help=f"Path to the DMLex XSD (default: {DEFAULT_XSD}).",
    )
    parser.add_argument(
        "--max-errors",
        type=int,
        default=20,
        help="Maximum number of validation errors to print (default: 20).",
    )
    args = parser.parse_args()

    try:
        import xmlschema
    except ImportError:
        print("ERROR: the 'xmlschema' package is not installed. Run: pip install -r requirements.txt", file=sys.stderr)
        return 2

    xml_path = Path(args.xml)
    xsd_path = Path(args.xsd)
    for label, p in (("XML", xml_path), ("XSD", xsd_path)):
        if not p.is_file():
            print(f"ERROR: {label} file not found: {p}", file=sys.stderr)
            return 2

    # XSD 1.1 is required: the DMLex schema relies on <xs:assert>.
    try:
        schema = xmlschema.XMLSchema11(str(xsd_path))
    except Exception as exc:  # noqa: BLE001 - surface any schema-loading problem
        print(f"ERROR: failed to load schema {xsd_path}: {exc}", file=sys.stderr)
        return 2

    try:
        resource = xmlschema.XMLResource(str(xml_path))
    except Exception as exc:  # noqa: BLE001 - malformed XML, encoding, etc.
        print(f"ERROR: failed to parse XML {xml_path}: {exc}", file=sys.stderr)
        return 2

    errors = list(schema.iter_errors(resource))
    if not errors:
        print(f"VALID: {xml_path} conforms to {xsd_path}")
        return 0

    print(f"INVALID: {xml_path} has {len(errors)} validation error(s) against {xsd_path}")
    for i, err in enumerate(errors[: args.max_errors], start=1):
        path = getattr(err, "path", None) or "<unknown path>"
        # sqname/reason give the rule; path/line give the location.
        reason = getattr(err, "reason", None) or str(err).splitlines()[0]
        print(f"\n[{i}] at {path}")
        print(f"    {reason}")
    remaining = len(errors) - args.max_errors
    if remaining > 0:
        print(f"\n... and {remaining} more error(s); raise --max-errors to see them.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
