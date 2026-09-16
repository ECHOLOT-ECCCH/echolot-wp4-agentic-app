"""Background jobs (Procrastinate tasks) that need the database, storage and the pipeline.

The worker imports this package through ``ELX task_modules`` (see settings); the API imports it
so that tasks are registered on the shared app object before deferring them.
"""

from elexifier_api.jobs import conversions, documents

__all__ = ["conversions", "documents"]
