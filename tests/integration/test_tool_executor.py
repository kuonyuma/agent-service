"""工具注册表、工具执行器和具体工具之间的集成测试。"""

import pytest
from google.genai import types

from agent_service.adapters.tools.index import (
    create_tool_registry,
    get_function_declarations,
)
from agent_service.kernel.dispatch.executor import execute_tools


@pytest.mark.asyncio
async def test_execute_tools_dispatches_registered_tool(tmp_path):
    target = tmp_path / "source.txt"
    target.write_text("来自文件工具的内容", encoding="utf-8")
    function_call = types.FunctionCall(
        name="read_file",
        args={"path": str(target)},
    )

    batch = await execute_tools([function_call], tool_registry=create_tool_registry())
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

    batch = await execute_tools([function_call], tool_registry=create_tool_registry())
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
        [function_call], tool_registry=create_tool_registry(),
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

    denied = await execute_tools([function_call], tool_registry=create_tool_registry())
    approved = await execute_tools(
        [function_call], tool_registry=create_tool_registry(),
        approved_tool_call_ids=frozenset({"call-approval"}),
    )

    assert denied.executions[0].result.is_error
    assert not approved.executions[0].result.is_error
    assert target.read_text(encoding="utf-8") == "已批准"


def test_tool_registry_exposes_function_declarations():
    registry = create_tool_registry()
    assert {"read_file", "web_search", "fetch_web_page"} <= registry.keys()
    assert registry["read_file"] is not create_tool_registry()["read_file"]

    declarations = get_function_declarations(registry)
    function_declarations = declarations[0].function_declarations
    assert function_declarations is not None
    names = {declaration.name for declaration in function_declarations}

    assert "read_file" in names
    assert "write_file" in names
    assert "web_search" in names
    assert "fetch_web_page" in names
