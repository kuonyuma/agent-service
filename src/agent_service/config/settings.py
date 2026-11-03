from dataclasses import dataclass
from math import isfinite
from pathlib import Path

import yaml

_CONFIG_DIR = Path(__file__).parent


@dataclass(frozen=True)
class ModelConfig:
    name: str
    max_tokens: int
    key: str


@dataclass(frozen=True)
class SafetyConfig:
    chunk_chars: int = 800
    interval_seconds: float = 1.5
    timeout_seconds: float = 15.0
    context_chars: int = 1200
    max_buffer_chars: int = 16000

    def __post_init__(self) -> None:
        if (
            any(
                type(value) is not int
                for value in (
                    self.chunk_chars,
                    self.context_chars,
                    self.max_buffer_chars,
                )
            )
            or any(
                type(value) not in (int, float) or not isfinite(value)
                for value in (self.interval_seconds, self.timeout_seconds)
            )
            or self.chunk_chars <= 0
            or self.interval_seconds <= 0
            or self.timeout_seconds <= 0
            or self.context_chars < 0
            or self.max_buffer_chars < self.chunk_chars
        ):
            raise ValueError("安全审核的长度、时间和缓冲区配置无效")


@dataclass(frozen=True)
class AppConfig:
    model_config: ModelConfig
    system_prompt: str
    safety: SafetyConfig
    safety_prompt: str


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
        safety=SafetyConfig(**config.get("safety", {})),
        safety_prompt=(_CONFIG_DIR / "safety_prompt.md").read_text(encoding="utf-8"),
    )


settings = _load_config()
