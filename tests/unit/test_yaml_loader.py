"""LoadYaml 工具的单元测试。"""

import pytest

from agent_service.tools.yaml_loader import LoadYamlTool


@pytest.mark.asyncio
async def test_load_valid_yaml(tmp_path):
    target = tmp_path / "custom.yaml"
    target.write_text("name: test\nversion: 1\n", encoding="utf-8")

    result = await LoadYamlTool().run({"path": str(target)})

    assert not result.is_error
    assert "name" in result.content
    assert "test" in result.content


@pytest.mark.asyncio
async def test_nonexistent_path_returns_error(tmp_path):
    result = await LoadYamlTool().run({"path": str(tmp_path / "missing.yaml")})

    assert result.is_error


@pytest.mark.asyncio
async def test_empty_path_returns_error():
    result = await LoadYamlTool().run({"path": ""})

    assert result.is_error


@pytest.mark.asyncio
async def test_invalid_yaml_returns_error(tmp_path):
    target = tmp_path / "invalid.yaml"
    target.write_text("name: [unclosed", encoding="utf-8")

    result = await LoadYamlTool().run({"path": str(target)})

    assert result.is_error


@pytest.mark.asyncio
async def test_empty_yaml_file_returns_error(tmp_path):
    target = tmp_path / "empty.yaml"
    target.write_text("", encoding="utf-8")

    result = await LoadYamlTool().run({"path": str(target)})

    assert result.is_error


@pytest.mark.asyncio
async def test_missing_path_key_returns_error():
    result = await LoadYamlTool().run({})

    assert result.is_error


def test_tool_metadata():
    tool = LoadYamlTool()

    assert tool.name == "load_yaml"
    assert tool.read_only is True
    assert tool.to_function_declaration().name == "load_yaml"
