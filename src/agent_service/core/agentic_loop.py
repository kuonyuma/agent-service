import json
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Literal
from uuid import uuid7

from google.genai import types

from agent_service.client.stream_message import StreamResult, stream_message
from agent_service.config.settings import settings
from agent_service.errors import (
    AgentRuntimeError,
    AgentServiceError,
    InvalidToolCallError,
)
from agent_service.tools.base import Tool, ToolResult
from agent_service.tools.executor import execute_tools
from agent_service.tools.index import TOOL_REGISTRY

ToolRegistry = Mapping[str, Tool]


@dataclass(frozen=True)
class ToolApprovalRequest:
    """绑定到一次具体工具调用的不可变审批请求。"""

    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    request_hash: str

    @classmethod
    def create(
        cls,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolApprovalRequest:
        canonical = json.dumps(
            {
                "call_id": call_id,
                "tool_name": tool_name,
                "arguments": arguments,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return cls(
            call_id=call_id,
            tool_name=tool_name,
            arguments=dict(arguments),
            request_hash=sha256(canonical.encode("utf-8")).hexdigest(),
        )


PermissionCheck = Callable[[ToolApprovalRequest], Awaitable[bool]]
LoopEventType = Literal[
    "text.delta",
    "tool.requested",
    "tool.approval_required",
    "tool.completed",
    "run.completed",
    "run.failed",
]


@dataclass(frozen=True)
class LoopResult:
    """一次 Agent 运行的最终结果和新历史。"""

    reason: Literal["completed", "max_turns", "error"]
    history: list[types.Content]
    new_contents: list[types.Content]
    turns: int
    usage: dict[str, int] = field(default_factory=dict)
    error: AgentServiceError | None = None


@dataclass(frozen=True)
class LoopEvent:
    """与终端、HTTP 等具体传输方式无关的运行事件。"""

    type: LoopEventType
    text: str = ""
    turn: int = 0
    tool_call_id: str = ""
    tool_name: str = ""
    tool_arguments: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False
    approval_request_hash: str = ""
    tool_result: ToolResult | None = None
    result: LoopResult | None = None


def _usage_value(usage: dict[str, Any], key: str) -> int:
    value = usage.get(key, 0)
    return value if isinstance(value, int) else 0


def _run_result(
    *,
    reason: Literal["completed", "max_turns", "error"],
    history: list[types.Content],
    new_contents: list[types.Content],
    turns: int,
    usage: dict[str, int],
    error: AgentServiceError | None = None,
) -> LoopResult:
    return LoopResult(
        reason=reason,
        history=list(history),
        new_contents=list(new_contents),
        turns=turns,
        usage=dict(usage),
        error=error,
    )


async def query(
    contents: list[types.Content],
    tools: list[types.Tool],
    permission_check: PermissionCheck | None = None,
    allowed_tool_names: frozenset[str] | None = None,
    tool_registry: ToolRegistry | None = None,
    system_prompt: str | None = None,
    max_turns: int = 10,
) -> AsyncGenerator[LoopEvent]:
    """运行 Agent 循环，不修改调用方传入的历史列表。"""

    history = list(contents)
    initial_history_length = len(history)
    usage = {"input_len": 0, "output_len": 0}
    completed_turns = 0
    active_registry = tool_registry if tool_registry is not None else TOOL_REGISTRY

    try:
        for turn in range(1, max_turns + 1):
            completed_turns = turn
            result: StreamResult | None = None
            async for event in stream_message(
                contents=history,
                tools=tools,
                system_prompt=(
                    system_prompt
                    if system_prompt is not None
                    else settings.system_prompt
                ),
                max_tokens=settings.model_config.max_tokens,
            ):
                if event.type == "text":
                    yield LoopEvent(type="text.delta", text=event.text, turn=turn)
                elif event.type == "message_done":
                    result = event.result

            if result is None:
                raise AgentRuntimeError("Agent 循环没有收到模型的完成结果。")

            usage["input_len"] += _usage_value(result.usage, "input_len")
            usage["output_len"] += _usage_value(result.usage, "output_len")
            history.append(types.Content(role="model", parts=result.raw_parts))

            if not result.function_calls:
                yield LoopEvent(
                    type="run.completed",
                    turn=turn,
                    result=_run_result(
                        reason="completed",
                        history=history,
                        new_contents=history[initial_history_length:],
                        turns=turn,
                        usage=usage,
                    ),
                )
                return

            named_calls: list[
                tuple[str, str, types.FunctionCall, ToolApprovalRequest | None]
            ] = []
            for function_call in result.function_calls:
                name = function_call.name
                if not name:
                    raise InvalidToolCallError("模型返回的工具调用缺少名称，无法执行。")
                call_id = function_call.id or str(uuid7())
                function_call.id = call_id
                arguments = dict(function_call.args or {})
                tool = active_registry.get(name)
                requires_approval = bool(
                    tool is not None
                    and not tool.read_only
                    and (
                        allowed_tool_names is None
                        or name in allowed_tool_names
                    )
                )
                approval_request = (
                    ToolApprovalRequest.create(call_id, name, arguments)
                    if requires_approval
                    else None
                )
                named_calls.append(
                    (call_id, name, function_call, approval_request)
                )
                yield LoopEvent(
                    type="tool.requested",
                    turn=turn,
                    tool_call_id=call_id,
                    tool_name=name,
                    tool_arguments=arguments,
                    requires_approval=requires_approval,
                    approval_request_hash=(
                        approval_request.request_hash if approval_request else ""
                    ),
                )

            tool_parts: list[types.Part] = []
            for call_id, name, function_call, approval_request in named_calls:
                approved_call_ids: frozenset[str] = frozenset()
                if approval_request is not None:
                    allowed = (
                        await permission_check(approval_request)
                        if permission_check is not None
                        else False
                    )
                    if allowed:
                        approved_call_ids = frozenset({call_id})

                batch = await execute_tools(
                    [function_call],
                    allowed_tool_names=allowed_tool_names,
                    approved_tool_call_ids=approved_call_ids,
                    tool_registry=active_registry,
                )
                for execution in batch.executions:
                    yield LoopEvent(
                        type="tool.completed",
                        turn=turn,
                        tool_call_id=execution.call_id,
                        tool_name=execution.name,
                        tool_arguments=execution.arguments,
                        tool_result=execution.result,
                    )
                if batch.content.parts:
                    tool_parts.extend(batch.content.parts)

            history.append(types.Content(role="user", parts=tool_parts))

        max_turns_error = AgentRuntimeError(
            f"Agent 已达到最大运行轮数 {max_turns}，任务仍未完成。"
        )
        yield LoopEvent(
            type="run.failed",
            turn=max_turns,
            result=_run_result(
                reason="max_turns",
                history=history,
                new_contents=history[initial_history_length:],
                turns=max_turns,
                usage=usage,
                error=max_turns_error,
            ),
        )
    except AgentServiceError as exc:
        yield LoopEvent(
            type="run.failed",
            turn=completed_turns,
            result=_run_result(
                reason="error",
                history=history,
                new_contents=history[initial_history_length:],
                turns=completed_turns,
                usage=usage,
                error=exc,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - Runtime 边界必须转换未知异常
        runtime_error = AgentRuntimeError(f"Agent 运行失败：{exc}")
        yield LoopEvent(
            type="run.failed",
            turn=completed_turns,
            result=_run_result(
                reason="error",
                history=history,
                new_contents=history[initial_history_length:],
                turns=completed_turns,
                usage=usage,
                error=runtime_error,
            ),
        )
