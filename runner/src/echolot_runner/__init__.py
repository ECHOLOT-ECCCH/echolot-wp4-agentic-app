"""ECHOLOT workbench runner: runs a workspace script over records (docs/PLAN.md 7.1-7.4).

Scripts may catch ``ServiceError`` (``from echolot_runner import ServiceError``).
"""

from echolot_runner.services import ServiceError

__all__ = ["ServiceError"]
__version__ = "0.1.0"
