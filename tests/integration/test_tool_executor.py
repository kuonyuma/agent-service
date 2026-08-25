"""工具注册表、工具执行器和具体工具之间的集成测试。"""

import pytest
from google.genai import types

from agent_service.tools.executor import execute_tools
from agent_service.tools.index import (
    ALL_TOOLS,
    TOOL_REGISTRY,
    create_web_tool_registry,
    find_tool,
    get_function_declarations,
)
from agent_service.tools.workspace import WorkspacePathPolicy


@pytest.mark.asyncio
async def test_execute_tools_dispatches_registered_tool(tmp_path):
    target = tmp_path / "source.txt"
    target.write_text("来自文件工具的内容", encoding="utf-8")
    function_call = types.FunctionCall(
        name="read_file",
        args={"path": str(target)},
    )

    batch = await execute_tools([function_call])
    response = batch.content

    assert response.role == "user"
    parts = response.parts
    assert parts is not None
    assert len(parts) == 1
    function_response = parts[0].function_response
    assert function_response is not None
    assert function_response.name == "read_file"
    assert function_response.response == {
        "result": "来自文件工具的内容",
        "is_error": False,
    }
    assert len(batch.executions) == 1
    assert not batch.executions[0].result.is_error


@pytest.mark.asyncio
async def test_execute_tools_reports_unknown_tool():
    function_call = types.FunctionCall(
        name="not_registered",
        args={},
    )

    batch = await execute_tools([function_call])
    response = batch.content

    parts = response.parts
    assert parts is not None
    function_response = parts[0].function_response
    assert function_response is not None
    assert function_response.response == {
        "result": "未知的工具not_registered",
        "is_error": True,
    }
    assert batch.executions[0].result.is_error


@pytest.mark.asyncio
async def test_execute_tools_enforces_allowed_tool_names(tmp_path):
    target = tmp_path / "blocked.txt"
    function_call = types.FunctionCall(
        id="call-1",
        name="write_file",
        args={"path": str(target), "content": "不应写入"},
    )

    batch = await execute_tools(
        [function_call],
        allowed_tool_names=frozenset(),
    )

    assert not target.exists()
    assert batch.executions[0].call_id == "call-1"
    assert batch.executions[0].result.is_error
    assert "未开放" in batch.executions[0].result.content


@pytest.mark.asyncio
async def test_execute_tools_requires_single_call_approval_for_write(tmp_path):
    target = tmp_path / "approval.txt"
    function_call = types.FunctionCall(
        id="call-approval",
        name="write_file",
        args={"path": str(target), "content": "已批准"},
    )

    denied = await execute_tools([function_call])
    approved = await execute_tools(
        [function_call],
        approved_tool_call_ids=frozenset({"call-approval"}),
    )

    assert denied.executions[0].result.is_error
    assert not approved.executions[0].result.is_error
    assert target.read_text(encoding="utf-8") == "已批准"


def test_tool_registry_exposes_function_declarations():
    assert find_tool("read_file") is not None
    assert len(TOOL_REGISTRY) == len(ALL_TOOLS)

    declarations = get_function_declarations()
    function_declarations = declarations[0].function_declarations
    assert function_declarations is not None
    names = {declaration.name for declaration in function_declarations}

    assert "read_file" in names
    assert "write_file" in names


def test_web_read_only_registry_is_explicit_and_fail_closed():
    from agent_service.tools.index import get_read_only_tool_names

    assert get_read_only_tool_names() == frozenset({"list_files", "read_file"})
    assert "load_yaml" not in get_read_only_tool_names()


@pytest.mark.asyncio
async def test_web_tool_registries_do_not_share_workspace_state(tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "same.txt").write_text("第一个工作区", encoding="utf-8")
    (second_root / "same.txt").write_text("第二个工作区", encoding="utf-8")
    first_registry = create_web_tool_registry(WorkspacePathPolicy(first_root))
    second_registry = create_web_tool_registry(WorkspacePathPolicy(second_root))
    function_call = types.FunctionCall(
        id="read-call",
        name="read_file",
        args={"path": "same.txt"},
    )

    first = await execute_tools([function_call], tool_registry=first_registry)
    second = await execute_tools([function_call], tool_registry=second_registry)

    assert first.executions[0].result.content == "第一个工作区"
    assert second.executions[0].result.content == "第二个工作区"
