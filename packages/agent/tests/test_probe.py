from pydantic_ai.models.test import TestModel

from elexifier_agent.probe import _safe_error, probe_model


async def test_probe_with_test_model() -> None:
    result = await probe_model(TestModel(custom_output_text="OK"), "test")
    assert result.ok is True
    assert result.reply == "OK"
    assert result.latency_ms >= 0


def test_errors_never_leak_keys() -> None:
    assert (
        _safe_error(RuntimeError("401 for key sk-or-v1-secret"))
        == "RuntimeError: 401 for key sk-***"
    )
