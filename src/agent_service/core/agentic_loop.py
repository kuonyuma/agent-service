import json
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Literal
from uuid import uuid7

from google.genai import types

from agent_service.client.stream_message import StreamResult, stream_message
from agent_service.config.settings import settings
from agent_service.core.plan import AgentPhase, ExecutionPlan
from agent_service.errors import (
    AgentRuntimeError,
    AgentServiceError,
    InvalidToolCallError,
)
from agent_service.tools.base import Tool, ToolResult
from agent_service.tools.executor import execute_tools, tool_result_part
from agent_service.tools.index import TOOL_REGISTRY
from agent_service.tools.plan_tool import UpdatePlanTool

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
    "plan.updated",
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
    plan: ExecutionPlan | None = None


@dataclass(frozen=True)
class LoopEvent:
    """与终端、HTTP 等具体传输方式无关的运行事件。"""

    type: LoopEventType
    text: str = ""
    turn: int = 0
    phase: AgentPhase = "planning"
    plan: ExecutionPlan | None = None
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
    plan: ExecutionPlan | None = None,
) -> LoopResult:
    return LoopResult(
        reason=reason,
        history=list(history),
        new_contents=list(new_contents),
        turns=turns,
        usage=dict(usage),
        error=error,
        plan=plan.snapshot() if plan is not None and plan.steps else None,
    )


def _filter_planning_tools(
    tools: list[types.Tool],
    registry: ToolRegistry,
) -> list[types.Tool]:
    """在规划阶段仅向模型暴露 read_only 工具（含 update_plan）。"""

    filtered: list[types.Tool] = []
    for tool_group in tools:
        declarations = tool_group.function_declarations
        if not declarations:
            continue
        read_only_decls = [
            decl
            for decl in declarations
            if decl.name
            and (
                (reg_tool := registry.get(decl.name)) is not None
                and reg_tool.read_only
            )
        ]
        if read_only_decls:
            filtered.append(types.Tool(function_declarations=read_only_decls))
    return filtered


def _build_turn_system_prompt(
    base_prompt: str,
    plan_state: ExecutionPlan,
) -> str:
    """在执行阶段将最新计划状态注入系统提示词。"""

    if not plan_state.steps:
        return base_prompt
    return f"{base_prompt}\n\n## 当前执行计划状态\n{plan_state.format_markdown()}"


async def query(
    contents: list[types.Content],
    tools: list[types.Tool],
    permission_check: PermissionCheck | None = None,
    allowed_tool_names: frozenset[str] | None = None,
    tool_registry: ToolRegistry | None = None,
    system_prompt: str | None = None,
    max_turns: int = 10,
    plan_and_execute: bool = True,
) -> AsyncGenerator[LoopEvent]:
    """运行 Plan-and-Execute Agent 循环，不修改调用方传入的历史列表。"""

    history = list(contents)
    initial_history_length = len(history)
    usage = {"input_len": 0, "output_len": 0}
    completed_turns = 0

    base_registry = (
        dict(tool_registry) if tool_registry is not None else dict(TOOL_REGISTRY)
    )
    use_plan_mode = (
        plan_and_execute
        and "update_plan" in base_registry
        and (allowed_tool_names is None or "update_plan" in allowed_tool_names)
    )
    plan_state = ExecutionPlan(
        phase="planning" if use_plan_mode else "executing"
    )
    if "update_plan" in base_registry:
        base_registry["update_plan"] = UpdatePlanTool(plan_state)
    active_registry: ToolRegistry = base_registry

    base_system_prompt = (
        system_prompt if system_prompt is not None else settings.system_prompt
    )
    completion_reminder_sent = False

    try:
        for turn in range(1, max_turns + 1):
            completed_turns = turn
            current_phase = plan_state.phase
            turn_tools = (
                _filter_planning_tools(tools, active_registry)
                if use_plan_mode and current_phase == "planning"
                else tools
            )
            turn_system_prompt = _build_turn_system_prompt(
                base_system_prompt,
                plan_state,
            )

            result: StreamResult | None = None
            async for event in stream_message(
                contents=history,
                tools=turn_tools,
                system_prompt=turn_system_prompt,
                max_tokens=settings.model_config.max_tokens,
            ):
                if event.type == "text":
                    yield LoopEvent(
                        type="text.delta",
                        text=event.text,
                        turn=turn,
                        phase=plan_state.phase,
                        plan=(
                            plan_state.snapshot() if plan_state.steps else None
                        ),
                    )
                elif event.type == "message_done":
                    result = event.result

            if result is None:
                raise AgentRuntimeError("Agent 循环没有收到模型的完成结果。")

            usage["input_len"] += _usage_value(result.usage, "input_len")
            usage["output_len"] += _usage_value(result.usage, "output_len")
            history.append(types.Content(role="model", parts=result.raw_parts))

            if not result.function_calls:
                if (
                    use_plan_mode
                    and plan_state.phase == "executing"
                    and plan_state.has_unfinished_steps()
                    and not completion_reminder_sent
                    and turn < max_turns
                ):
                    completion_reminder_sent = True
                    history.append(
                        types.Content(
                            role="user",
                            parts=[
                                types.Part.from_text(
                                    text=(
                                        "[系统提醒] 当前执行计划中仍有未完成的步骤：\n"
                                        f"{plan_state.format_markdown()}\n\n"
                                        "请继续执行剩余步骤，或调用 `update_plan(action='update_step', ...)` "
                                        "将已完成/跳过的步骤标记为 `completed` 或 `skipped` 后再给出最终总结。"
                                    )
                                )
                            ],
                        )
                    )
                    continue

                yield LoopEvent(
                    type="run.completed",
                    turn=turn,
                    phase=plan_state.phase,
                    plan=plan_state.snapshot() if plan_state.steps else None,
                    result=_run_result(
                        reason="completed",
                        history=history,
                        new_contents=history[initial_history_length:],
                        turns=turn,
                        usage=usage,
                        plan=plan_state,
                    ),
                )
                return

            named_calls: list[
                tuple[
                    str,
                    str,
                    types.FunctionCall,
                    ToolApprovalRequest | None,
                    bool,
                ]
            ] = []
            for function_call in result.function_calls:
                name = function_call.name
                if not name:
                    raise InvalidToolCallError("模型返回的工具调用缺少名称，无法执行。")
                call_id = function_call.id or str(uuid7())
                function_call.id = call_id
                arguments = dict(function_call.args or {})
                tool = active_registry.get(name)
                blocked_by_planning = bool(
                    use_plan_mode
                    and current_phase == "planning"
                    and tool is not None
                    and not tool.read_only
                )
                requires_approval = bool(
                    not blocked_by_planning
                    and tool is not None
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
                    (
                        call_id,
                        name,
                        function_call,
                        approval_request,
                        blocked_by_planning,
                    )
                )
                yield LoopEvent(
                    type="tool.requested",
                    turn=turn,
                    phase=plan_state.phase,
                    plan=plan_state.snapshot() if plan_state.steps else None,
                    tool_call_id=call_id,
                    tool_name=name,
                    tool_arguments=arguments,
                    requires_approval=requires_approval,
                    approval_request_hash=(
                        approval_request.request_hash if approval_request else ""
                    ),
                )

            tool_parts: list[types.Part] = []
            for (
                call_id,
                name,
                function_call,
                approval_request,
                blocked_by_planning,
            ) in named_calls:
                if blocked_by_planning:
                    blocked_result = ToolResult(
                        content=(
                            f"当前处于只读规划阶段（planning），禁止直接执行修改或命令工具 `{name}`。"
                            "请先使用只读工具探索上下文，并调用 `update_plan(action='create', ...)` "
                            "创建执行计划，进入执行阶段（executing）后再执行该操作。"
                        ),
                        is_error=True,
                    )
                    yield LoopEvent(
                        type="tool.completed",
                        turn=turn,
                        phase=plan_state.phase,
                        plan=plan_state.snapshot() if plan_state.steps else None,
                        tool_call_id=call_id,
                        tool_name=name,
                        tool_arguments=dict(function_call.args or {}),
                        tool_result=blocked_result,
                    )
                    tool_parts.append(
                        tool_result_part(name, blocked_result, call_id)
                    )
                    continue

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
                        phase=plan_state.phase,
                        plan=plan_state.snapshot() if plan_state.steps else None,
                        tool_call_id=execution.call_id,
                        tool_name=execution.name,
                        tool_arguments=execution.arguments,
                        tool_result=execution.result,
                    )
                    if (
                        execution.name == "update_plan"
                        and not execution.result.is_error
                    ):
                        yield LoopEvent(
                            type="plan.updated",
                            turn=turn,
                            phase=plan_state.phase,
                            plan=plan_state.snapshot(),
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
            phase=plan_state.phase,
            plan=plan_state.snapshot() if plan_state.steps else None,
            result=_run_result(
                reason="max_turns",
                history=history,
                new_contents=history[initial_history_length:],
                turns=max_turns,
                usage=usage,
                error=max_turns_error,
                plan=plan_state,
            ),
        )
    except AgentServiceError as exc:
        yield LoopEvent(
            type="run.failed",
            turn=completed_turns,
            phase=plan_state.phase,
            plan=plan_state.snapshot() if plan_state.steps else None,
            result=_run_result(
                reason="error",
                history=history,
                new_contents=history[initial_history_length:],
                turns=completed_turns,
                usage=usage,
                error=exc,
                plan=plan_state,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - Runtime 边界必须转换未知异常
        runtime_error = AgentRuntimeError(f"Agent 运行失败：{exc}")
        yield LoopEvent(
            type="run.failed",
            turn=completed_turns,
            phase=plan_state.phase,
            plan=plan_state.snapshot() if plan_state.steps else None,
            result=_run_result(
                reason="error",
                history=history,
                new_contents=history[initial_history_length:],
                turns=completed_turns,
                usage=usage,
                error=runtime_error,
                plan=plan_state,
            ),
        )
