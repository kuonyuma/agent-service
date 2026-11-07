from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from google.genai import types


@dataclass
class StreamResult:
    contents: str
    stop_reason: str
    usage: dict[str, Any]
    function_calls: list[types.FunctionCall] = field(default_factory=list)
    raw_parts: list[types.Part] = field(default_factory=list)


@dataclass
class StreamEvent:
    type: Literal["message_start", "text", "tool_use_start", "message_done"]
    text: str = ""
    id: str = ""
    name: str = ""
    result: StreamResult | None = None


class ModelGateway(Protocol):
    """内核所需的模型端口；连接与 SDK 调用由外部适配器实现。"""

    def stream(
        self,
        *,
        contents: list[types.Content],
        system_prompt: str,
        max_tokens: int,
        tools: list[types.Tool] | None = None,
    ) -> AsyncGenerator[StreamEvent]: ...

    async def close(self) -> None: ...
