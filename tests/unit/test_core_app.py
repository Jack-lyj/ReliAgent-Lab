from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import kama_claude.core.app as app_module
from kama_claude.core.app import CoreApp
from kama_claude.core.bus.commands import SessionCompactResult


class MemoryWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.drain_calls = 0

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        self.drain_calls += 1


class FakeSessions:
    def __init__(self) -> None:
        self.closed: list[str] = []

    async def create(self, *, mode: str, title: str) -> Any:
        return SimpleNamespace(id=f"{mode}-1", status="waiting_for_input", title=title)

    async def resume(self, session_id: str) -> Any:
        return SimpleNamespace(id=session_id, status="waiting_for_input", title="restored")

    async def send_message(self, session_id: str, content: str) -> str:
        return f"run-{session_id}-{content}"

    async def get_history(self, session_id: str) -> list[dict[str, Any]]:
        return [{"role": "user", "content": session_id}]

    async def compact(self, session_id: str, focus: str) -> SessionCompactResult:
        assert session_id == "s1"
        assert focus == "tests"
        return SessionCompactResult(summary_tokens=12, saved_tokens=34)

    async def close(self, session_id: str) -> None:
        self.closed.append(session_id)


async def test_ping_returns_version_and_non_negative_uptime() -> None:
    result = await CoreApp()._ping_handler({"client": "pytest"})

    assert result.server_version
    assert result.uptime_ms >= 0
    assert result.received_at


async def test_replay_events_filters_topics_and_ignores_corrupt_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                "not-json",
                json.dumps({"type": "run.started", "run_id": "r1"}),
                json.dumps({"type": "tool.started", "run_id": "r1"}),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "events_file", lambda _run_id: events_path)
    writer = MemoryWriter()

    count = await CoreApp()._replay_events(  # type: ignore[arg-type]
        "r1", writer, ["run.*"]
    )

    assert count == 1
    assert writer.drain_calls == 1
    envelope = json.loads(writer.buffer)
    assert envelope["kind"] == "event"
    assert envelope["event"]["type"] == "run.started"


async def test_replay_events_missing_file_is_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(app_module, "events_file", lambda _run_id: tmp_path / "missing.jsonl")

    count = await CoreApp()._replay_events(  # type: ignore[arg-type]
        "missing", MemoryWriter(), ["*"]
    )

    assert count == 0


async def test_session_handlers_preserve_typed_contracts() -> None:
    app = CoreApp()
    sessions = FakeSessions()
    app._sessions = sessions  # type: ignore[assignment]

    created = await app._session_create_handler({"mode": "chat", "title": "demo"})
    resumed = await app._session_resume_handler({"session_id": created.session_id})
    sent = await app._session_send_handler(
        {"session_id": created.session_id, "content": "hello"}
    )
    history = await app._session_history_handler({"session_id": created.session_id})
    compacted = await app._session_compact_handler({"session_id": "s1", "focus": "tests"})
    closed = await app._session_close_handler({"session_id": created.session_id})

    assert created.status == "waiting_for_input"
    assert resumed.title == "restored"
    assert sent.run_id == "run-chat-1-hello"
    assert history.messages == [{"role": "user", "content": "chat-1"}]
    assert compacted.saved_tokens == 34
    assert closed.status == "closed"
    assert sessions.closed == ["chat-1"]


async def test_permission_response_is_safe_before_manager_initialization() -> None:
    result = await CoreApp()._permission_respond_handler(
        {"tool_use_id": "tool-1", "decision": "deny_once"}
    )

    assert result.ok

