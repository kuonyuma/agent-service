"""上下文压缩单元测试。

使用模拟的 stream_message，保证测试不访问真实 Gemini API。
"""

import pytest
from google.genai import types

import agent_service.core.context
from agent_service.client.stream_message import StreamEvent, StreamResult
from agent_service.core.context import compress_context


async def _fake_stream_message(contents, system_prompt, max_tokens):
    """模拟 LLM 流式返回压缩摘要。"""

    yield StreamEvent(
        type="message_done",
        result=StreamResult(
            contents="用户要求编写一个 Web 服务器，已讨论框架选择，最新问题是 HTTPS 支持。",
            stop_reason="STOP",
            usage={},
        ),
    )


def make_contents(count: int):
    return [
        types.Content(
            role="user" if i % 2 == 0 else "model",
            parts=[types.Part.from_text(text=f"消息 {i}")],
        )
        for i in range(count)
    ]


@pytest.mark.parametrize(
    "count,expected_length",
    [
        (5, 5),
        (9, 9),
    ],
)
@pytest.mark.asyncio
async def test_compress_below_threshold(count, expected_length):
    """消息数低于阈值时不触发压缩。"""

    mock_contents = make_contents(count)
    compressed = await compress_context(mock_contents, max_history_len=10)

    assert len(compressed) == expected_length
    assert compressed is mock_contents


@pytest.mark.parametrize(
    "count,expected_length",
    [
        (10, 4),
        (12, 4),
    ],
)
@pytest.mark.asyncio
async def test_compress_exceed_threshold(monkeypatch, count, expected_length):
    """消息数达到阈值时，将模拟摘要合并到对话历史。"""

    mock_contents = make_contents(count)
    monkeypatch.setattr(
        agent_service.core.context,
        "stream_message",
        _fake_stream_message,
    )

    compressed = await compress_context(mock_contents, max_history_len=10)

    assert len(compressed) == expected_length
    assert compressed[0].role == "user"
    first_parts = compressed[0].parts
    assert first_parts is not None
    assert len(first_parts) == 2
    summary_text = first_parts[1].text
    assert summary_text is not None
    assert "HTTPS" in summary_text
    assert compressed[1].role == "model"
    assert compressed[-2:] == mock_contents[-2:]
