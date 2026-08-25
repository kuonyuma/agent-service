from subprocess import CompletedProcess

import pytest

from agent_service.tools import quality_check
from agent_service.tools.quality_check import QualityCheckTool
from agent_service.tools.workspace import WorkspacePathPolicy


@pytest.mark.asyncio
async def test_quality_check_uses_fixed_argv_and_sanitized_environment(
    monkeypatch,
    tmp_path,
):
    calls = []
    monkeypatch.setenv("GEMINI_API_KEY", "不能泄露")
    monkeypatch.setenv("AGENT_SERVICE_DATABASE_URL", "不能泄露")

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return CompletedProcess(command, 0, stdout="检查通过", stderr="")

    monkeypatch.setattr(quality_check.subprocess, "run", fake_run)
    tool = QualityCheckTool(WorkspacePathPolicy(tmp_path))

    result = await tool.run({"check": "ruff"})

    assert not result.is_error
    command, kwargs = calls[0]
    assert command[1:] == ["-m", "ruff", "check", "."]
    assert kwargs["shell"] is False
    assert kwargs["cwd"] == tmp_path.resolve()
    assert "GEMINI_API_KEY" not in kwargs["env"]
    assert "AGENT_SERVICE_DATABASE_URL" not in kwargs["env"]


@pytest.mark.asyncio
async def test_quality_check_rejects_arbitrary_command(tmp_path):
    tool = QualityCheckTool(WorkspacePathPolicy(tmp_path))

    result = await tool.run({"check": "python -c evil"})

    assert result.is_error
    assert "不允许" in result.content


@pytest.mark.asyncio
async def test_quality_check_nonzero_exit_is_error(monkeypatch, tmp_path):
    def fake_run(command, **kwargs):
        return CompletedProcess(command, 1, stdout="", stderr="检查失败")

    monkeypatch.setattr(quality_check.subprocess, "run", fake_run)
    tool = QualityCheckTool(WorkspacePathPolicy(tmp_path))

    result = await tool.run({"check": "pyright"})

    assert result.is_error
    assert "returncode=1" in result.content
