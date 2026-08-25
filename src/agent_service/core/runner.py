from collections.abc import AsyncGenerator, Mapping
from types import TracebackType
from typing import Self

from google.genai import types

from agent_service.client.client import close_client
from agent_service.core.agentic_loop import (
    LoopEvent,
    LoopResult,
    PermissionCheck,
    query,
)
from agent_service.core.context import compress_context
from agent_service.errors import AgentRuntimeError, AgentServiceError
from agent_service.tools.base import Tool
from agent_service.tools.index import get_function_declarations


class AgentRunner:
    """供终端 UI 或未来 Web API 调用的单轮 Agent 服务。"""

    def __init__(
        self,
        tools: list[types.Tool] | None = None,
        allowed_tool_names: frozenset[str] | None = None,
        tool_registry: Mapping[str, Tool] | None = None,
        system_prompt: str | None = None,
        max_turns: int = 10,
    ) -> None:
        self.tool_registry = dict(tool_registry) if tool_registry is not None else None
        self.tools = (
            tools
            if tools is not None
            else get_function_declarations(self.tool_registry)
        )
        self.allowed_tool_names = allowed_tool_names
        self.system_prompt = system_prompt
        self.max_turns = max_turns

    async def run_turn(
        self,
        history: list[types.Content],
        user_query: str,
        permission_check: PermissionCheck | None = None,
    ) -> AsyncGenerator[LoopEvent]:
        """基于历史运行一轮，并通过最终事件显式返回新历史。"""

        next_history = [
            *history,
            types.Content(role="user", parts=[types.Part.from_text(text=user_query)]),
        ]
        try:
            next_history = await compress_context(next_history)
        except AgentServiceError as exc:
            yield self._failed_event(next_history, exc)
            return
        except Exception as exc:  # noqa: BLE001 - 服务边界必须转换未知异常
            runtime_error = AgentRuntimeError(f"上下文压缩失败：{exc}")
            yield self._failed_event(next_history, runtime_error)
            return

        async for event in query(
            contents=next_history,
            tools=self.tools,
            permission_check=permission_check,
            allowed_tool_names=self.allowed_tool_names,
            tool_registry=self.tool_registry,
            system_prompt=self.system_prompt,
            max_turns=self.max_turns,
        ):
            yield event

    async def close(self) -> None:
        """释放 Runner 使用的共享外部客户端。"""

        await close_client()

    @staticmethod
    def _failed_event(
        history: list[types.Content],
        error: AgentServiceError,
    ) -> LoopEvent:
        return LoopEvent(
            type="run.failed",
            result=LoopResult(
                reason="error",
                history=list(history),
                new_contents=[],
                turns=0,
                usage={"input_len": 0, "output_len": 0},
                error=error,
            ),
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()
