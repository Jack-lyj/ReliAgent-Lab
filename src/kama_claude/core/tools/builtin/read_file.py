from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from kama_claude.core.tools.base import BaseTool, ToolResult
from kama_claude.core.tools.builtin._workspace_path import WorkspacePathResolver

_MAX_BYTES = 512 * 1024  # 512 KB


class ReadFileParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str


class ReadFileTool(BaseTool):
    params_model = ReadFileParams
    name = "read_file"
    description = (
        "Read the text content of a file. "
        "Path must be relative to the current working directory. "
        "Files larger than 512 KB are truncated."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file (relative to current working directory).",
            }
        },
        "required": ["path"],
    }

    # 创建工具时捕获工作区根目录，防止调用期间 cwd 改变扩大读取范围
    def __init__(self, workspace: Path | None = None) -> None:
        self._paths = WorkspacePathResolver(workspace)

    # 读取 workspace 内文件；超 512KB 截断；拒绝绝对路径和解析后的越界路径
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        path_str = ReadFileParams.model_validate(params).path
        path = self._paths.resolve(path_str, must_exist=True)
        raw = path.read_bytes()  # raises FileNotFoundError if absent
        truncated = len(raw) > _MAX_BYTES
        text = raw[:_MAX_BYTES].decode("utf-8", errors="replace")
        if truncated:
            text += "\n[truncated]"

        return ToolResult(content=text)
