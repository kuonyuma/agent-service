"""RunCommand 工具的单元测试。"""

from subprocess import CompletedProcess

import pytest

from agent_service.tools import run_command
from agent_service.tools.run_command import RunCommandTool


@pytest.mark.asyncio
async def test_run_command_passes_explicit_check_flag(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return CompletedProcess(command, 0, stdout="完成", stderr="")

    monkeypatch.setattr(run_command.subprocess, "run", fake_run)

    result = await RunCommandTool().run({"command": "echo 完成"})

    assert not result.is_error
    assert result.content == "完成returncode0"
    assert calls == [
        (
            "echo 完成",
            {
                "shell": True,
                "capture_output": True,
                "text": True,
                "check": False,
            },
        )
    ]


@pytest.mark.asyncio
async def test_run_command_returns_nonzero_exit_output(monkeypatch):
    def fake_run(command, **kwargs):
        return CompletedProcess(command, 1, stdout="", stderr="执行失败")

    monkeypatch.setattr(run_command.subprocess, "run", fake_run)

    result = await RunCommandTool().run({"command": "exit 1"})

    assert not result.is_error
    assert result.content == "执行失败returncode1"


@pytest.mark.asyncio
async def test_empty_command_returns_error():
    result = await RunCommandTool().run({"command": ""})

    assert result.is_error


def test_tool_metadata():
    tool = RunCommandTool()

    assert tool.name == "run_command"
    assert tool.read_only is False
    assert tool.input_schema["required"] == ["command"]
    assert tool.to_function_declaration().name == "run_command"
