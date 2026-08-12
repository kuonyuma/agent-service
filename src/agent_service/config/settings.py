from dataclasses import dataclass
from pathlib import Path
import yaml
import sys

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
    except yaml.YAMLError as e:
        sys.stderr.write(f"文件setting解析yaml失败.\n错误信息:{e}")
    model = config["model"]
    return AppConfig(
        model_config=ModelConfig(
            name=model["name"], max_tokens=model["max_tokens"], key=model["key"]
        ),
        system_prompt=(_CONFIG_DIR / "system_prompt.md").read_text(encoding="utf-8"),
    )


settings = _load_config()
