"""WriteFile 工具的单元测试。"""

import pytest

from agent_service.tools.write_file import WriteFileTool


@pytest.mark.asyncio
async def test_write_new_file(tmp_path):
    target = tmp_path / "hello.txt"

    result = await WriteFileTool().run(
        {"path": str(target), "content": "hello_write_test"}
    )

    assert not result.is_error
    assert target.read_text(encoding="utf-8") == "hello_write_test"


@pytest.mark.asyncio
async def test_empty_path_returns_error():
    result = await WriteFileTool().run({"path": "", "content": "内容"})

    assert result.is_error


@pytest.mark.asyncio
async def test_empty_content_returns_error(tmp_path):
    result = await WriteFileTool().run(
        {"path": str(tmp_path / "empty.txt"), "content": ""}
    )

    assert result.is_error


@pytest.mark.asyncio
async def test_nested_directory_is_created(tmp_path):
    target = tmp_path / "a" / "b" / "c" / "deep.txt"

    result = await WriteFileTool().run(
        {"path": str(target), "content": "deep_content"}
    )

    assert not result.is_error
    assert target.read_text(encoding="utf-8") == "deep_content"


@pytest.mark.asyncio
async def test_existing_file_is_overwritten(tmp_path):
    target = tmp_path / "overwrite.txt"

    await WriteFileTool().run({"path": str(target), "content": "first"})
    result = await WriteFileTool().run(
        {"path": str(target), "content": "second"}
    )

    assert not result.is_error
    assert target.read_text(encoding="utf-8") == "second"


def test_tool_metadata():
    tool = WriteFileTool()

    assert tool.name == "write_file"
    assert tool.read_only is False
    assert set(tool.input_schema["required"]) == {"path", "content"}
    assert tool.to_function_declaration().name == "write_file"
