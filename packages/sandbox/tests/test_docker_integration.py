"""Real containers: run only when the daemon answers and the sandbox image exists."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from elexifier_core.sandbox_contract import RunLimits
from elexifier_core.storage import LocalStorage
from elexifier_sandbox.runner.execute import (
    ExecutionSettings,
    RunInput,
    RunRequest,
    execute,
)

IMAGE = os.environ.get("SANDBOX_IMAGE", "elexifier-sandbox:local")

pytestmark = pytest.mark.docker


@pytest.fixture(scope="module")
def docker_client() -> Any:
    docker = pytest.importorskip("docker")
    try:
        client = docker.from_env(timeout=30)
        client.ping()
        client.images.get(IMAGE)
    except Exception as exc:  # no daemon, no socket, no image
        pytest.skip(f"docker or image {IMAGE} unavailable: {type(exc).__name__}")
    return client


async def _run(
    client: Any, tmp_path: Path, code: str, *, inputs: list[RunInput] | None = None, **limits: Any
) -> tuple[Any, LocalStorage]:
    storage = LocalStorage(tmp_path / "storage")
    request = RunRequest(
        run_id="itest",
        project_id="p1",
        code=code,
        inputs=inputs or [],
        limits=RunLimits(**limits),
    )
    response = await execute(
        request, storage=storage, client=client, settings=ExecutionSettings(image=IMAGE)
    )
    return response, storage


async def test_hello_world_with_input_and_output(docker_client: Any, tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "storage")
    await storage.put("projects/p1/source/f/words.txt", b"abacus\nabandon\n")
    request = RunRequest(
        run_id="itest",
        project_id="p1",
        code=(
            "import os, pathlib\n"
            "words = pathlib.Path('in/words.txt').read_text().split()\n"
            "print(len(words), 'words')\n"
            "pathlib.Path('out/upper.txt').write_text('\\n'.join(w.upper() for w in words))\n"
            "print('writable in:', os.access('in/words.txt', os.W_OK))\n"
            "print('uid', os.getuid())\n"
        ),
        inputs=[RunInput(s3_key="projects/p1/source/f/words.txt", mount_path="in/words.txt")],
        limits=RunLimits(timeout_s=60),
    )
    response = await execute(
        request, storage=storage, client=docker_client, settings=ExecutionSettings(image=IMAGE)
    )
    assert response.exit_code == 0, response.stderr
    assert "2 words" in response.stdout and "writable in: False" in response.stdout
    assert "uid 1000" in response.stdout
    assert [o.name for o in response.outputs] == ["upper.txt"]
    data = b"".join([c async for c in storage.open(response.outputs[0].s3_key)])
    assert data == b"ABACUS\nABANDON"


async def test_network_is_unreachable(docker_client: Any, tmp_path: Path) -> None:
    code = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 80), timeout=3)\n"
        "    print('connected')\n"
        "except OSError as exc:\n"
        "    print('blocked:', type(exc).__name__)\n"
        "    raise SystemExit(3)\n"
    )
    response, _ = await _run(docker_client, tmp_path, code, timeout_s=60)
    assert response.exit_code == 3 and "blocked:" in response.stdout


async def test_runaway_snippet_is_killed_at_the_timeout(docker_client: Any, tmp_path: Path) -> None:
    response, _ = await _run(
        docker_client,
        tmp_path,
        "import time\nprint('start', flush=True)\ntime.sleep(60)\n",
        timeout_s=3,
    )
    assert response.killed and response.exit_code == 137
    assert "start" in response.stdout and "killed after 3 s" in response.stderr
    assert response.duration_s < 30


async def test_rootfs_is_read_only_and_tmp_is_writable(docker_client: Any, tmp_path: Path) -> None:
    code = (
        "import pathlib\n"
        "pathlib.Path('/tmp/x').write_text('ok')\n"
        "try:\n"
        "    pathlib.Path('/usr/x').write_text('no')\n"
        "except OSError as exc:\n"
        "    print('rootfs:', type(exc).__name__)\n"
        "try:\n"
        "    pathlib.Path('/work/escape').write_text('no')\n"
        "except OSError as exc:\n"
        "    print('work:', type(exc).__name__)\n"
    )
    response, _ = await _run(docker_client, tmp_path, code, timeout_s=60)
    assert response.exit_code == 0, response.stderr
    assert "rootfs: OSError" in response.stdout or "rootfs: PermissionError" in response.stdout
    assert "work: PermissionError" in response.stdout


async def test_memory_hog_is_killed_by_the_limit(docker_client: Any, tmp_path: Path) -> None:
    code = (
        "chunks = []\n"
        "for i in range(64):\n"
        "    chunks.append(bytearray(32 * 1024 * 1024))\n"
        "    print('allocated', (i + 1) * 32, 'MB', flush=True)\n"
        "print('survived')\n"
    )
    response, _ = await _run(docker_client, tmp_path, code, timeout_s=120, memory_mb=256)
    assert not response.killed  # not the timeout: the kernel's memory limit
    assert response.exit_code == 137 and "survived" not in response.stdout
    assert "allocated" in response.stdout and response.duration_s < 60


async def test_process_limit_holds(docker_client: Any, tmp_path: Path) -> None:
    code = (
        "import subprocess, sys\n"
        "children = []\n"
        "try:\n"
        "    for i in range(200):\n"
        "        cmd = [sys.executable, '-c', 'import time; time.sleep(30)']\n"
        "        children.append(subprocess.Popen(cmd))\n"
        "except (BlockingIOError, OSError) as exc:\n"
        "    print('stopped at', len(children), 'children:', type(exc).__name__)\n"
        "else:\n"
        "    print('started', len(children), 'children')\n"
        "for child in children:\n"
        "    child.kill()\n"
    )
    response, _ = await _run(docker_client, tmp_path, code, timeout_s=120, pids=32)
    assert response.exit_code == 0, response.stderr
    assert "stopped at" in response.stdout
    started = int(response.stdout.split("stopped at ")[1].split()[0])
    assert started < 32


async def test_outputs_beyond_the_cap_are_refused_for_real(
    docker_client: Any, tmp_path: Path
) -> None:
    code = (
        "import pathlib\n"
        "pathlib.Path('out/big.bin').write_bytes(b'0' * (3 * 1024 * 1024))\n"
        "pathlib.Path('out/small.txt').write_text('kept?')\n"
        "print('written')\n"
    )
    response, _ = await _run(docker_client, tmp_path, code, timeout_s=60, max_output_mb=1)
    assert response.exit_code == 0 and "written" in response.stdout
    assert response.outputs == [] and "outputs exceed 1 MB; nothing uploaded" in response.stderr


async def test_tmp_is_size_capped(docker_client: Any, tmp_path: Path) -> None:
    code = (
        "import pathlib\n"
        "try:\n"
        "    pathlib.Path('/tmp/big').write_bytes(b'0' * (400 * 1024 * 1024))\n"
        "    print('wrote 400 MB')\n"
        "except OSError as exc:\n"
        "    print('tmp full:', type(exc).__name__)\n"
    )
    response, _ = await _run(docker_client, tmp_path, code, timeout_s=120)
    assert response.exit_code == 0, response.stderr
    assert "tmp full: OSError" in response.stdout
