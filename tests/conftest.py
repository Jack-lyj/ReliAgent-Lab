from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply stable test-layer markers from the repository layout.

    Keeping the mapping here makes ``pytest -m unit`` and
    ``pytest -m integration`` reliable without repeating a marker in every
    test module. Tests may add narrower markers such as ``e2e`` themselves.
    """
    for item in items:
        path_parts = Path(str(item.path)).parts
        if "unit" in path_parts:
            item.add_marker(pytest.mark.unit)
        elif "integration" in path_parts:
            item.add_marker(pytest.mark.integration)

        if item.path.name == "test_run_e2e.py":
            item.add_marker(pytest.mark.e2e)

        if item.path.name == "test_workspace_path_security.py":
            item.add_marker(pytest.mark.platform)


@pytest.fixture
def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return port  # socket released; daemon can bind to this port


@pytest.fixture
async def running_daemon(
    free_port: int,
    tmp_path: Path,
) -> AsyncGenerator[subprocess.Popen[bytes], None]:
    env = os.environ.copy()
    env["KAMA_PORT"] = str(free_port)
    env["KAMA_LOG_FILE"] = ""
    env["KAMA_LOG_LEVEL"] = "WARNING"
    env["KAMA_TRACE_FILE"] = str(tmp_path / "daemon-trace.jsonl")
    env["USERPROFILE"] = str(tmp_path)
    env["HOME"] = str(tmp_path)

    proc = subprocess.Popen(
        [sys.executable, "-m", "kama_claude.core"],
        env=env,
        stderr=subprocess.PIPE,
    )

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
            pytest.fail(f"Daemon exited during startup:\n{stderr}")
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
            writer.close()
            await writer.wait_closed()
            break
        except (ConnectionRefusedError, OSError):
            pass
    else:
        proc.terminate()
        proc.wait()
        stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        pytest.fail(f"Daemon did not start within 10 seconds:\n{stderr}")

    yield proc

    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
