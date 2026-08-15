"""工具注册表、工具执行器和具体工具之间的集成测试。"""

import pytest
from google.genai import types

from agent_service.tools.executor import execute_tools
from agent_service.tools.index import find_tool, get_function_declarations


@pytest.mark.asyncio
async def test_execute_tools_dispatches_registered_tool(tmp_path):
    target = tmp_path / "source.txt"
    target.write_text("来自文件工具的内容", encoding="utf-8")
    function_call = types.FunctionCall(
        name="read_file",
        args={"path": str(target)},
    )

    response = await execute_tools([function_call])

    assert response.role == "user"
    parts = response.parts
    assert parts is not None
    assert len(parts) == 1
    function_response = parts[0].function_response
    assert function_response is not None
    assert function_response.name == "read_file"
    assert function_response.response == {"result": "来自文件工具的内容"}


@pytest.mark.asyncio
async def test_execute_tools_reports_unknown_tool():
    function_call = types.FunctionCall(
        name="not_registered",
        args={},
    )

    response = await execute_tools([function_call])

    parts = response.parts
    assert parts is not None
    function_response = parts[0].function_response
    assert function_response is not None
    assert function_response.response == {"result": "未知的工具not_registered"}


def test_tool_registry_exposes_function_declarations():
    assert find_tool("read_file") is not None

    declarations = get_function_declarations()
    function_declarations = declarations[0].function_declarations
    assert function_declarations is not None
    names = {declaration.name for declaration in function_declarations}

    assert "read_file" in names
    assert "write_file" in names
