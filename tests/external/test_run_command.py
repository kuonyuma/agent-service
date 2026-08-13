"""RunCommand 工具的真实操作系统进程测试。

该测试会调用当前 Windows shell，因此不归入纯单元测试。
"""

import pytest

from agent_service.tools.run_command import RunCommandTool


@pytest.mark.asyncio
async def test_echo_command():
    result = await RunCommandTool().run({"command": "echo hello_external"})

    assert not result.is_error
    assert "hello_external" in result.content
    assert "returncode0" in result.content


@pytest.mark.asyncio
async def test_empty_command_returns_error():
    result = await RunCommandTool().run({"command": ""})

    assert result.is_error


@pytest.mark.asyncio
async def test_missing_command_returns_error():
    result = await RunCommandTool().run({})

    assert result.is_error


@pytest.mark.asyncio
async def test_failing_command_reports_return_code():
    result = await RunCommandTool().run({"command": "exit 1"})

    assert not result.is_error
    assert "returncode1" in result.content


def test_tool_metadata():
    tool = RunCommandTool()

    assert tool.name == "run_command"
    assert tool.read_only is False
    assert "command" in tool.input_schema["required"]
    assert tool.to_function_declaration().name == "run_command"
