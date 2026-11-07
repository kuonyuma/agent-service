from collections.abc import AsyncGenerator, Mapping
from contextlib import aclosing
from types import TracebackType
from typing import Self

from google.genai import types

from agent_service.errors import AgentRuntimeError, AgentServiceError
from agent_service.kernel.context.compression import compress_context
from agent_service.kernel.dispatch.approval import PermissionCheck
from agent_service.kernel.dispatch.base import Tool
from agent_service.kernel.model.port import ModelGateway
from agent_service.kernel.runtime.bindings import BindingFactory
from agent_service.kernel.runtime.control import RunControl
from agent_service.kernel.runtime.events import LoopEvent, LoopResult
from agent_service.kernel.runtime.loop import query, turn_limit


class AgentRunner:
    """接收一次用户输入，管理上下文并驱动运行；所有外部能力显式注入。"""

    def __init__(
        self,
        *,
        model: ModelGateway,
        tool_registry: Mapping[str, Tool],
        system_prompt: str,
        binding_factory: BindingFactory | None = None,
        max_tokens: int = 4096,
        tools: list[types.Tool] | None = None,
        allowed_tool_names: frozenset[str] | None = None,
        max_turns: int = 10,
        plan_and_execute: bool = True,
    ) -> None:
        self.model = model
        self.binding_factory = binding_factory
        self.max_tokens = max_tokens
        self.tool_registry = dict(tool_registry)
        self.tools = (
            tools
            if tools is not None
            else [types.Tool(function_declarations=[
                tool.to_function_declaration() for tool in self.tool_registry.values()
            ])]
        )
        self.allowed_tool_names = allowed_tool_names
        self.system_prompt = system_prompt
        self.max_turns = turn_limit(max_turns)
        self.plan_and_execute = plan_and_execute

    async def run_turn(
        self,
        history: list[types.Content],
        user_query: str,
        permission_check: PermissionCheck | None = None,
        run_control: RunControl | None = None,
    ) -> AsyncGenerator[LoopEvent]:
        """基于历史运行一轮，并通过最终事件显式返回新历史。"""

        next_history = [
            *history,
            types.Content(role="user", parts=[types.Part.from_text(text=user_query)]),
        ]
        try:
            if run_control is not None:
                run_control.ensure_running()
            next_history = await compress_context(
                next_history, model=self.model, max_tokens=self.max_tokens,
            )
        except AgentServiceError as exc:
            yield self._failed_event(next_history, exc)
            return
        except Exception as exc:  # noqa: BLE001 - 服务边界必须转换未知异常
            runtime_error = AgentRuntimeError(f"上下文压缩失败：{exc}")
            yield self._failed_event(next_history, runtime_error)
            return

        async with aclosing(
            query(
                contents=next_history,
                tools=self.tools,
                model=self.model,
                binding_factory=self.binding_factory,
                max_tokens=self.max_tokens,
                permission_check=permission_check,
                allowed_tool_names=self.allowed_tool_names,
                tool_registry=self.tool_registry,
                system_prompt=self.system_prompt,
                max_turns=self.max_turns,
                plan_and_execute=self.plan_and_execute,
                run_control=run_control,
            )
        ) as stream:
            async for event in stream:
                yield event

    async def close(self) -> None:
        """释放 Runner 使用的共享外部客户端。"""

        await self.model.close()

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
