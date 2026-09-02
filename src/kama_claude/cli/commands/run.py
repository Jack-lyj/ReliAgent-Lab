from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any

from kama_claude.core.config import KamaConfig
from kama_claude.core.transport.socket_client import IpcError, SocketClient


class StdoutPrinter:
    # 接收 dict 格式的事件并将运行进度格式化打印到终端
    def __init__(self) -> None:
        self._inline = False  # True while LLM tokens are mid-line
        self._run_start: float = 0.0

    # 若当前行有未换行的 token，补一个换行符
    def _ensure_newline(self) -> None:
        if self._inline:
            print()
            self._inline = False

    # 根据事件 type 字段分发并格式化打印到 stdout/stderr
    async def handle(self, event: dict[str, Any]) -> None:
        t = event.get("type", "")

        if t == "run.started":
            self._run_start = time.monotonic()
            print(f"[run] {event.get('run_id', '')}")

        elif t == "step.started":
            self._ensure_newline()
            print(f"[step {event.get('step')}] planning...")

        elif t == "llm.token":
            print(event.get("token", ""), end="", flush=True)
            self._inline = True

        elif t == "tool.call_started":
            self._ensure_newline()
            params_str = json.dumps(event.get("params", {}), ensure_ascii=False)
            print(f"[tool] {event.get('tool_name', '')} {params_str}")

        elif t == "tool.call_finished":
            print(f"[tool] {event.get('tool_name', '')} ✓  {event.get('elapsed_ms')}ms")

        elif t == "tool.call_failed":
            print(
                f"[tool] {event.get('tool_name', '')} ✗  {event.get('error_message', '')}",
                file=sys.stderr,
            )

        elif t == "step.finished":
            self._ensure_newline()
            print(f"[step {event.get('step')}] done")

        elif t == "run.finished":
            self._ensure_newline()
            elapsed = time.monotonic() - self._run_start
            print(f"[run] {event.get('status', '')}  {event.get('steps')} steps  {elapsed:.1f}s")


class _RunEventRouter:
    # 初始化 run 事件路由器；run_id 返回前先暂存事件，避免快速 run 的完成事件丢失
    def __init__(self, printer: StdoutPrinter) -> None:
        self._printer = printer
        self._run_id: str | None = None
        self._buffered: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self.finished = asyncio.Event()
        self.exit_code = 0

    # 接收全局订阅事件，只向打印器转发当前命令所属 run 的事件
    async def handle(self, event: dict[str, Any]) -> None:
        async with self._lock:
            if self._run_id is None:
                self._buffered.append(event)
                return
            if event.get("run_id") == self._run_id:
                await self._deliver(event)

    # 绑定 agent.run 返回的 run_id，并按原顺序回放此前暂存的匹配事件
    async def select_run(self, run_id: str) -> None:
        async with self._lock:
            self._run_id = run_id
            buffered = self._buffered
            self._buffered = []
            for event in buffered:
                if event.get("run_id") == run_id:
                    await self._deliver(event)

    # 打印已匹配事件，并且只用目标 run.finished 决定命令退出状态
    async def _deliver(self, event: dict[str, Any]) -> None:
        await self._printer.handle(event)
        if event.get("type") == "run.finished":
            if event.get("status") != "success":
                self.exit_code = 1
            self.finished.set()


# 异步核心：连接 daemon，订阅事件，触发 run，等待 run.finished
async def _run_async(goal: str, config: KamaConfig) -> int:
    client = SocketClient(config.host, config.port)
    try:
        await client.connect()
    except (ConnectionRefusedError, OSError):
        print(f"error: core not running ({config.host}:{config.port})", file=sys.stderr)
        return 1

    printer = StdoutPrinter()
    router = _RunEventRouter(printer)

    async def on_event(event: dict[str, Any]) -> None:
        await router.handle(event)

    client.on_event(on_event)
    loop_task = asyncio.create_task(client.run_event_loop())

    try:
        await client.send_command(
            "event.subscribe",
            {
                "topics": ["run.*", "step.*", "tool.*", "llm.token", "llm.usage"],
                "scope": "global",
            },
        )
        result = await client.send_command("agent.run", {"goal": goal})
        run_id = result.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise IpcError(-1, "agent.run returned no run_id")
        await router.select_run(run_id)
    except IpcError as e:
        print(f"error: {e}", file=sys.stderr)
        loop_task.cancel()
        await client.close()
        return 1

    await router.finished.wait()

    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass

    await client.close()
    return router.exit_code


# 执行 kama run --goal "..." 命令
def cmd_run(goal: str, config: KamaConfig) -> None:
    try:
        exit_code = asyncio.run(_run_async(goal, config))
    except KeyboardInterrupt:
        sys.exit(130)
    sys.exit(exit_code)
