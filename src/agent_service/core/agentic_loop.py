import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import aclosing
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Literal
from uuid import uuid7

from google.genai import types

from agent_service.client.stream_message import StreamResult, stream_message
from agent_service.config.settings import settings
from agent_service.core.control import RunControl
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
        """生成一份“工具执行申请单”。

        当模型想执行危险操作（比如改文件、跑命令）时，先把“调哪个工具、传了什么参数”
        打包成一份申请单，并算出一个唯一特征码，方便后面找用户确认时核对内容有没有被改动。

        参数:
            call_id: 这次工具调用的编号。
            tool_name: 要调用的工具名字，比如 "write_file"。
            arguments: 调用工具时传入的参数，比如文件路径和内容。

        返回:
            打包好的申请单对象（ToolApprovalRequest）。
        """
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
    "safety.blocked",
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
    safety_usage: dict[str, int] = field(default_factory=dict)


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
    safety_code: str = ""
    safety_category: str = ""


def _usage_value(usage: dict[str, Any], key: str) -> int:
    """从 token 统计字典里安全地取出对应的数字。

    参数:
        usage: 存放 token 消耗量的字典。
        key: 想取的字段名，比如 "input_len"（输入量）或 "output_len"（输出量）。

    返回:
        如果里面有这个字段且是整数，就返回对应的数字；如果不存在或格式不对，就返回 0。
    """
    value = usage.get(key, 0)
    return value if isinstance(value, int) else 0


def _merged_usage(
    usage: dict[str, int],
    subagent_manager: SubagentManager | None,
) -> dict[str, int]:
    """把主 Agent 和所有子 Agent 消耗的 token 数量加在一起。

    参数:
        usage: 主 Agent 自己消耗的 token 统计。
        subagent_manager: 子 Agent 管家，里面记着所有子 Agent 消耗的 token；
            如果没有用到子 Agent，这里就是 None。

    返回:
        汇总后的 token 字典，包含总输入量（input_len）、总输出量（output_len），
        以及有缓存命中时的缓存量（cached_len）。
    """
    if subagent_manager is None:
        return dict(usage)
    merged: dict[str, int] = {
        "input_len": usage.get("input_len", 0)
        + subagent_manager.total_usage.get("input_len", 0),
        "output_len": usage.get("output_len", 0)
        + subagent_manager.total_usage.get("output_len", 0),
    }
    if "cached_len" in usage or "cached_len" in subagent_manager.total_usage:
        merged["cached_len"] = usage.get(
            "cached_len", 0
        ) + subagent_manager.total_usage.get("cached_len", 0)
    return merged


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
    """在整次任务结束时，把最终运行结果打包成一个结果对象。

    参数:
        reason: 为什么结束。"completed" 表示正常完成，"max_turns" 表示跑满最大轮数也没做完，
            "error" 表示中途出错停止。
        history: 任务结束时的完整聊天记录（包含之前的旧记录和这次新增的记录）。
        new_contents: 仅在这次运行中新增的聊天记录。
        turns: 这次一共跟大模型来回对话了多少轮。
        usage: 这次任务总共消耗的 token 数量。
        error: 如果是因为出错结束，这里放具体的错误对象；正常结束时为 None。
        plan: 当前的任务计划表；如果这次任务建过计划或做过反思，就保存一份副本，否则为 None。

    返回:
        打包好的最终运行结果（LoopResult）。
    """
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
    """挑出所有“只读、不改东西”的安全工具。

    在刚开始做计划（planning）或出错后做反思（reflecting）时，不能让模型改文件或跑命令，
    所以用这个函数把能改东西的工具过滤掉，只留下看文件、改计划、写反思这类安全工具。

    参数:
        tools: 原来准备提供给模型的全部工具说明列表。
        registry: 工具箱字典（名字 -> 工具对象），用来查每个工具是不是只读工具。

    返回:
        过滤后只包含只读工具的列表，直接拿去发给模型。
    """

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


def _build_runtime_notice(body: str) -> str:
    """给系统提示语套上 `<runtime-notice>` 标签。

    套上这个标签是为了告诉大模型：“这段话是程序自动发给你的状态提醒，不是用户说的话。”

    参数:
        body: 要提醒大模型的文字内容。

    返回:
        去掉首尾多余空格、并包好 `<runtime-notice>...</runtime-notice>` 标签的字符串。
    """

    return f"<runtime-notice>\n{body.strip()}\n</runtime-notice>"


def _append_user_notice(
    history: list[types.Content],
    notice_text: str,
) -> None:
    """把一条系统状态提醒塞到聊天记录的最后面。

    大模型的聊天记录要求“用户一句、模型一句”交替出现。如果最后一条本来就是用户消息，
    就直接把提醒拼进这条消息里；如果不是，就在末尾新建一条用户消息放进去。
    注意：这个函数会直接修改传入的 history 列表。

    参数:
        history: 当前的聊天记录列表。
        notice_text: 已经包好标签的提醒文字。

    返回:
        None（直接改在 history 里，不额外返回值）。
    """

    notice_part = types.Part.from_text(text=notice_text)
    if history and history[-1].role == "user":
        existing_parts = list(history[-1].parts or [])
        history[-1] = types.Content(
            role="user",
            parts=[*existing_parts, notice_part],
        )
    else:
        history.append(types.Content(role="user", parts=[notice_part]))


def _history_has_prior_plan_state(
    prior_contents: list[types.Content],
) -> bool:
    """看一眼之前的聊天记录里，有没有留下“上一个任务做过计划或反思”的痕迹。

    比如用户聊了第二个新问题，但历史记录里还留着第一个问题的计划。
    这个函数只负责检查历史、不会改动任何内容。如果查出来有旧痕迹，
    外面的代码就会提醒大模型：“旧计划已经翻篇了，新任务请重新开始。”

    参数:
        prior_contents: 新问题之前的旧聊天记录列表。

    返回:
        True 表示找到了旧计划、旧反思、系统提醒或历史压缩摘要的痕迹；
        False 表示干干净净，没有这些旧痕迹。
    """

    for item in prior_contents:
        for part in item.parts or []:
            if part.function_call is not None and part.function_call.name in {
                "update_plan",
                "reflect",
            }:
                return True
            if (
                part.function_response is not None
                and part.function_response.name in {"update_plan", "reflect"}
            ):
                return True
            if part.text and (
                "<runtime-notice>" in part.text
                or "[系统上下文注入 - 以下是之前对话的压缩摘要" in part.text
            ):
                return True
    return False


def _build_error_reflection_notice(failed_tools: list[str]) -> str:
    """根据报错的工具名字，拼出一段“请停下来反思原因”的提醒文字。

    注意：这个函数不负责检查工具成没成功，外面的代码发现工具报错后，
    会把失败的工具名字传进来，由这个函数生成提醒话术。
    为了安全，这里只写工具名字，不把工具报错的原始内容直接拼进去。

    参数:
        failed_tools: 这一轮执行失败的工具名字列表，比如 ["edit_file"]。

    返回:
        包好 `<runtime-notice>` 标签的反思提醒字符串。
    """

    joined = "、".join(f"`{name}`" for name in failed_tools)
    return _build_runtime_notice(
        f"检测到工具 {joined} 执行报错，当前已从执行阶段（executing）切换至只读反思阶段（reflecting）。\n"
        "修改与命令类工具已临时锁定。\n"
        "请结合上方工具返回结果排查失败原因（必要时使用只读工具检查上下文），"
        "并调用 `reflect` 工具提交故障根因与下一步策略（或调用 `update_plan(action='replan', ...)` 动态调整计划），"
        "恢复至 `executing` 阶段后再执行修改操作。"
    )


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
    run_control: RunControl | None = None,
) -> AsyncGenerator[LoopEvent]:
    """让 Agent 真正跑起来的核心循环：不断“问大模型 -> 调工具 -> 把结果喂回大模型”，直到任务完成。

    你可以把它理解成 Agent 的发动机。它会一轮一轮跟大模型对话，中间每发生一件事
    （比如模型打出一段话、申请调工具、工具跑完了、计划更新了、任务结束了），
    都会立刻往外抛出一个事件（LoopEvent），方便界面或接口实时展示进度。
    传入的原始聊天记录 contents 不会被改动。

    参数:
        contents: 开始时的聊天记录列表（最后一条通常是用户刚提的问题）。
        tools: 允许大模型看到的工具说明列表。
        permission_check: 当模型想执行改文件、跑命令等危险工具时，用来找人确认的函数；
            返回 True 才放行，如果不传这个函数，所有需要确认的工具都会被拒绝。
        allowed_tool_names: 允许使用的工具白名单；传 None 表示不额外限制。
        tool_registry: 工具箱字典（名字 -> 具体工具实现）；传 None 时使用默认的全套工具。
        system_prompt: 给大模型设定的系统人设和规则说明；传 None 时使用默认配置。
        max_turns: 最多允许大模型来回折腾几轮，默认 10 轮，超过还没做完就强制停下。
        plan_and_execute: 是否开启“先定计划、再执行、出错先反思”的管控流程，默认开启（True）。
        graceful_final_turn: 到了最后一轮时，是否收走所有工具、强迫模型直接写总结报告，
            默认 False（常用于子 Agent）。
        inbox: 实时消息信箱；如果运行中途有人往信箱塞了新指令，下一轮对话前会自动带给大模型。
        subagent_manager: 管理子 Agent 的管家；如果不传，函数会在需要时自己建一个，并在结束时清理掉。

    返回:
        一个可以不断拿到运行事件的异步结果流（每次拿到的都是一个 LoopEvent，
        最后一条固定是表示成功的 "run.completed" 或表示失败的 "run.failed"）。
    """

    history = list(contents)
    initial_history_length = len(history)
    usage = {"input_len": 0, "output_len": 0}
    completed_turns = 0
    if run_control is not None:
        run_control.ensure_running()
        run_control.capture(history, [], 0, usage)

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
        """把子 Agent 运行时产生的事件转交给主循环，并等主循环处理完再继续。

        比如子 Agent 想改文件、需要找用户审批时，得先把这个请求递到外面的主循环去处理。

        参数:
            sub_event: 子 Agent 传出来的运行事件。

        返回:
            None。
        """
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
                run_control=run_control,
            )
            owns_subagent_manager = True
        base_registry["subagent"] = SubagentTool(active_subagent_manager)

    active_registry: ToolRegistry = base_registry# 拿到新的基础工具

    completion_reminder_sent = False
    reflection_reminder_sent = False
    if (
        use_plan_mode
        and len(history) > 1
        and _history_has_prior_plan_state(history[:-1])
    ):
        _append_user_notice(
            history,
            _build_runtime_notice(
                "新一轮任务已开始，当前阶段已重置为规划阶段（planning）。\n"
                "此前历史中的旧执行计划与阶段状态已结束，请勿沿用。\n"
                "针对本次新任务：简单问答或单步只读查询可直接回复；"
                "若需修改代码或多步排查，请先使用只读工具探索并调用 `update_plan(action='create', ...)` 创建新计划。"
            ),
        )

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
                _append_user_notice(
                    history,
                    _build_runtime_notice(
                        "## 最终轮总结指令（Final Turn）\n"
                        "当前已到达子代理最大运行步数，工具调用已关闭。"
                        "请立即基于目前已掌握的上下文、证据与已完成的操作，输出完整、精炼的最终总结报告。"
                    ),
                )
            else:
                turn_tools = ( # 如果处于 
                    _filter_planning_tools(tools, active_registry)  
                    if (
                        (use_plan_mode and current_phase == "planning")
                        or (use_reflection_mode and current_phase == "reflecting")
                    )
                    else tools
                )

            result: StreamResult | None = None
            if run_control is not None:
                run_control.ensure_running()
            async with aclosing(stream_message(
                contents=history,
                tools=turn_tools,
                system_prompt=base_system_prompt,
                max_tokens=settings.model_config.max_tokens,
            )) as model_stream:
                async for event in model_stream:
                    if event.type == "text":
                        yield LoopEvent(
                            type="text.delta", text=event.text, turn=turn,
                            phase=plan_state.phase,
                            plan=plan_state.snapshot() if plan_state.has_state() else None,
                        )
                    elif event.type == "message_done":
                        result = event.result

            if result is None:
                raise AgentRuntimeError("Agent 循环没有收到模型的完成结果。")

            usage["input_len"] += _usage_value(result.usage, "input_len")
            usage["output_len"] += _usage_value(result.usage, "output_len")
            if "cached_len" in result.usage:
                usage["cached_len"] = usage.get(
                    "cached_len", 0
                ) + _usage_value(result.usage, "cached_len")
            history.append(types.Content(role="model", parts=result.raw_parts))
            if run_control is not None:
                run_control.turns = turn
                run_control.usage = _merged_usage(usage, active_subagent_manager)
                if not result.function_calls:
                    run_control.capture(
                        history, history[initial_history_length:], turn,
                        _merged_usage(usage, active_subagent_manager),
                    )

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
                                    text=_build_runtime_notice(
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
                                    text=_build_runtime_notice(
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
                                    text=_build_runtime_notice(
                                        "[系统反思提醒] 检测到你已修改代码文件，但在交付前尚未进行验证或终局自检：\n"
                                        f"{plan_state.format_markdown()}\n\n"
                                        "当前已切换至只读反思阶段（reflecting），修改与命令类工具已临时锁定。\n"
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
                        run_control=run_control,
                    )
                )
                queue_get_task = None
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
                    if queue_get_task is not None and not queue_get_task.done():
                        queue_get_task.cancel()
                        await asyncio.gather(queue_get_task, return_exceptions=True)
                    if not exec_task.done():
                        exec_task.cancel()
                        try:
                            await exec_task
                        except asyncio.CancelledError:
                            pass
                executions_by_id = {
                    ex.call_id: ex for ex in batch.executions
                }

            has_batch_phase_unlock = any(
                ex.name in {"update_plan", "reflect"} and not ex.result.is_error
                for ex in executions_by_id.values()
            )
            failed_reflection_tools: list[str] = []
            deferred_failed_executions: list[ToolExecution] = []
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
                    and execution.name not in {"update_plan", "reflect"}
                    and not denied_by_user
                ):
                    if has_batch_phase_unlock:
                        deferred_failed_executions.append(execution)
                    elif plan_state.phase == "executing":
                        raw_err = execution.result.content.strip()
                        first_line = (
                            raw_err.splitlines()[0]
                            if raw_err
                            else "工具执行失败"
                        )
                        plan_state.enter_reflection(
                            reason=f"工具 `{execution.name}` 执行报错：{first_line}",
                            trigger="tool_error",
                        )
                        failed_reflection_tools.append(execution.name)
                        yield LoopEvent(
                            type="reflection.updated",
                            turn=turn,
                            phase=plan_state.phase,
                            plan=plan_state.snapshot(),
                        )
                    elif (
                        plan_state.phase == "reflecting"
                        and failed_reflection_tools
                    ):
                        failed_reflection_tools.append(execution.name)
                tool_parts.append(
                    tool_result_part(
                        execution.name,
                        execution.result,
                        execution.call_id,
                    )
                )

            if deferred_failed_executions and plan_state.phase == "executing":
                first_failed = deferred_failed_executions[0]
                raw_err = first_failed.result.content.strip()
                first_line = (
                    raw_err.splitlines()[0] if raw_err else "工具执行失败"
                )
                plan_state.enter_reflection(
                    reason=f"工具 `{first_failed.name}` 执行报错：{first_line}",
                    trigger="tool_error",
                )
                failed_reflection_tools.extend(
                    ex.name for ex in deferred_failed_executions
                )
                yield LoopEvent(
                    type="reflection.updated",
                    turn=turn,
                    phase=plan_state.phase,
                    plan=plan_state.snapshot(),
                )

            if failed_reflection_tools and plan_state.phase == "reflecting":
                tool_parts.append(
                    types.Part.from_text(
                        text=_build_error_reflection_notice(
                            failed_reflection_tools
                        )
                    )
                )

            history.append(types.Content(role="user", parts=tool_parts))
            if run_control is not None:
                run_control.capture(
                    history, history[initial_history_length:], turn,
                    _merged_usage(usage, active_subagent_manager),
                )

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
