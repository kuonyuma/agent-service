"""ReadFile 工具的单元测试。"""

import pytest

from agent_service.tools.read_file import ReadFileTool


@pytest.mark.asyncio
async def test_read_existing_file(tmp_path):
    target = tmp_path / "readme.txt"
    target.write_text("hello agent", encoding="utf-8")

    result = await ReadFileTool().run({"path": str(target)})

    assert not result.is_error
    assert result.content == "hello agent"


@pytest.mark.asyncio
async def test_read_nonexistent_file_returns_error(tmp_path):
    result = await ReadFileTool().run({"path": str(tmp_path / "missing.txt")})

    assert result.is_error


@pytest.mark.asyncio
async def test_missing_path_returns_error():
    result = await ReadFileTool().run({})

    assert result.is_error


def test_tool_metadata():
    tool = ReadFileTool()

    assert tool.name == "read_file"
    assert tool.read_only is True
    assert "path" in tool.input_schema["required"]
    assert tool.to_function_declaration().name == "read_file"
