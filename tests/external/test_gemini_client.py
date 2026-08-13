"""真实 Gemini 同步客户端测试。

执行前需要配置 GEMINI_API_KEY，并显式传入 --run-external。
"""

from agent_service.client.client import get_client
from agent_service.config.settings import settings


def test_gemini_client_returns_text(require_gemini_api_key):
    client = get_client()

    response = client.models.generate_content(
        model=settings.model_config.name,
        contents="请只回复：外部客户端测试成功",
    )

    assert response.text
