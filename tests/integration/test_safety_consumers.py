import asyncio
from io import StringIO

from google.genai import types
from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

import agent_service.adapters.cli.app as app_module
import agent_service.adapters.gemini.model as loop_module
from agent_service.adapters.security.coordinator import BLOCKED_MESSAGE, RunCoordinator
from agent_service.adapters.security.reviewer import ReviewResult
from agent_service.config.settings import SafetyConfig
from agent_service.kernel.model.port import StreamEvent, StreamResult
from tests.support import create_runner


async def test_cli_replaces_answer_and_cancels_actual_prompt_application(monkeypatch):
    text = "待屏蔽的模型文本"
    output = StringIO()
    monkeypatch.setattr(app_module, "console", Console(file=output, width=80))
    request_call = types.FunctionCall(
        id="call-1", name="write_file", args={"path": "unused.txt", "content": "测试"}
    )

    async def model(**kwargs):
        yield StreamEvent(type="text", text=text)
        yield StreamEvent(
            type="message_done",
            result=StreamResult(
                contents=text,
                stop_reason="tool_use",
                usage={},
                function_calls=[request_call],
                raw_parts=[
                    types.Part.from_text(text=text),
                    types.Part(function_call=request_call),
                ],
            ),
        )

    monkeypatch.setattr(loop_module, "stream_message", model)
    with create_pipe_input() as pipe:
        session = PromptSession(input=pipe, output=DummyOutput())
        monkeypatch.setattr(app_module, "PromptSession", lambda: session)

        class Reviewer:
            async def check(self, text):
                while not session.app.is_running:
                    await asyncio.sleep(0)
                return ReviewResult("block", "violence")

        app = app_module.App(RunCoordinator(create_runner(plan_and_execute=False), Reviewer()))
        app.coordinator.config = SafetyConfig(chunk_chars=1)
        async with asyncio.timeout(3):
            await app.run_turn("测试请求")
        assert not session.app.is_running
        assert app._active_live is None
        assert app._safety_stopped
        assert text not in str(app.contents)
        assert text not in output.getvalue()
        assert BLOCKED_MESSAGE in output.getvalue()
