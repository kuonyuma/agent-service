"""真实 Gemini 测试的前置条件。"""

import os

import pytest
from sqlalchemy import delete, text
from sqlalchemy.engine import make_url

from agent_service.config.settings import settings
from agent_service.db import (
    AgentRun,
    Conversation,
    Database,
    Message,
    ToolCall,
    create_database,
)


@pytest.fixture
def require_gemini_api_key():
    """没有有效 API Key 时跳过 Gemini 测试，而不是误报为代码失败。"""

    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        key = settings.model_config.key.strip()
    if not key or key.startswith("your"):
        pytest.skip("未配置有效的 GEMINI_API_KEY")


@pytest.fixture
async def mysql_database():
    """连接显式指定的测试库；不会回退到开发库。"""

    database_url = os.getenv("AGENT_SERVICE_TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("未配置 AGENT_SERVICE_TEST_DATABASE_URL")
    database_name = make_url(database_url).database or ""
    if not database_name.lower().endswith("_test"):
        pytest.fail("MySQL 外部测试只允许使用名称以 _test 结尾的专用数据库。")

    database = create_database(database_url)
    cleanup_ready = False
    try:
        async with database.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        await _clear_mysql_test_data(database)
        cleanup_ready = True
        yield database
    finally:
        if cleanup_ready:
            await _clear_mysql_test_data(database)
        await database.close()


async def _clear_mysql_test_data(database: Database) -> None:
    """只清理用户显式传入的测试数据库中的本项目表数据。"""

    async with database.session_factory.begin() as session:
        for model in (ToolCall, Message, AgentRun, Conversation):
            await session.execute(delete(model))
