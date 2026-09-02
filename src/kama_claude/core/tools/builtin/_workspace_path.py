from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


class WorkspaceBoundaryError(PermissionError):
    """工具路径离开已捕获工作区时抛出的权限错误。"""


class WorkspacePathResolver:
    """将不可信相对路径限制在创建时捕获的工作区内。"""

    # 捕获并规范化工作区根目录，后续 cwd 变化不会扩大权限边界
    def __init__(self, workspace: Path | None = None) -> None:
        root = workspace if workspace is not None else Path.cwd()
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise NotADirectoryError(f"workspace is not a directory: {root}")

    # 同时按 POSIX/Windows 语法拒绝锚定路径和父级跳转，再返回规范化路径
    def resolve(self, raw_path: str, *, must_exist: bool) -> Path:
        posix_path = PurePosixPath(raw_path)
        windows_path = PureWindowsPath(raw_path)

        # Check both path grammars so a Windows path is rejected on POSIX and
        # a POSIX-rooted path is rejected on Windows.  Windows drive-relative
        # paths (for example C:secret.txt) are anchored too and are unsafe.
        if posix_path.is_absolute() or bool(windows_path.anchor):
            raise WorkspaceBoundaryError(f"absolute paths are not allowed: {raw_path}")
        if ".." in posix_path.parts or ".." in windows_path.parts:
            raise WorkspaceBoundaryError(f"parent traversal is not allowed: {raw_path}")

        candidate = self.root / Path(raw_path)
        resolved = self.ensure_contained(candidate, must_exist=False)
        if must_exist:
            # Resolve once more in strict mode after the boundary check.  This
            # preserves FileNotFoundError for ordinary missing paths while a
            # broken link whose target is outside the workspace is rejected
            # above as a boundary violation.
            resolved = self.ensure_contained(candidate, must_exist=True)
        return resolved

    # 解析已构造路径并验证其仍位于工作区根目录内
    def ensure_contained(self, candidate: Path, *, must_exist: bool) -> Path:
        try:
            resolved = candidate.resolve(strict=must_exist)
        except RuntimeError as exc:
            raise WorkspaceBoundaryError(f"cannot safely resolve path: {candidate.name}") from exc

        if not resolved.is_relative_to(self.root):
            raise WorkspaceBoundaryError(f"path escapes workspace: {candidate.name}")
        return resolved
