import asyncio
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
from agent_service.core.subagent import SubagentManager
from agent_service.errors import (
    AgentRuntimeError,
    AgentServiceError,
    InvalidToolCallError,
)
from agent_service.tools.base import Tool, ToolResult
from agent_service.tools.executor import ToolExecution, execute_tools, tool_result_part
from agent_service.tools.index import TOOL_REGISTRY
from agent_service.tools.plan_tool import UpdatePlanTool
from agent_service.tools.reflect_tool import ReflectTool
from agent_service.tools.subagent_tool import SubagentTool

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
    "reflection.updated",
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


def _merged_usage(
    usage: dict[str, int],
    subagent_manager: SubagentManager | None,
) -> dict[str, int]:
    if subagent_manager is None:
        return dict(usage)
    return {
        "input_len": usage.get("input_len", 0)
        + subagent_manager.total_usage.get("input_len", 0),
        "output_len": usage.get("output_len", 0)
        + subagent_manager.total_usage.get("output_len", 0),
    }


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
        plan=plan.snapshot() if plan is not None and plan.has_state() else None,
    )


def _filter_planning_tools( # 工具过滤器
    tools: list[types.Tool],
    registry: ToolRegistry,
) -> list[types.Tool]:
    """在只读阶段（planning / reflecting）仅向模型暴露 read_only 工具（含 update_plan 与 reflect）。"""

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
    """在执行或反思阶段将最新计划与反思状态注入系统提示词。"""

    if not plan_state.has_state():
        return base_prompt
    prompt = f"{base_prompt}\n\n## 当前执行计划状态\n{plan_state.format_markdown()}"
    if plan_state.phase == "reflecting":
        prompt += (
            "\n\n## 反思阶段指令（Reflecting）\n"
            "当前处于只读反思阶段，修改与命令类工具已临时锁定。"
            "请先使用只读工具检查上下文（如需），并调用 `reflect` 工具提交故障根因或交付自检分析"
            "（或调用 `update_plan(action='replan', ...)` 动态调整计划），恢复至 `executing` 阶段后再执行修改操作。"
        )
    return prompt


async def query(
    contents: list[types.Content],
    tools: list[types.Tool],
    permission_check: PermissionCheck | None = None,
    allowed_tool_names: frozenset[str] | None = None,
    tool_registry: ToolRegistry | None = None,
    system_prompt: str | None = None,
    max_turns: int = 10,
    plan_and_execute: bool = True,
    graceful_final_turn: bool = False,
    inbox: asyncio.Queue[str] | None = None,
    subagent_manager: SubagentManager | None = None,
) -> AsyncGenerator[LoopEvent]:
    """运行 Plan-and-Execute Agent 循环，不修改调用方传入的历史列表。"""

    history = list(contents)
    initial_history_length = len(history)
    usage = {"input_len": 0, "output_len": 0}
    completed_turns = 0

    base_registry = (# 准备基础工具
        dict(tool_registry) if tool_registry is not None else dict(TOOL_REGISTRY)
    )
    use_plan_mode = ( # 启动plan
        plan_and_execute
        and "update_plan" in base_registry
        and (allowed_tool_names is None or "update_plan" in allowed_tool_names)
    )
    use_reflection_mode = (
        plan_and_execute
        and "reflect" in base_registry
        and (allowed_tool_names is None or "reflect" in allowed_tool_names)
    )
    plan_state = ExecutionPlan( # 创建状态机
        phase="planning" if use_plan_mode else "executing"
    )

    if "update_plan" in base_registry: # 将状态机的使用权交给工具函数
        base_registry["update_plan"] = UpdatePlanTool(plan_state)
    if "reflect" in base_registry:
        base_registry["reflect"] = ReflectTool(plan_state)

    base_system_prompt = (
        system_prompt if system_prompt is not None else settings.system_prompt
    )

    forwarded_events: asyncio.Queue[tuple[LoopEvent, asyncio.Event] | None] = (
        asyncio.Queue()
    )

    async def _forward_subagent_event(sub_event: LoopEvent) -> None:
        ack = asyncio.Event()
        await forwarded_events.put((sub_event, ack))
        await ack.wait()

    active_subagent_manager: SubagentManager | None = subagent_manager
    owns_subagent_manager = False
    if "subagent" in base_registry:
        if active_subagent_manager is None:
            active_subagent_manager = SubagentManager(
                base_registry,
                parent_allowed_tool_names=allowed_tool_names,
                parent_system_prompt=base_system_prompt,
                parent_plan=plan_state,
                permission_check=permission_check,
                event_forwarder=_forward_subagent_event,
                query_runner=query,
            )
            owns_subagent_manager = True
        base_registry["subagent"] = SubagentTool(active_subagent_manager)

    active_registry: ToolRegistry = base_registry# 拿到新的基础工具

    completion_reminder_sent = False
    reflection_reminder_sent = False
    try:
        for turn in range(1, max_turns + 1):
            completed_turns = turn
            if inbox is not None and not inbox.empty():
                inbox_msgs: list[str] = []
                while not inbox.empty():
                    try:
                        raw_msg = inbox.get_nowait()
                        if isinstance(raw_msg, str) and raw_msg.strip():
                            inbox_msgs.append(raw_msg.strip())
                    except asyncio.QueueEmpty:
                        break
                if inbox_msgs:
                    injected_text = "\n\n".join(
                        f"[实时通信指令] {msg}" for msg in inbox_msgs
                    )
                    injected_part = types.Part.from_text(text=injected_text)
                    if history and history[-1].role == "user":
                        existing_parts = list(history[-1].parts or [])
                        history[-1] = types.Content(
                            role="user",
                            parts=[*existing_parts, injected_part],
                        )
                    else:
                        history.append(
                            types.Content(role="user", parts=[injected_part])
                        )

            current_phase = plan_state.phase # 如果处于planing or reflection阶段就过滤工具只提供read-lonly
            is_graceful_last_turn = (
                graceful_final_turn and max_turns > 1 and turn == max_turns
            )
            if is_graceful_last_turn:
                turn_tools: list[types.Tool] = []
            else:
                turn_tools = ( # 如果处于 
                    _filter_planning_tools(tools, active_registry)  
                    if (
                        (use_plan_mode and current_phase == "planning")
                        or (use_reflection_mode and current_phase == "reflecting")
                    )
                    else tools
                )
            turn_system_prompt = _build_turn_system_prompt( #构建新的系统提示词
                base_system_prompt,
                plan_state,
            )
            if is_graceful_last_turn:
                turn_system_prompt += (
                    "\n\n## 最终轮总结指令（Final Turn）\n"
                    "当前已到达子代理最大运行步数，工具调用已关闭。"
                    "请立即基于目前已掌握的上下文、证据与已完成的操作，输出完整、精炼的最终总结报告。"
                )

            result: StreamResult | None = None
            async for event in stream_message( # 请求llm
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
                        phase=plan_state.phase, # 交代当前的状态
                        plan=( # 返回copy状态机信息
                            plan_state.snapshot()
                            if plan_state.has_state()
                            else None
                        ),
                    )
                elif event.type == "message_done":
                    result = event.result

            if result is None:
                raise AgentRuntimeError("Agent 循环没有收到模型的完成结果。")

            usage["input_len"] += _usage_value(result.usage, "input_len")
            usage["output_len"] += _usage_value(result.usage, "output_len")
            history.append(types.Content(role="model", parts=result.raw_parts))

            if not result.function_calls: # 模型打算结束这次任务
                if ( # 如果使用反思功能且处于反思状态，并且还未纠正过，且还可以循环，就触发反思提醒
                    use_reflection_mode
                    and plan_state.phase == "reflecting"
                    and not reflection_reminder_sent
                    and turn < max_turns
                ):
                    reflection_reminder_sent = True
                    history.append(
                        types.Content(
                            role="user",
                            parts=[
                                types.Part.from_text(
                                    text=(
                                        "[系统反思提醒] 当前处于反思阶段（reflecting），尚有待处理的反思事项：\n"
                                        f"{plan_state.format_markdown()}\n\n"
                                        "请先调用 `reflect` 工具提交故障根因或交付自检分析（retry/replan/verify/complete），"
                                        "恢复至执行阶段并完成必要操作后再给出最终总结。"
                                    )
                                )
                            ],
                        )
                    )
                    continue

                if ( # 防止大模型提前提交没有完成的状态
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

                if (
                    use_reflection_mode
                    and plan_state.phase == "executing"
                    and plan_state.needs_verification()
                    and not reflection_reminder_sent
                    and turn < max_turns
                ):
                    reflection_reminder_sent = True
                    edited_targets = ", ".join(plan_state.unverified_edits)
                    plan_state.enter_reflection(
                        reason=(
                            f"已编辑文件 ({edited_targets})，但在结束任务前尚未读取确认、"
                            "运行质量检查或提交终局交付反思。"
                        ),
                        trigger="verification",
                    )
                    yield LoopEvent(
                        type="reflection.updated",
                        turn=turn,
                        phase=plan_state.phase,
                        plan=plan_state.snapshot(),
                    )
                    history.append(
                        types.Content(
                            role="user",
                            parts=[
                                types.Part.from_text(
                                    text=(
                                        "[系统反思提醒] 检测到你已修改代码文件，但在交付前尚未进行验证或终局自检：\n"
                                        f"{plan_state.format_markdown()}\n\n"
                                        "请先使用 `read_file` 或质量检查工具核对修改结果，并调用 "
                                        "`reflect(strategy='complete', ...)` 完成终局自检后再给出最终总结。"
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
                    plan=plan_state.snapshot() if plan_state.has_state() else None,
                    result=_run_result(
                        reason="completed",
                        history=history,
                        new_contents=history[initial_history_length:],
                        turns=turn,
                        usage=_merged_usage(usage, active_subagent_manager),
                        plan=plan_state,
                    ),
                )
                return #一次agent运行结束

            named_calls: list[ # 待执行工具的名单
                tuple[
                    str,
                    str,
                    types.FunctionCall,
                    ToolApprovalRequest | None,
                    Literal["planning", "reflecting"] | None,
                ]
            ] = []

            for function_call in result.function_calls:
                name = function_call.name
                if not name:
                    raise InvalidToolCallError("模型返回的工具调用缺少名称，无法执行。")
                call_id = function_call.id or str(uuid7())
                function_call.id = call_id
                arguments = dict(function_call.args or {})
                tool = active_registry.get(name) # 拿到工具实例
                blocked_phase: Literal["planning", "reflecting"] | None = None
                if tool is not None and not tool.read_only:
                    if use_plan_mode and current_phase == "planning":
                        blocked_phase = "planning"
                    elif use_reflection_mode and current_phase == "reflecting":
                        blocked_phase = "reflecting"
                requires_approval = bool(
                    blocked_phase is None
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
                        blocked_phase,
                    )
                )
                yield LoopEvent(
                    type="tool.requested",
                    turn=turn,
                    phase=plan_state.phase,
                    plan=plan_state.snapshot() if plan_state.has_state() else None,
                    tool_call_id=call_id,
                    tool_name=name,
                    tool_arguments=arguments,
                    requires_approval=requires_approval,
                    approval_request_hash=(
                        approval_request.request_hash if approval_request else ""
                    ),
                )

            approved_call_ids: set[str] = set()
            denied_call_ids: set[str] = set()
            executable_calls: list[types.FunctionCall] = []
            for (
                call_id,
                _name,
                function_call,
                approval_request,
                blocked_phase,
            ) in named_calls:
                if blocked_phase is not None:
                    continue
                if approval_request is not None:
                    allowed = (
                        await permission_check(approval_request)
                        if permission_check is not None
                        else False
                    )
                    if allowed:
                        approved_call_ids.add(call_id)
                    else:
                        denied_call_ids.add(call_id)
                executable_calls.append(function_call)

            executions_by_id: dict[str, ToolExecution] = {}
            if executable_calls:
                exec_task = asyncio.create_task(
                    execute_tools(
                        executable_calls,
                        allowed_tool_names=allowed_tool_names,
                        approved_tool_call_ids=frozenset(approved_call_ids),
                        tool_registry=active_registry,
                    )
                )
                try:
                    while not exec_task.done():
                        queue_get_task = asyncio.create_task(
                            forwarded_events.get()
                        )
                        done, _ = await asyncio.wait(
                            {exec_task, queue_get_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if queue_get_task in done:
                            item = queue_get_task.result()
                            if item is not None:
                                sub_event, ack = item
                                yield sub_event
                                ack.set()
                        else:
                            queue_get_task.cancel()
                            try:
                                await queue_get_task
                            except asyncio.CancelledError:
                                pass
                    while not forwarded_events.empty():
                        item = forwarded_events.get_nowait()
                        if item is not None:
                            sub_event, ack = item
                            yield sub_event
                            ack.set()
                    batch = await exec_task
                finally:
                    if not exec_task.done():
                        exec_task.cancel()
                        try:
                            await exec_task
                        except asyncio.CancelledError:
                            pass
                executions_by_id = {
                    ex.call_id: ex for ex in batch.executions
                }

            tool_parts: list[types.Part] = []
            for (
                call_id,
                name,
                function_call,
                _approval_request,
                blocked_phase,
            ) in named_calls: # 开始遍历可调用工具
                if blocked_phase is not None:
                    if blocked_phase == "planning":
                        blocked_msg = (
                            f"当前处于只读规划阶段（planning），禁止直接执行修改或命令工具 `{name}`。"
                            "请先使用只读工具探索上下文，并调用 `update_plan(action='create', ...)` "
                            "创建执行计划，进入执行阶段（executing）后再执行该操作。"
                        )
                    else:
                        reason_text = (
                            plan_state.pending_reflection_reason
                            or "前序操作出现异常或需要交付自检"
                        )
                        blocked_msg = (
                            f"当前处于只读反思阶段（reflecting），禁止直接执行修改或命令工具 `{name}`。"
                            f"待反思原因：{reason_text}。"
                            "请先使用只读工具检查上下文，并调用 `reflect` 工具分析根因与下一步策略"
                            "（或调用 `update_plan(action='replan', ...)` 动态重规划），"
                            "恢复至执行阶段（executing）后再执行该操作。"
                        )
                    blocked_result = ToolResult(
                        content=blocked_msg,
                        is_error=True,
                    )
                    yield LoopEvent(
                        type="tool.completed",
                        turn=turn,
                        phase=plan_state.phase,
                        plan=plan_state.snapshot() if plan_state.has_state() else None,
                        tool_call_id=call_id,
                        tool_name=name,
                        tool_arguments=dict(function_call.args or {}),
                        tool_result=blocked_result,
                    )
                    tool_parts.append(
                        tool_result_part(name, blocked_result, call_id)
                    )
                    continue

                execution = executions_by_id.get(call_id)
                if execution is None:
                    continue
                denied_by_user = call_id in denied_call_ids
                yield LoopEvent(
                    type="tool.completed",
                    turn=turn,
                    phase=plan_state.phase,
                    plan=plan_state.snapshot() if plan_state.has_state() else None,
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
                elif (
                    execution.name == "reflect"
                    and not execution.result.is_error
                ):
                    yield LoopEvent(
                        type="reflection.updated",
                        turn=turn,
                        phase=plan_state.phase,
                        plan=plan_state.snapshot(),
                    )
                elif not execution.result.is_error:
                    if execution.name == "edit_file":
                        plan_state.record_edit(
                            str(execution.arguments.get("path", ""))
                        )
                    elif execution.name in {
                        "read_file",
                        "quality_check",
                        "run_command",
                    }:
                        plan_state.mark_verified()
                elif (
                    use_reflection_mode
                    and plan_state.phase == "executing"
                    and execution.name not in {"update_plan", "reflect"}
                    and not denied_by_user
                ):
                    raw_err = execution.result.content.strip()
                    first_line = (
                        raw_err.splitlines()[0] if raw_err else "工具执行失败"
                    )
                    plan_state.enter_reflection(
                        reason=f"工具 `{execution.name}` 执行报错：{first_line}",
                        trigger="tool_error",
                    )
                    yield LoopEvent(
                        type="reflection.updated",
                        turn=turn,
                        phase=plan_state.phase,
                        plan=plan_state.snapshot(),
                    )
                tool_parts.append(
                    tool_result_part(
                        execution.name,
                        execution.result,
                        execution.call_id,
                    )
                )

            history.append(types.Content(role="user", parts=tool_parts))

        max_turns_error = AgentRuntimeError(
            f"Agent 已达到最大运行轮数 {max_turns}，任务仍未完成。"
        )
        yield LoopEvent(
            type="run.failed",
            turn=max_turns,
            phase=plan_state.phase,
            plan=plan_state.snapshot() if plan_state.has_state() else None,
            result=_run_result(
                reason="max_turns",
                history=history,
                new_contents=history[initial_history_length:],
                turns=max_turns,
                usage=_merged_usage(usage, active_subagent_manager),
                error=max_turns_error,
                plan=plan_state,
            ),
        )
    except AgentServiceError as exc:
        yield LoopEvent(
            type="run.failed",
            turn=completed_turns,
            phase=plan_state.phase,
            plan=plan_state.snapshot() if plan_state.has_state() else None,
            result=_run_result(
                reason="error",
                history=history,
                new_contents=history[initial_history_length:],
                turns=completed_turns,
                usage=_merged_usage(usage, active_subagent_manager),
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
            plan=plan_state.snapshot() if plan_state.has_state() else None,
            result=_run_result(
                reason="error",
                history=history,
                new_contents=history[initial_history_length:],
                turns=completed_turns,
                usage=_merged_usage(usage, active_subagent_manager),
                error=runtime_error,
                plan=plan_state,
            ),
        )
    finally:
        if owns_subagent_manager and active_subagent_manager is not None:
            await active_subagent_manager.close()
