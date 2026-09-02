from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from kama_claude.core.tools.builtin._workspace_path import WorkspaceBoundaryError
from kama_claude.core.tools.builtin.list_dir import ListDirTool
from kama_claude.core.tools.builtin.read_file import ReadFileTool
from kama_claude.core.tools.builtin.write_file import WriteFileTool

windows_symlink_test = pytest.mark.skipif(
    os.name == "nt",
    reason="symlink escape semantics run on Linux; Windows uses the junction regression",
)


# 构造目录符号链接；当前平台或权限不支持时跳过相关安全用例
def _directory_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable on this platform: {exc}")


# 功能：验证读取工具同时拒绝 POSIX、盘符、盘符相对路径和 UNC 锚定路径
# 设计：参数化两套路径语法，在 Windows 上一次覆盖跨平台输入校验边界
@pytest.mark.parametrize(
    "raw_path",
    [
        "/etc/passwd",
        r"C:\Windows\System32\drivers\etc\hosts",
        r"C:drive-relative.txt",
        r"\\server\share\secret.txt",
    ],
)
async def test_read_file_rejects_posix_and_windows_anchored_paths(
    tmp_path: Path, raw_path: str
) -> None:
    with pytest.raises(WorkspaceBoundaryError, match="absolute paths are not allowed"):
        await ReadFileTool(workspace=tmp_path).invoke({"path": raw_path})


# 功能：验证三个工具统一拒绝正斜杠和反斜杠形式的父目录跳转
# 设计：参数化典型变体并通过写工具触发公共 resolver，避免只测字符串特例
@pytest.mark.parametrize("raw_path", ["../secret.txt", r"..\secret.txt", "a/../file.txt"])
async def test_all_parent_traversal_forms_are_rejected(
    tmp_path: Path, raw_path: str
) -> None:
    with pytest.raises(WorkspaceBoundaryError, match="parent traversal is not allowed"):
        await WriteFileTool(workspace=tmp_path).invoke(
            {"path": raw_path, "content": "blocked"}
        )


# 功能：验证即使绝对路径位于 workspace 内也会被拒绝
# 设计：分别调用读、写、列目录工具，保证接口契约始终要求不可信输入为相对路径
async def test_absolute_path_is_rejected_even_when_it_is_inside_workspace(
    tmp_path: Path,
) -> None:
    target = tmp_path / "inside.txt"
    target.write_text("inside", encoding="utf-8")

    with pytest.raises(WorkspaceBoundaryError):
        await ReadFileTool(workspace=tmp_path).invoke({"path": str(target)})
    with pytest.raises(WorkspaceBoundaryError):
        await WriteFileTool(workspace=tmp_path).invoke(
            {"path": str(target), "content": "blocked"}
        )
    with pytest.raises(WorkspaceBoundaryError):
        await ListDirTool(workspace=tmp_path).invoke({"path": str(tmp_path)})


# 功能：验证默认 workspace 在工具创建时捕获，而不是每次调用动态读取 cwd
# 设计：创建工具后切换 cwd，再读取原工作区文件，覆盖权限边界随环境漂移的风险
async def test_default_workspace_is_captured_when_tool_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    elsewhere = tmp_path / "elsewhere"
    workspace.mkdir()
    elsewhere.mkdir()
    (workspace / "captured.txt").write_text("captured", encoding="utf-8")

    monkeypatch.chdir(workspace)
    tool = ReadFileTool()
    monkeypatch.chdir(elsewhere)

    result = await tool.invoke({"path": "captured.txt"})
    assert result.content == "captured"


# 功能：验证 read_file 不会通过文件符号链接读取工作区外内容
# 设计：链接指向同级外部目录的真实文件，断言规范化后的目标触发边界异常
@windows_symlink_test
async def test_read_file_rejects_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    link = workspace / "secret-link.txt"
    try:
        link.symlink_to(secret)
    except OSError as exc:
        pytest.skip(f"file symlinks are unavailable on this platform: {exc}")

    with pytest.raises(WorkspaceBoundaryError, match="path escapes workspace"):
        await ReadFileTool(workspace=workspace).invoke({"path": "secret-link.txt"})


# 功能：验证 write_file 不会通过符号链接父目录在工作区外创建文件
# 设计：把 workspace 子目录链接到 outside，断言调用失败且目标文件没有副作用
@windows_symlink_test
async def test_write_file_rejects_symlinked_parent_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    _directory_symlink_or_skip(workspace / "linked", outside)

    with pytest.raises(WorkspaceBoundaryError, match="path escapes workspace"):
        await WriteFileTool(workspace=workspace).invoke(
            {"path": "linked/created.txt", "content": "blocked"}
        )
    assert not (outside / "created.txt").exists()


# 功能：验证 list_dir 递归时遇到越界目录链接会拒绝而非泄漏目录内容
# 设计：外部目录放置唯一文件，通过工作区链接列举并断言公共边界异常
@windows_symlink_test
async def test_list_dir_does_not_follow_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    _directory_symlink_or_skip(workspace / "linked", outside)

    with pytest.raises(WorkspaceBoundaryError, match="path escapes workspace"):
        await ListDirTool(workspace=workspace).invoke({"path": ".", "max_depth": 2})


# 功能：验证 Windows junction 不能绕过 write_file 的规范化工作区边界
# 设计：仅在 Windows 创建 junction 指向外部目录，断言写入失败且外部无文件
@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
async def test_windows_junction_escape_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    junction = workspace / "junction"
    workspace.mkdir()
    outside.mkdir()
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip("could not create a Windows junction in the test environment")

    with pytest.raises(WorkspaceBoundaryError, match="path escapes workspace"):
        await WriteFileTool(workspace=workspace).invoke(
            {"path": "junction/created.txt", "content": "blocked"}
        )
    assert not (outside / "created.txt").exists()
