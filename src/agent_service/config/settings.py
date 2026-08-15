from dataclasses import dataclass
from pathlib import Path

import yaml

_CONFIG_DIR = Path(__file__).parent


@dataclass(frozen=True)
class ModelConfig:
    name: str
    max_tokens: int
    key: str


@dataclass(frozen=True)
class AppConfig:
    model_config: ModelConfig
    system_prompt: str


def _load_config() -> AppConfig:
    try:
        with open(_CONFIG_DIR / "config.yaml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"配置文件解析失败: {exc}") from exc

    if not isinstance(config, dict):
        raise TypeError("配置文件根节点必须是对象")

    if "model" not in config:
        raise ValueError("配置文件缺少 model 对象")

    model = config["model"]
    if not isinstance(model, dict):
        raise TypeError("配置文件的 model 必须是对象")

    required_model_keys = ("name", "max_tokens", "key")
    if any(key not in model for key in required_model_keys):
        raise ValueError("配置文件的 model 缺少 name、max_tokens 或 key")

    name = model["name"]
    max_tokens = model["max_tokens"]
    key = model["key"]
    if (
        not isinstance(name, str)
        or not isinstance(max_tokens, int)
        or not isinstance(key, str)
    ):
        raise TypeError(
            "model.name、model.max_tokens 和 model.key 必须分别为字符串、整数和字符串"
        )

    return AppConfig(
        model_config=ModelConfig(name=name, max_tokens=max_tokens, key=key),
        system_prompt=(_CONFIG_DIR / "system_prompt.md").read_text(encoding="utf-8"),
    )


settings = _load_config()
