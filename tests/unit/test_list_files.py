"""ListFiles 工具的单元测试。"""

import pytest

from agent_service.tools.list_files import ListFilesTool


@pytest.mark.asyncio
async def test_list_directory(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "note.txt").write_text("内容", encoding="utf-8")

    result = await ListFilesTool().run({"path": str(tmp_path)})

    assert not result.is_error
    assert "nested/" in result.content
    assert "note.txt" in result.content


@pytest.mark.asyncio
async def test_nonexistent_path_returns_error(tmp_path):
    result = await ListFilesTool().run({"path": str(tmp_path / "missing")})

    assert result.is_error


@pytest.mark.asyncio
async def test_default_path_uses_current_directory(tmp_path, monkeypatch):
    (tmp_path / "current.txt").write_text("内容", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = await ListFilesTool().run({})

    assert not result.is_error
    assert "current.txt" in result.content


def test_tool_metadata():
    tool = ListFilesTool()

    assert tool.name == "list_files"
    assert tool.read_only is True
    assert tool.to_function_declaration().name == "list_files"
