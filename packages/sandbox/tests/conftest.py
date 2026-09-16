"""Fakes for the runner tests: a docker-py look-alike that records what it was asked to do."""

from __future__ import annotations

import io
import tarfile
from dataclasses import dataclass, field
from typing import Any

import requests.exceptions


def tar_bytes(files: dict[str, bytes], root: str = "out") -> bytes:
    """A tar archive the way ``get_archive('/work/out')`` returns one: rooted at ``out/``."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo(root)
        info.type = tarfile.DIRTYPE
        tar.addfile(info)
        for name, data in files.items():
            info = tarfile.TarInfo(f"{root}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@dataclass
class FakeContainer:
    options: dict[str, Any]
    stdout: bytes = b""
    stderr: bytes = b""
    exit_code: int = 0
    outputs: dict[str, bytes] = field(default_factory=dict)
    hangs: bool = False  # wait() times out
    archive: bytes | None = None
    started: bool = False
    killed: bool = False
    removed: dict[str, Any] | None = None

    def put_archive(self, path: str, data: Any) -> bool:
        self.archive = data.read() if hasattr(data, "read") else bytes(data)
        self.put_path = path
        return True

    def start(self) -> None:
        self.started = True

    def wait(self, **kwargs: Any) -> dict[str, Any]:
        self.wait_kwargs = kwargs
        if self.hangs:
            raise requests.exceptions.ReadTimeout("no answer")
        return {"StatusCode": self.exit_code}

    def kill(self, signal: Any = None) -> None:
        self.killed = True

    def logs(self, **kwargs: Any) -> bytes:
        return self.stdout if kwargs.get("stdout") else self.stderr

    def get_archive(self, path: str, **kwargs: Any) -> tuple[Any, dict[str, Any]]:
        data = tar_bytes(self.outputs)
        chunks = [data[i : i + 1024] for i in range(0, len(data), 1024)]
        return iter(chunks), {"name": "out"}

    def remove(self, **kwargs: Any) -> None:
        self.removed = kwargs


class FakeContainers:
    def __init__(self, template: FakeContainer) -> None:
        self.template = template
        self.created: list[FakeContainer] = []

    def create(self, image: str, **options: Any) -> FakeContainer:
        container = FakeContainer(
            options={"image": image, **options},
            stdout=self.template.stdout,
            stderr=self.template.stderr,
            exit_code=self.template.exit_code,
            outputs=dict(self.template.outputs),
            hangs=self.template.hangs,
        )
        self.created.append(container)
        return container


class FakeDocker:
    def __init__(self, template: FakeContainer | None = None) -> None:
        self.containers = FakeContainers(template or FakeContainer(options={}))
        self.images = self

    def get(self, name: str) -> dict[str, str]:
        return {"name": name}

    def ping(self) -> bool:
        return True
