"""Gemini 客户端配置与生命周期测试。"""

from types import SimpleNamespace

import pytest

import agent_service.client.client as client_module
from agent_service.errors import ConfigurationError


def test_get_client_raises_configuration_error_for_missing_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(client_module, "client", None)
    monkeypatch.setattr(
        client_module,
        "settings",
        SimpleNamespace(model_config=SimpleNamespace(key="your...")),
    )

    with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
        client_module.get_client()


@pytest.mark.asyncio
async def test_close_client_releases_sync_and_async_clients(monkeypatch):
    calls: list[str] = []

    class FakeAsyncClient:
        async def aclose(self):
            calls.append("async")

    class FakeClient:
        aio = FakeAsyncClient()

        def close(self):
            calls.append("sync")

    monkeypatch.setattr(client_module, "client", FakeClient())

    await client_module.close_client()

    assert calls == ["async", "sync"]
    assert client_module.client is None
