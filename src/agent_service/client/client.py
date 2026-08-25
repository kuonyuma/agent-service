import os

from google import genai

from agent_service.config.settings import settings
from agent_service.errors import ConfigurationError

client: genai.Client | None = None


def get_client() -> genai.Client:
    global client
    if client is not None:
        return client

    # 优先使用环境变量，未配置时回退到 config/config.yaml
    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_api_key:
        gemini_api_key = settings.model_config.key.strip()

    if gemini_api_key == "" or gemini_api_key.startswith("your"):
        raise ConfigurationError(
            "请配置 GEMINI_API_KEY 环境变量，或修改 config/config.yaml 中的 model.key"
        )

    client = genai.Client(api_key=gemini_api_key)
    return client


async def close_client() -> None:
    """关闭共享 Gemini 客户端，并允许后续重新初始化。"""

    global client
    current_client = client
    client = None
    if current_client is None:
        return

    try:
        await current_client.aio.aclose()
    finally:
        current_client.close()
