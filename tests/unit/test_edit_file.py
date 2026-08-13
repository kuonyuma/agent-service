"""EditFile 工具的单元测试。"""

import pytest

from agent_service.tools.edit_file import EditFileTool


@pytest.mark.asyncio
async def test_normal_replace(tmp_path):
    """唯一匹配时应完成替换。"""

    target = tmp_path / "normal.txt"
    target.write_text("hello world", encoding="utf-8")

    result = await EditFileTool().run(
        {
            "path": str(target),
            "old_string": "hello",
            "new_string": "goodbye",
        }
    )

    assert not result.is_error
    assert target.read_text(encoding="utf-8") == "goodbye world"


@pytest.mark.asyncio
async def test_nonexistent_file_returns_error(tmp_path):
    result = await EditFileTool().run(
        {
            "path": str(tmp_path / "missing.txt"),
            "old_string": "a",
            "new_string": "b",
        }
    )

    assert result.is_error


@pytest.mark.asyncio
async def test_no_match_returns_error(tmp_path):
    target = tmp_path / "nomatch.txt"
    target.write_text("hello world", encoding="utf-8")

    result = await EditFileTool().run(
        {
            "path": str(target),
            "old_string": "not-exist",
            "new_string": "replaced",
        }
    )

    assert result.is_error
    assert target.read_text(encoding="utf-8") == "hello world"


@pytest.mark.asyncio
async def test_multiple_matches_are_rejected(tmp_path):
    target = tmp_path / "multiple.txt"
    target.write_text("aaa bbb aaa", encoding="utf-8")

    result = await EditFileTool().run(
        {
            "path": str(target),
            "old_string": "aaa",
            "new_string": "ccc",
        }
    )

    assert result.is_error
    assert target.read_text(encoding="utf-8") == "aaa bbb aaa"


def test_tool_metadata():
    tool = EditFileTool()

    assert tool.name == "edit_file"
    assert tool.read_only is False
    assert set(tool.input_schema["required"]) == {
        "path",
        "old_string",
        "new_string",
    }
    assert tool.to_function_declaration().name == "edit_file"
