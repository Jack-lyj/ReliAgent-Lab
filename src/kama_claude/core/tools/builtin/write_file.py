from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from kama_claude.core.tools.base import BaseTool, ToolResult
from kama_claude.core.tools.builtin._workspace_path import WorkspacePathResolver

_MAX_BYTES = 1 * 1024 * 1024  # 1 MB


class WriteFileParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str
    content: str


class WriteFileTool(BaseTool):
    params_model = WriteFileParams
    name = "write_file"
    description = (
        "Write text content to a file, creating it (and any parent directories) if it "
        "does not exist, or overwriting it if it does. "
        "Path must be relative to the current working directory. "
        "Content size is limited to 1 MB."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file (relative to current working directory).",
            },
            "content": {
                "type": "string",
                "description": "Text content to write.",
            },
        },
        "required": ["path", "content"],
    }

    # 创建工具时捕获工作区根目录，所有写入都复用同一权限边界
    def __init__(self, workspace: Path | None = None) -> None:
        self._paths = WorkspacePathResolver(workspace)

    # 写入 workspace 内文件；超 1MB 拒绝；校验父目录后再创建并写入
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = WriteFileParams.model_validate(params)
        path_str = p.path
        content = p.content

        encoded = content.encode("utf-8")
        if len(encoded) > _MAX_BYTES:
            return ToolResult(
                content=f"content too large: {len(encoded)} bytes (limit 1 MB)",
                is_error=True,
                error_type="runtime_error",
            )

        path = self._paths.resolve(path_str, must_exist=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Re-resolve after mkdir so a symlink/junction introduced through an
        # existing or newly created parent cannot redirect this write.
        path = self._paths.resolve(path_str, must_exist=False)
        self._paths.ensure_contained(path.parent, must_exist=True)
        path.write_text(content, encoding="utf-8")

        return ToolResult(content=f"wrote {len(encoded)} bytes to {path_str}")
