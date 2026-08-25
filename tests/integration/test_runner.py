"""AgentRunner 与 Agentic Loop 的协作测试。"""

import pytest
from google.genai import types

import agent_service.core.agentic_loop
from agent_service.client.stream_message import StreamEvent, StreamResult
from agent_service.core.runner import AgentRunner


@pytest.mark.asyncio
async def test_runner_returns_new_history_without_modifying_input(monkeypatch):
    received_system_prompt = ""

    async def fake_stream_message(*args, **kwargs):
        nonlocal received_system_prompt
        received_system_prompt = kwargs["system_prompt"]
        yield StreamEvent(type="text", text="完成")
        yield StreamEvent(
            type="message_done",
            result=StreamResult(
                contents="完成",
                stop_reason="STOP",
                usage={"input_len": 12, "output_len": 2},
                raw_parts=[types.Part.from_text(text="完成")],
            ),
        )

    monkeypatch.setattr(
        agent_service.core.agentic_loop,
        "stream_message",
        fake_stream_message,
    )
    original_history = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text="旧问题")],
        ),
        types.Content(
            role="model",
            parts=[types.Part.from_text(text="旧回答")],
        ),
    ]
    runner = AgentRunner(tools=[], system_prompt="仅允许只读工具")

    events = [
        event
        async for event in runner.run_turn(
            history=original_history,
            user_query="新问题",
        )
    ]

    assert [event.type for event in events] == ["text.delta", "run.completed"]
    assert len(original_history) == 2
    assert events[-1].result is not None
    assert len(events[-1].result.history) == 4
    assert len(events[-1].result.new_contents) == 1
    user_parts = events[-1].result.history[-2].parts
    assert user_parts is not None
    assert user_parts[0].text == "新问题"
    assert events[-1].result.usage == {"input_len": 12, "output_len": 2}
    assert received_system_prompt == "仅允许只读工具"
