from elexifier_core import jobs
from elexifier_core.tasks import ping


def test_ping_task_is_registered() -> None:
    assert "core.ping" in jobs.app.tasks
    assert ping.ping.name == "core.ping"
