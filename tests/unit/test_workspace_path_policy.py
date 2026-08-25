"""工作区路径隔离策略及文件工具适配测试。"""

import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from agent_service.tools.edit_file import EditFileTool
from agent_service.tools.list_files import ListFilesTool
from agent_service.tools.read_file import ReadFileTool
from agent_service.tools.workspace import WorkspacePathError, WorkspacePathPolicy
from agent_service.tools.write_file import WriteFileTool
from agent_service.tools.yaml_loader import LoadYamlTool


def test_policy_accepts_relative_existing_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "docs" / "note.txt"
    target.parent.mkdir(parents=True)
    target.write_text("工作区内容", encoding="utf-8")
    policy = WorkspacePathPolicy(workspace)

    resolved = policy.resolve_existing("docs/note.txt", expected="file")

    assert resolved == target.resolve()
    assert policy.display_path(resolved) == "docs/note.txt"


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../outside.txt",
        r"C:\Windows\win.ini",
        r"C:relative.txt",
        r"\\server\share\secret.txt",
        r"\\?\C:\Windows\win.ini",
        r"\\.\C:\Windows\win.ini",
        "/etc/passwd",
        "note.txt:secret",
    ],
)
def test_policy_rejects_escape_and_windows_special_paths(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    policy = WorkspacePathPolicy(tmp_path / "workspace")

    with pytest.raises(WorkspacePathError):
        policy.resolve_existing(unsafe_path)


@pytest.mark.parametrize(
    "sensitive_path",
    [
        ".git/config",
        ".venv/pyvenv.cfg",
        ".env",
        ".env.local",
        "config.yaml",
        "nested/CONFIG.YAML",
    ],
)
def test_policy_rejects_sensitive_path_segments(
    tmp_path: Path,
    sensitive_path: str,
) -> None:
    policy = WorkspacePathPolicy(tmp_path / "workspace")

    with pytest.raises(WorkspacePathError, match="敏感路径"):
        policy.resolve_for_write(sensitive_path)


def test_policy_creates_safe_nested_directories_for_write(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    policy = WorkspacePathPolicy(workspace)

    target = policy.resolve_for_write("generated/nested/result.txt")
    target.write_text("完成", encoding="utf-8")

    assert target.read_text(encoding="utf-8") == "完成"
    assert target.parent == (workspace / "generated" / "nested").resolve()


def test_policy_rejects_symlink_component(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = workspace / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前 Windows 环境不允许创建符号链接：{exc}")
    policy = WorkspacePathPolicy(workspace)

    with pytest.raises(WorkspacePathError, match="符号链接|重解析点"):
        policy.resolve_existing("linked/secret.txt", expected="file")


def test_windows_reparse_attribute_is_recognized(tmp_path: Path) -> None:
    policy = WorkspacePathPolicy(tmp_path / "workspace")
    fake_status = cast(
        os.stat_result,
        SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
        ),
    )

    assert policy._is_link_or_reparse(policy.root / "junction", fake_status)


def test_policy_enforces_read_and_write_limits(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "large.txt"
    target.write_text("12345", encoding="utf-8")
    policy = WorkspacePathPolicy(
        workspace,
        max_read_bytes=4,
        max_write_bytes=4,
        max_output_bytes=10,
    )

    with pytest.raises(WorkspacePathError, match="读取上限"):
        policy.read_text(policy.resolve_existing("large.txt", expected="file"))
    with pytest.raises(WorkspacePathError, match="写入内容超过"):
        policy.validate_write_content("12345")


@pytest.mark.asyncio
async def test_file_tools_share_workspace_policy(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    policy = WorkspacePathPolicy(workspace)

    write_result = await WriteFileTool(policy).run(
        {"path": "notes/todo.txt", "content": "第一版"}
    )
    read_result = await ReadFileTool(policy).run({"path": "notes/todo.txt"})
    edit_result = await EditFileTool(policy).run(
        {
            "path": "notes/todo.txt",
            "old_string": "第一版",
            "new_string": "第二版",
        }
    )

    assert not write_result.is_error
    assert str(workspace.resolve()) not in write_result.content
    assert read_result.content == "第一版"
    assert not edit_result.is_error
    assert (workspace / "notes" / "todo.txt").read_text(encoding="utf-8") == "第二版"


@pytest.mark.asyncio
async def test_tools_with_policy_cannot_read_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside.txt"
    outside.write_text("不应读取", encoding="utf-8")
    policy = WorkspacePathPolicy(workspace)

    result = await ReadFileTool(policy).run({"path": str(outside)})

    assert result.is_error
    assert "相对路径" in result.content


@pytest.mark.asyncio
async def test_none_policy_preserves_cli_absolute_path_behavior(tmp_path: Path) -> None:
    target = tmp_path / "cli.txt"
    target.write_text("CLI 可读", encoding="utf-8")

    result = await ReadFileTool().run({"path": str(target)})

    assert not result.is_error
    assert result.content == "CLI 可读"


@pytest.mark.asyncio
async def test_list_tool_hides_sensitive_entries_and_host_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text("SECRET=value", encoding="utf-8")
    (workspace / "visible.txt").write_text("内容", encoding="utf-8")
    (workspace / "folder").mkdir()
    policy = WorkspacePathPolicy(workspace)

    result = await ListFilesTool(policy).run({})

    assert not result.is_error
    assert "visible.txt" in result.content
    assert "folder/" in result.content
    assert ".env" not in result.content
    assert str(workspace.resolve()) not in result.content


@pytest.mark.asyncio
async def test_yaml_tool_obeys_policy_and_blocks_config_yaml(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "public.yml").write_text("name: demo\n", encoding="utf-8")
    (workspace / "config.yaml").write_text("key: secret\n", encoding="utf-8")
    tool = LoadYamlTool(WorkspacePathPolicy(workspace))

    public_result = await tool.run({"path": "public.yml"})
    blocked_result = await tool.run({"path": "config.yaml"})

    assert not public_result.is_error
    assert "demo" in public_result.content
    assert blocked_result.is_error
    assert "敏感路径" in blocked_result.content
