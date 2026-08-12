from dataclasses import dataclass, field
from google.genai import types
from typing import Any, Literal, AsyncGenerator
from agent_service.client.client import get_client
from agent_service.config.settings import settings


@dataclass
class StreamResult:
    contents: str
    stop_reason: str
    usage: dict[str, Any]
    function_calls: list[types.FunctionCall] = field(default_factory=list)
    raw_parts: list = field(default_factory=list)


@dataclass
class StreamEvent:
    type: Literal["message_start", "text", "tool_use_start", "message_done"]
    text: str = ""
    id: str = ""
    name: str = ""
    result: StreamResult | None = None


async def stream_message(
    contents: Any,
    system_prompt: str | None = "你是一位编程助手",
    max_tokens: int | None = None,
    tools: list[types.Tool] | None = None,
) -> AsyncGenerator[StreamEvent, None]:
    client = get_client()

    config = types.GenerateContentConfig(
        system_instruction=system_prompt, max_output_tokens=max_tokens, tools=tools
    )
    response = await client.aio.models.generate_content_stream(
        model=settings.model_config.name, contents=contents, config=config
    )

    yield StreamEvent(type="message_start")

    full_text: str = ""
    full_parts: list = []
    calls: list = []
    input_tokens: int = 0
    output_tokens: int = 0
    stop: str = "message_done"

    async for chunk in response:
        if chunk.usage_metadata:
            input_tokens = getattr(
                chunk.usage_metadata, "prompt_token_count", input_tokens
            )
            output_tokens = getattr(
                chunk.usage_metadata, "candidates_token_count", output_tokens
            )
        if chunk.candidates:
            candidate = chunk.candidates[0]
            if candidate.finish_reason:
                stop = str(candidate.finish_reason)
            if candidate.content and candidate.content.parts:
                parts = candidate.content.parts
                for part in parts:
                    full_parts.append(part)
                    if part.text:
                        full_text += part.text
                        yield StreamEvent(type="text", text=part.text)
                    if part.function_call:
                        fc = part.function_call
                        calls.append(fc)
                        yield StreamEvent(
                            type="tool_use_start",
                            id=fc.id or "",
                            name=fc.name or "",
                        )

    result = StreamResult(
        contents=full_text,
        stop_reason="tool_use" if calls else stop,
        usage={"input_len": input_tokens, "output_len": output_tokens},
        function_calls=calls,
        raw_parts=full_parts,
    )

    yield StreamEvent(type="message_done", result=result)
