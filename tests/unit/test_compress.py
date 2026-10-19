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


@pytest.mark.asyncio
async def test_compress_with_tool_call_history_avoids_invalid_role_or_orphan_calls(
    monkeypatch,
):
    """包含多轮工具调用的历史在压缩时不会向模型发送孤立 function_call，且返回角色严格交替。"""

    captured_summary_query: list[types.Content] = []

    async def capture_stream_message(contents, system_prompt, max_tokens):
        captured_summary_query.extend(contents)
        yield StreamEvent(
            type="message_done",
            result=StreamResult(
                contents="已完成 pyproject.toml 与 README.md 的分析并创建了 notes_demo.md。",
                stop_reason="STOP",
                usage={},
            ),
        )

    monkeypatch.setattr(
        agent_service.core.context,
        "stream_message",
        capture_stream_message,
    )

    tool_history = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text="请阅读配置并生成总结文件")],
        ),
        types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name="read_file",
                    args={"path": "pyproject.toml"},
                )
            ],
        ),
        types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="read_file",
                    response={"result": "[project]", "is_error": False},
                )
            ],
        ),
        types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name="update_plan",
                    args={"action": "create", "goal": "生成总结"},
                )
            ],
        ),
        types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="update_plan",
                    response={"result": "计划已创建", "is_error": False},
                )
            ],
        ),
        types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name="write_file",
                    args={"path": "notes_demo.md", "content": "总结"},
                )
            ],
        ),
        types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="write_file",
                    response={"result": "写入成功", "is_error": False},
                )
            ],
        ),
        types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name="update_plan",
                    args={
                        "action": "update_step",
                        "step_id": "step-1",
                        "status": "completed",
                    },
                )
            ],
        ),
        types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="update_plan",
                    response={"result": "步骤已完成", "is_error": False},
                )
            ],
        ),
        types.Content(
            role="model",
            parts=[types.Part.from_text(text="任务已全部完成。")],
        ),
        types.Content(
            role="user",
            parts=[types.Part.from_text(text="你好，请用一句话介绍 Plan-and-Execute 模式？")],
        ),
    ]

    compressed = await compress_context(tool_history, max_history_len=10)

    # 1. 发送给压缩摘要模型的请求必须是纯文本 user 消息，不含孤立 function_call
    assert len(captured_summary_query) == 1
    assert captured_summary_query[0].role == "user"
    for part in captured_summary_query[0].parts or []:
        assert part.function_call is None
        assert part.function_response is None
        assert part.text is not None and "read_file" in part.text

    # 2. 压缩后的主对话历史角色必须严格交替（user -> model -> user）且无残留工具 Part
    assert [item.role for item in compressed] == ["user", "model", "user"]
    for item in compressed:
        for part in item.parts or []:
            assert part.function_call is None
            assert part.function_response is None
