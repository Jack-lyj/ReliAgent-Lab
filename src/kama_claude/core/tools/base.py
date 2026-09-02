from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    # "runtime_error" | "tool_error" | "timeout" | "schema_error" | "permission_denied"
    error_type: str | None = None
    # 给 LLM 的富内容；为空时使用 content。事件与 TUI 始终只记录文本摘要。
    llm_content: list[dict[str, object]] | None = None


class BaseTool(ABC):
    name: str
    description: str
    input_schema: dict[str, object]
    params_model: ClassVar[type[BaseModel] | None] = None

    # 执行工具调用，返回结果或错误
    @abstractmethod
    async def invoke(self, params: dict[str, object]) -> ToolResult: ...
