import sys
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from typing import Literal

from google.genai import types

from agent_service.client.stream_message import StreamResult, stream_message
from agent_service.config.settings import settings
from agent_service.tools.executor import execute_tools
from agent_service.tools.index import find_tool


@dataclass
class LoopResult:
    reason: Literal["completed", "max_turns", "error"]


@dataclass
class LoopEvent:
    type: Literal["text", "tool_start", "tool_done", "turn_complete"]
    text: str = ""
    result: LoopResult | None = None


async def query(
    contents: list[types.Content],
    tools: list[types.Tool],
    permission_check: Callable | None = None,
) -> AsyncGenerator[LoopEvent]:

    turn = 1
    while turn <= 10:
        turn += 1
        result: StreamResult | None = None
        async for event in stream_message(
            contents=contents,
            tools=tools,
            system_prompt=settings.system_prompt,
            max_tokens=settings.model_config.max_tokens,
        ):
            if event.type == "text":
                yield LoopEvent(type="text", text=event.text)
            if event.type == "message_done":
                result = event.result

        if result is None:
            sys.stderr.write("在agentic_loop未收到result")
            sys.exit(1)
        model_content = types.Content(role="model", parts=result.raw_parts)
        contents.append(model_content)

        if result.function_calls:
            named_calls: list[tuple[str, types.FunctionCall]] = []
            for function_call in result.function_calls:
                name = function_call.name
                if name is None:
                    sys.stderr.write("模型返回的工具调用缺少名称，无法执行。\n")
                    yield LoopEvent(
                        type="turn_complete",
                        result=LoopResult(reason="error"),
                    )
                    return
                named_calls.append((name, function_call))

            yield LoopEvent(type="tool_start")

            is_denied = False
            for name, function_call in named_calls:
                t = find_tool(name)

                if t and not t.read_only and permission_check:
                    allowed = await permission_check(name, function_call.args or {})
                    if not allowed:
                        is_denied = True
                        break

            if is_denied:
                parts = []
                for name, _ in named_calls:
                    parts.append(
                        types.Part.from_function_response(
                            name=name,
                            response={"error": "user拒绝了你的修改请求。"},
                        )
                    )
                tool_content = types.Content(role="user", parts=parts)
            else:
                tool_content = await execute_tools(
                    [function_call for _, function_call in named_calls]
                )
            contents.append(tool_content)
            yield LoopEvent(type="tool_done")
        else:
            # 模型不再调用工具，正常结束
            yield LoopEvent(
                type="turn_complete",
                result=LoopResult(reason="completed"),
            )
            return

    # while 循环耗尽（超过最大轮次）
    yield LoopEvent(
        type="turn_complete",
        result=LoopResult(reason="max_turns"),
    )
