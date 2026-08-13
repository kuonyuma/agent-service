"""真实 Gemini 异步流式客户端测试。"""

import pytest

from agent_service.client.stream_message import stream_message


@pytest.mark.asyncio
async def test_gemini_stream_emits_completion(require_gemini_api_key):
    events = []

    async for event in stream_message(
        contents="请只回复：外部流式测试成功",
    ):
        events.append(event)

    assert events
    assert events[0].type == "message_start"
    assert events[-1].type == "message_done"
    assert events[-1].result is not None
    assert events[-1].result.contents.strip()
