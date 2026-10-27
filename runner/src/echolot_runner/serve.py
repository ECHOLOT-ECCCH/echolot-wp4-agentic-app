"""``python -m echolot_runner.serve``: register the ``run-script/default`` deployment with the
Prefect server (``PREFECT_API_URL``) and execute its flow runs, as ECHOLOT's own serve does.
``RUNNER_MAX_RUNS`` caps how many runs execute at the same time (default 4)."""

from __future__ import annotations

import os

from echolot_runner.flow import run_script

DEPLOYMENT_NAME = "default"


def main() -> None:
    run_script.serve(name=DEPLOYMENT_NAME, limit=int(os.environ.get("RUNNER_MAX_RUNS", "4")))


if __name__ == "__main__":
    main()
