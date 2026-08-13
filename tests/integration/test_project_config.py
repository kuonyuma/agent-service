"""项目配置文件与配置工具之间的集成测试。"""

from pathlib import Path

import pytest

from agent_service.tools.yaml_loader import LoadYamlTool


@pytest.mark.asyncio
async def test_project_config_can_be_loaded():
    project_root = Path(__file__).resolve().parents[2]
    config_path = project_root / "src" / "agent_service" / "config" / "config.yaml"

    result = await LoadYamlTool().run({"path": str(config_path)})

    assert not result.is_error
    assert "model" in result.content
