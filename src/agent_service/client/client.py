import os
import sys
from google import genai
from agent_service.config.settings import settings

client: genai.Client | None = None


def get_client():
    global client
    if client is not None:
        return client

    # 优先使用环境变量，未配置时回退到 config/config.yaml
    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_api_key:
        gemini_api_key = settings.model_config.key.strip()

    if gemini_api_key == "" or gemini_api_key.startswith("your"):
        sys.stderr.write(
            "请配置 GEMINI_API_KEY 环境变量，或修改 config/config.yaml 中的 model.key"
        )
        sys.exit(1)

    client = genai.Client(api_key=gemini_api_key)
    return client
