from collections.abc import AsyncGenerator
from typing import Any

from google.genai import types

from agent_service.adapters.gemini.client import get_client
from agent_service.config.settings import settings
from agent_service.kernel.model.port import StreamEvent, StreamResult


async def stream_message(
    contents: Any,
    system_prompt: str | None = "你是一位编程助手",
    max_tokens: int | None = None,
    tools: list[types.Tool] | None = None,
) -> AsyncGenerator[StreamEvent]:
    client = get_client()

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=max_tokens,
        tools=tools,
    )
    response = await client.aio.models.generate_content_stream(
        model=settings.model_config.name,
        contents=contents,
        config=config,
    )

    yield StreamEvent(type="message_start")

    full_text: str = ""
    full_parts: list = []
    calls: list = []
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    has_cached_tokens: bool = False
    stop: str = "message_done"

    try:
        async for chunk in response:
            if chunk.usage_metadata:
                input_tokens = getattr(
                    chunk.usage_metadata, "prompt_token_count", input_tokens
                )
                output_tokens = getattr(
                    chunk.usage_metadata, "candidates_token_count", output_tokens
                )
                raw_cached = getattr(
                    chunk.usage_metadata, "cached_content_token_count", None
                )
                if isinstance(raw_cached, int):
                    cached_tokens = raw_cached
                    has_cached_tokens = True
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
    finally:
        close_stream = getattr(response, "aclose", None)
        if close_stream is not None:
            await close_stream()

    usage_payload: dict[str, Any] = {
        "input_len": input_tokens,
        "output_len": output_tokens,
    }
    if has_cached_tokens:
        usage_payload["cached_len"] = cached_tokens

    result = StreamResult(
        contents=full_text,
        stop_reason="tool_use" if calls else stop,
        usage=usage_payload,
        function_calls=calls,
        raw_parts=full_parts,
    )

    yield StreamEvent(type="message_done", result=result)
