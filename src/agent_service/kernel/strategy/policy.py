from dataclasses import dataclass, field

from google.genai import types

from agent_service.kernel.context.history import has_prior_plan_state, runtime_notice
from agent_service.kernel.dispatch.base import Tool
from agent_service.kernel.dispatch.executor import ToolExecution
from agent_service.kernel.runtime.events import LoopEvent
from agent_service.kernel.strategy.plan import AgentPhase, ExecutionPlan


@dataclass
class StrategyDecision:
    """策略只提供状态变化与提醒，不驱动模型或执行工具。"""

    notice: str = ""
    events: list[LoopEvent] = field(default_factory=list)


class TaskPolicy:
    def __init__(self, available_names: frozenset[str], enabled: bool = True) -> None:
        self.use_plan = enabled and "update_plan" in available_names
        self.use_reflection = enabled and "reflect" in available_names
        self.plan = ExecutionPlan(phase="planning" if self.use_plan else "executing")
        self.completion_reminded = False
        self.reflection_reminded = False

    def start_notice(self, history: list[types.Content]) -> str:
        if not self.use_plan or not has_prior_plan_state(history[:-1]):
            return ""
        return runtime_notice(
            "新一轮任务已开始，当前阶段已重置为规划阶段（planning）。\n"
            "此前历史中的旧执行计划与阶段状态已结束，请勿沿用。\n"
            "简单问答或单步只读查询可直接回复；若需修改代码或多步排查，"
            "请先使用只读工具探索并调用 `update_plan(action='create', ...)` 创建新计划。"
        )

    def blocked_reason(self, name: str, tool: Tool, phase: AgentPhase) -> str:
        if tool.read_only:
            return ""
        if self.use_plan and phase == "planning":
            return (
                f"当前处于只读规划阶段（planning），禁止直接执行修改或命令工具 `{name}`。"
                "请先使用只读工具探索上下文，并调用 `update_plan(action='create', ...)` "
                "创建执行计划，进入执行阶段（executing）后再执行该操作。"
            )
        if self.use_reflection and phase == "reflecting":
            return (
                f"当前处于只读反思阶段（reflecting），禁止直接执行修改或命令工具 `{name}`。"
                f"待反思原因：{self.plan.pending_reflection_reason or '需要交付自检'}。"
                "请先使用只读工具检查上下文，并调用 `reflect` 工具分析根因与下一步策略"
                "（或调用 `update_plan(action='replan', ...)` 动态重规划），恢复至执行阶段后再执行该操作。"
            )
        return ""

    def before_completion(self, turn: int, max_turns: int) -> StrategyDecision:
        plan = self.plan
        if turn >= max_turns:
            return StrategyDecision()
        if (
            self.use_reflection
            and plan.phase == "reflecting"
            and not self.reflection_reminded
        ):
            self.reflection_reminded = True
            return StrategyDecision(
                notice=runtime_notice(
                    "[系统反思提醒] 当前处于反思阶段（reflecting），尚有待处理的反思事项：\n"
                    f"{plan.format_markdown()}\n\n"
                    "请先调用 `reflect` 工具提交故障根因或交付自检分析（retry/replan/verify/complete），"
                    "恢复至执行阶段并完成必要操作后再给出最终总结。"
                )
            )
        if (
            self.use_plan
            and plan.phase == "executing"
            and plan.has_unfinished_steps()
            and not self.completion_reminded
        ):
            self.completion_reminded = True
            return StrategyDecision(
                notice=runtime_notice(
                    "[系统提醒] 当前执行计划中仍有未完成的步骤：\n"
                    f"{plan.format_markdown()}\n\n"
                    "请继续执行剩余步骤，或调用 `update_plan(action='update_step', ...)` "
                    "将已完成/跳过的步骤标记为 `completed` 或 `skipped` 后再给出最终总结。"
                )
            )
        if (
            self.use_reflection
            and plan.phase == "executing"
            and plan.needs_verification()
            and not self.reflection_reminded
        ):
            self.reflection_reminded = True
            plan.enter_reflection(
                reason=f"已编辑文件 ({', '.join(plan.unverified_edits)})，但在结束任务前尚未验证。",
                trigger="verification",
            )
            return StrategyDecision(
                notice=runtime_notice(
                    "[系统反思提醒] 检测到你已修改代码文件，但在交付前尚未进行验证或终局自检：\n"
                    f"{plan.format_markdown()}\n\n"
                    "当前已切换至只读反思阶段（reflecting），修改与命令类工具已临时锁定。"
                    "请先使用 `read_file` 核对修改结果，或先反思恢复执行阶段再运行质量检查，"
                    "并调用 `reflect(strategy='complete', ...)` 完成终局自检后再给出最终总结。"
                ),
                events=[self._reflection_event(turn)],
            )
        return StrategyDecision()

    def observe(
        self, executions: list[ToolExecution], ignored_ids: set[str], turn: int
    ) -> StrategyDecision:
        """统一处理整批结果，防止计划解锁掩盖同批工具失败。"""
        decision = StrategyDecision()
        failures: list[ToolExecution] = []
        edits: list[str] = []
        for execution in executions:
            if execution.call_id in ignored_ids:
                continue
            if execution.result.is_error:
                if execution.name not in {"update_plan", "reflect"}:
                    failures.append(execution)
                continue
            if execution.name in {"update_plan", "reflect"}:
                decision.events.append(
                    LoopEvent(
                        type="plan.updated"
                        if execution.name == "update_plan"
                        else "reflection.updated",
                        turn=turn,
                        phase=self.plan.phase,
                        plan=self.plan.snapshot(),
                    )
                )
            elif execution.name in {"edit_file", "write_file"}:
                edits.append(str(execution.arguments.get("path", "")))
            elif execution.name == "read_file":
                self.plan.mark_verified(str(execution.arguments.get("path", "")))
            elif execution.name in {"run_quality_check", "run_command"}:
                self.plan.mark_verified()

        # 同批工具并发运行；声明顺序不能证明回读或检查发生在修改之后。
        for target in edits:
            self.plan.record_edit(target)

        if failures and self.use_reflection and self.plan.phase == "executing":
            first = failures[0]
            message = first.result.content.strip().splitlines()
            self.plan.enter_reflection(
                reason=f"工具 `{first.name}` 执行报错：{message[0] if message else '工具执行失败'}",
                trigger="tool_error",
            )
            decision.events.append(self._reflection_event(turn))
            names = "、".join(f"`{item.name}`" for item in failures)
            decision.notice = runtime_notice(
                f"检测到工具 {names} 执行报错，当前已切换至只读反思阶段（reflecting）。\n"
                "修改与命令类工具已临时锁定。请结合工具返回结果排查失败原因，"
                "并调用 `reflect` 工具提交故障根因与下一步策略（或调用 "
                "`update_plan(action='replan', ...)` 动态调整计划），恢复至 `executing` 阶段后再修改。"
            )
        return decision

    def _reflection_event(self, turn: int) -> LoopEvent:
        return LoopEvent(
            type="reflection.updated",
            turn=turn,
            phase=self.plan.phase,
            plan=self.plan.snapshot(),
        )
