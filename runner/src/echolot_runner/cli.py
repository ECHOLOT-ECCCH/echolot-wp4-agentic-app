"""``python -m echolot_runner.cli params.json``: one run, as the Prefect flow and the app's
LocalExecutor start it. Prints the summary as JSON; exits 1 with the reason on stderr when the
run fails."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from echolot_runner.harness import RunFailed, run
from echolot_runner.params import ParamsError


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m echolot_runner.cli params.json", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        params = json.loads(Path(args[0]).read_text(encoding="utf-8"))
        summary = run(params)
    except (RunFailed, ParamsError) as exc:
        print(f"run failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
