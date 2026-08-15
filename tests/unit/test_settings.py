"""配置加载的单元测试。"""

import pytest

import agent_service.config.settings as settings_module


def test_load_config_rejects_invalid_yaml(monkeypatch, tmp_path):
    (tmp_path / "config.yaml").write_text("model: [", encoding="utf-8")
    monkeypatch.setattr(settings_module, "_CONFIG_DIR", tmp_path)

    with pytest.raises(RuntimeError, match="配置文件解析失败"):
        settings_module._load_config()
