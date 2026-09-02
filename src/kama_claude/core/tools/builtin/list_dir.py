from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from kama_claude.core.tools.base import BaseTool, ToolResult
from kama_claude.core.tools.builtin._workspace_path import WorkspacePathResolver

_MAX_DEPTH = 4
_MAX_ENTRIES = 200


class ListDirParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str = "."
    max_depth: int = Field(default=2, ge=1, le=_MAX_DEPTH)


class ListDirTool(BaseTool):
    params_model = ListDirParams
    name = "list_dir"
    description = (
        "List the contents of a directory as a tree. "
        "Path must be relative to the current working directory. "
        "Hidden entries (starting with .) are included. "
        f"Maximum depth is {_MAX_DEPTH}, maximum total entries is {_MAX_ENTRIES}."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the directory (default '.').",
            },
            "max_depth": {
                "type": "integer",
                "description": f"How many levels deep to recurse (default 2, max {_MAX_DEPTH}).",
            },
        },
        "required": [],
    }

    # 创建工具时捕获工作区根目录，递归列举不会跟随越界链接
    def __init__(self, workspace: Path | None = None) -> None:
        self._paths = WorkspacePathResolver(workspace)

    # 以树状格式列出 workspace 内目录；递归前校验每个解析后的入口
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = ListDirParams.model_validate(params)
        path_str = p.path
        max_depth = p.max_depth

        root = self._paths.resolve(path_str, must_exist=True)
        if not root.is_dir():
            raise NotADirectoryError(f"not a directory: {path_str}")

        display_root = path_str.rstrip("/\\") or "."
        lines: list[str] = [display_root + "/"]
        count = 0
        visited: set[Path] = {root}

        def _walk(directory: Path, depth: int, prefix: str) -> None:
            nonlocal count
            if depth > max_depth or count >= _MAX_ENTRIES:
                return

            entries: list[tuple[str, Path, bool]] = []
            for entry in directory.iterdir():
                # resolve(strict=False) follows existing symlinks and Windows
                # junctions, including broken links far enough to detect an
                # outside target, without turning a benign broken link into a
                # generic FileNotFoundError for the whole listing.
                resolved_entry = self._paths.ensure_contained(entry, must_exist=False)
                entries.append((entry.name, resolved_entry, resolved_entry.is_dir()))
            entries.sort(key=lambda item: (not item[2], item[0]))

            for i, (entry_name, resolved_entry, is_dir) in enumerate(entries):
                if count >= _MAX_ENTRIES:
                    lines.append(f"{prefix}... (truncated)")
                    return
                connector = "└── " if i == len(entries) - 1 else "├── "
                suffix = "/" if is_dir else ""
                lines.append(f"{prefix}{connector}{entry_name}{suffix}")
                count += 1
                if is_dir and depth < max_depth and resolved_entry not in visited:
                    visited.add(resolved_entry)
                    extension = "    " if i == len(entries) - 1 else "│   "
                    _walk(resolved_entry, depth + 1, prefix + extension)

        _walk(root, 1, "")
        return ToolResult(content="\n".join(lines))
