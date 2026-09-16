"""The runner's execution logic against a fake docker client: the archive it sends, the
container options, log truncation, the timeout path and the outputs it uploads."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from elexifier_core.storage import LocalStorage
from elexifier_sandbox.runner.execute import (
    LOG_LIMIT,
    ExecutionError,
    ExecutionSettings,
    RunInput,
    RunLimits,
    RunRequest,
    build_tar,
    container_options,
    execute,
    mount_path_ok,
)

from .conftest import FakeContainer, FakeDocker


def _members(archive: bytes) -> dict[str, tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        return {m.name: m for m in tar.getmembers()}


async def test_execute_stages_inputs_runs_and_uploads_outputs(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "storage")
    await storage.put("projects/p1/source/f1/source.xml", b"<a/>")
    docker = FakeDocker(
        FakeContainer(
            options={},
            stdout=b"hello\n",
            stderr=b"",
            outputs={"result.json": b"{}", "sub/x.txt": b"x"},
        )
    )
    request = RunRequest(
        run_id="r1",
        project_id="p1",
        code="print('hello')",
        inputs=[RunInput(s3_key="projects/p1/source/f1/source.xml", mount_path="in/source.xml")],
        limits=RunLimits(timeout_s=10, cpus=1.5, memory_mb=512, pids=64),
    )
    response = await execute(
        request, storage=storage, client=docker, settings=ExecutionSettings(image="img:test")
    )
    assert response.exit_code == 0 and response.stdout == "hello\n" and not response.killed
    assert [o.name for o in response.outputs] == ["result.json", "sub/x.txt"]
    assert response.outputs[0].s3_key == "projects/p1/artifacts/r1/result.json"
    assert response.outputs[0].size == 2
    assert b"".join([c async for c in storage.open("projects/p1/artifacts/r1/sub/x.txt")]) == b"x"

    container = docker.containers.created[0]
    assert container.started and container.removed == {"v": True, "force": True}
    assert container.wait_kwargs == {"timeout": 10} and container.put_path == "/work"
    options = container.options
    assert options["image"] == "img:test" and options["command"] == ["python", "/work/main.py"]
    assert options["network_mode"] == "none" and options["read_only"] is True
    assert options["user"] == "1000:1000" and options["cap_drop"] == ["ALL"]
    assert options["security_opt"] == ["no-new-privileges"] and options["pids_limit"] == 64
    assert options["mem_limit"] == "512m" and options["memswap_limit"] == "512m"
    assert options["nano_cpus"] == 1_500_000_000 and "runtime" not in options
    assert "/tmp" in options["tmpfs"]
    (mount,) = options["mounts"]
    assert mount["Target"] == "/work" and mount["Type"] == "volume"
    assert mount.get("Source") is None  # anonymous

    members = _members(container.archive or b"")
    assert set(members) == {"main.py", "in", "in/source.xml", "out"}
    assert members["main.py"].uid == 0 and members["main.py"].mode == 0o444
    assert members["in"].isdir() and members["in"].mode == 0o555 and members["in"].uid == 0
    assert members["in/source.xml"].mode == 0o444 and members["in/source.xml"].size == 4
    assert members["out"].isdir() and members["out"].uid == 1000 and members["out"].mode == 0o755


async def test_timeout_kills_the_container_and_logs_are_truncated(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "storage")
    docker = FakeDocker(
        FakeContainer(options={}, stdout=b"x" * (LOG_LIMIT + 10), stderr=b"e", hangs=True)
    )
    request = RunRequest(
        run_id="r2", project_id="p1", code="while True: pass", limits=RunLimits(timeout_s=1)
    )
    response = await execute(
        request,
        storage=storage,
        client=docker,
        settings=ExecutionSettings(image="img", runtime="runsc"),
    )
    assert response.killed and response.exit_code == 137
    assert response.truncated and len(response.stdout) == LOG_LIMIT
    assert response.stderr == "e\n[runner] killed after 1 s (timeout)"
    container = docker.containers.created[0]
    assert container.killed and container.options["runtime"] == "runsc"


async def test_outputs_beyond_the_cap_are_refused(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "storage")
    docker = FakeDocker(FakeContainer(options={}, outputs={"big.bin": b"0" * (2 * 1024 * 1024)}))
    request = RunRequest(run_id="r3", project_id="p1", code="", limits=RunLimits(max_output_mb=1))
    response = await execute(
        request, storage=storage, client=docker, settings=ExecutionSettings(image="img")
    )
    assert response.outputs == [] and "outputs exceed 1 MB; nothing uploaded" in response.stderr


async def test_bad_inputs_and_languages_are_refused(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "storage")
    docker = FakeDocker()
    with pytest.raises(ExecutionError, match="unsupported language"):
        await execute(
            RunRequest(run_id="r", project_id="p", code="", language="ruby"),
            storage=storage,
            client=docker,
            settings=ExecutionSettings(),
        )
    duplicate = RunRequest(
        run_id="r",
        project_id="p",
        code="",
        inputs=[RunInput(s3_key="k", mount_path="in/a"), RunInput(s3_key="k", mount_path="in/a")],
    )
    with pytest.raises(ExecutionError, match="duplicate"):
        await execute(duplicate, storage=storage, client=docker, settings=ExecutionSettings())
    assert docker.containers.created == []
    assert mount_path_ok("in/a/b.txt") and not mount_path_ok("in/../x") and not mount_path_ok("in/")
    with pytest.raises(ValueError):
        RunInput(s3_key="k", mount_path="etc/passwd")


def test_build_tar_nests_directories_once(tmp_path: Path) -> None:
    from elexifier_sandbox.runner.execute import StagedInput

    (tmp_path / "a").write_bytes(b"aa")
    (tmp_path / "b").write_bytes(b"b")
    build_tar(
        "print(1)",
        [
            StagedInput("in/x/y/a.txt", tmp_path / "a", 2),
            StagedInput("in/x/b.txt", tmp_path / "b", 1),
        ],
        tmp_path / "in.tar",
    )
    members = _members((tmp_path / "in.tar").read_bytes())
    assert list(members) == ["main.py", "in", "in/x", "in/x/y", "in/x/y/a.txt", "in/x/b.txt", "out"]
    assert all(m.uid == 0 for n, m in members.items() if n != "out")


def test_container_options_follow_the_limits() -> None:
    request = RunRequest(run_id="r", project_id="p", code="", limits=RunLimits(cpus=0.5))
    options = container_options(request, ExecutionSettings(image="i", tmp_size_mb=64))
    assert options["nano_cpus"] == 500_000_000 and options["tmpfs"] == {
        "/tmp": "rw,noexec,nosuid,size=64m"
    }
    assert options["labels"] == {"elexifier.run_id": "r", "elexifier.project_id": "p"}
