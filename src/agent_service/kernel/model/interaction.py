from collections.abc import AsyncGenerator
from contextlib import aclosing
from copy import deepcopy

from google.genai import types

from agent_service.errors import AgentRuntimeError
from agent_service.kernel.model.port import ModelGateway, StreamEvent, StreamResult


class ModelInteraction:
    """准备请求、校验完整响应并累计主模型用量，不管理具体客户端。"""

    def __init__(
        self, model: ModelGateway, system_prompt: str, max_tokens: int
    ) -> None:
        self.model = model
        self.system_prompt = system_prompt
        self.max_tokens = max_tokens
        self.usage: dict[str, int] = {"input_len": 0, "output_len": 0}
        self.result: StreamResult | None = None

    async def stream(
        self, contents: list[types.Content], tools: list[types.Tool]
    ) -> AsyncGenerator[StreamEvent]:
        self.result = None
        async with aclosing(
            self.model.stream(
                contents=contents,
                tools=tools,
                system_prompt=self.system_prompt,
                max_tokens=self.max_tokens,
            )
        ) as stream:
            async for event in stream:
                if event.type == "message_done":
                    self.result = deepcopy(event.result)
                yield event
        if self.result is None:
            raise AgentRuntimeError("Agent 循环没有收到模型的完成结果。")
        # 保留原始 Part（含思考签名），并将调用表与历史中的调用统一为同一批对象。
        raw_calls = [
            part.function_call
            for part in self.result.raw_parts
            if part.function_call is not None
        ]
        if raw_calls:
            self.result.function_calls = raw_calls
        else:
            if not self.result.raw_parts and self.result.contents:
                self.result.raw_parts.append(
                    types.Part.from_text(text=self.result.contents)
                )
            self.result.raw_parts.extend(
                types.Part(function_call=call) for call in self.result.function_calls
            )
        for key in ("input_len", "output_len", "cached_len"):
            if key in self.result.usage:
                value = self.result.usage[key]
                self.usage[key] = self.usage.get(key, 0) + (
                    value if isinstance(value, int) else 0
                )

    def total_usage(self, extra: dict[str, int]) -> dict[str, int]:
        merged = dict(self.usage)
        for key, value in extra.items():
            merged[key] = merged.get(key, 0) + value
        return merged
