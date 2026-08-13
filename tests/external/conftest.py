"""真实 Gemini 测试的前置条件。"""

import os

import pytest

from agent_service.config.settings import settings


@pytest.fixture
def require_gemini_api_key():
    """没有有效 API Key 时跳过 Gemini 测试，而不是误报为代码失败。"""

    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        key = settings.model_config.key.strip()
    if not key or key.startswith("your"):
        pytest.skip("未配置有效的 GEMINI_API_KEY")
