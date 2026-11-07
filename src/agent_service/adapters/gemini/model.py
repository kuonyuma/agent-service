from collections.abc import AsyncGenerator

from google.genai import types

from agent_service.adapters.gemini.client import close_client
from agent_service.adapters.gemini.stream import stream_message
from agent_service.kernel.model.port import StreamEvent


class GeminiModel:
    """将 Gemini 流式 SDK 适配到内核模型端口。"""

    def stream(
        self,
        *,
        contents: list[types.Content],
        system_prompt: str,
        max_tokens: int,
        tools: list[types.Tool] | None = None,
    ) -> AsyncGenerator[StreamEvent]:
        return stream_message(
            contents=contents,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            tools=tools,
        )

    async def close(self) -> None:
        await close_client()
