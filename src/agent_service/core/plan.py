from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

AgentPhase = Literal["planning", "executing", "reflecting"]
PlanStepStatus = Literal["pending", "in_progress", "completed", "skipped"]
ReflectionStrategy = Literal["retry", "replan", "verify", "complete"] # 反思策略
ReflectionTrigger = Literal["tool_error", "verification", "manual"] # 触发反思原因
#  运行时合法状态元组
VALID_STEP_STATUSES: tuple[PlanStepStatus, ...] = (
    "pending", # 待执行
    "in_progress", # 进行中
    "completed", # 已完成
    "skipped", # 已跳过
)
VALID_REFLECTION_STRATEGIES: tuple[ReflectionStrategy, ...] = (
    "retry",    # 修正参数后重试
    "replan",   # 调整后续计划步骤
    "verify",   # 进一步读取或测试验证
    "complete", # 验证通过可交付
)
# 状态到 UI 展示文案的映射字典
_STATUS_BADGE: dict[PlanStepStatus, str] = {
    "pending": "⏳ [ ]",
    "in_progress": "🔄 [>]",
    "completed": "✅ [x]",
    "skipped": "⏭️ [-]",
}

_STATUS_LABEL: dict[PlanStepStatus, str] = {
    "pending": "待执行",
    "in_progress": "进行中",
    "completed": "已完成",
    "skipped": "已跳过",
}

_STRATEGY_LABEL: dict[ReflectionStrategy, str] = {
    "retry": "修正重试",
    "replan": "动态重规划",
    "verify": "补充验证",
    "complete": "验证通过",
}


@dataclass(frozen=True)
class PlanStep:
    """计划中的单个可执行步骤。"""

    id: str
    title: str
    status: PlanStepStatus = "pending"
    notes: str = ""


@dataclass(frozen=True)
class ReflectionRecord:
    """单次结构化反思记录。"""

    id: str
    observation: str # 现象观察
    diagnosis: str # 质量评估
    strategy: ReflectionStrategy # 应对策略
    next_action: str 
    trigger: ReflectionTrigger = "manual" # 触发来源 manual 主动反思
    step_id: str = "" #  针对 Plan 的步骤id


def _parse_steps(raw_steps: Sequence[dict[str, Any] | PlanStep]) -> list[PlanStep]:
    """
    把外部传入的“原始步骤数据”清洗、校验，并统一转换成标准的 PlanStep 对象列表
    """
    if not raw_steps:
        raise ValueError("计划步骤列表不能为空，至少需要包含 1 个步骤。")

    parsed: list[PlanStep] = []
    seen_ids: set[str] = set()

    for index, item in enumerate(raw_steps, start=1):
        if isinstance(item, PlanStep):
            step_id = item.id.strip()
            title = item.title.strip()
            raw_status: str = item.status
            notes = item.notes.strip()
        elif isinstance(item, dict):
            raw_id = item.get("id", f"step-{index}")
            step_id = str(raw_id).strip() if raw_id is not None else ""
            raw_title = item.get("title", "")
            title = str(raw_title).strip() if raw_title is not None else ""
            raw_status = str(item.get("status", "pending")).strip() or "pending"
            raw_notes = item.get("notes", "")
            notes = str(raw_notes).strip() if raw_notes is not None else ""
        else:
            raise TypeError(f"第 {index} 个步骤格式无效，必须为对象。")

        if not step_id:
            raise ValueError(f"第 {index} 个步骤缺少有效的 id。")
        if not title:
            raise ValueError(f"步骤 `{step_id}` 缺少有效的 title。")
        if step_id in seen_ids:
            raise ValueError(f"计划中存在重复的步骤 id：`{step_id}`。")
        if raw_status not in VALID_STEP_STATUSES:
            allowed = ", ".join(VALID_STEP_STATUSES)
            raise ValueError(
                f"步骤 `{step_id}` 的状态 `{raw_status}` 无效，允许值：{allowed}。"
            )

        seen_ids.add(step_id)
        parsed.append(
            PlanStep(
                id=step_id,
                title=title,
                status=cast(PlanStepStatus, raw_status),
                notes=notes,
            )
        )
    return parsed


@dataclass
class ExecutionPlan:
    """单次 Agent 运行的 Plan-Execute-Reflect 状态机容器。"""

    goal: str = ""  # 本次任务的总体目标（如 "修复登录接口的超时问题"）。
    steps: list[PlanStep] = field(default_factory=list)
    phase: AgentPhase = "planning"
    revision: int = 0
    replan_reason: str = ""  # 记录最近一次为什么修改计划的原因。
    reflections: list[ReflectionRecord] = field(default_factory=list) # 保存反思记录

    pending_reflection_reason: str = "" 
    pending_reflection_trigger: ReflectionTrigger = "manual"
    unverified_edits: list[str] = field(default_factory=list) # 待验证列表
    def create_plan(
        self,
        goal: str,
        steps: Sequence[dict[str, Any] | PlanStep],
    ) -> None:
        clean_goal = goal.strip() if isinstance(goal, str) else ""
        if not clean_goal:
            raise ValueError("计划目标 goal 不能为空。")
        parsed_steps = _parse_steps(steps)
        self.goal = clean_goal
        self.steps = parsed_steps
        self.phase = "executing"
        self.revision += 1
        self.replan_reason = ""
        self.pending_reflection_reason = "" 

    def update_step(
        self,
        step_id: str,
        status: str,
        notes: str = "",
    ) -> PlanStep:
        if not self.steps:
            raise ValueError("当前尚未创建执行计划，请先调用 create 创建计划。")

        clean_id = step_id.strip() if isinstance(step_id, str) else ""
        if not clean_id:
            raise ValueError("更新步骤时必须提供有效的 step_id。")

        clean_status = status.strip() if isinstance(status, str) else ""
        if clean_status not in VALID_STEP_STATUSES:
            allowed = ", ".join(VALID_STEP_STATUSES)
            raise ValueError(
                f"步骤状态 `{clean_status}` 无效，允许值：{allowed}。"
            )

        clean_notes = notes.strip() if isinstance(notes, str) else ""
        typed_status = cast(PlanStepStatus, clean_status)

        for idx, existing in enumerate(self.steps):
            if existing.id == clean_id:
                updated = PlanStep(
                    id=existing.id,
                    title=existing.title,
                    status=typed_status,
                    notes=clean_notes if clean_notes else existing.notes,
                )
                self.steps[idx] = updated
                return updated

        raise ValueError(f"未找到步骤 `{clean_id}`，请检查步骤 id 是否正确。")

    def replan(
        self,
        goal: str,
        steps: Sequence[dict[str, Any] | PlanStep],
        reason: str = "",
    ) -> None:
        """重新规划plan，若当前处于反思阶段则自动恢复至执行阶段。"""
        clean_goal = goal.strip() if isinstance(goal, str) else ""
        if not clean_goal:
            clean_goal = self.goal
        if not clean_goal:
            raise ValueError("重规划时必须提供有效的计划目标 goal。")

        parsed_steps = _parse_steps(steps)
        self.goal = clean_goal
        self.steps = parsed_steps
        self.phase = "executing"
        self.revision += 1
        self.replan_reason = reason.strip() if isinstance(reason, str) else ""
        self.pending_reflection_reason = ""

    def enter_reflection(
        self,
        reason: str,
        *,
        trigger: ReflectionTrigger = "tool_error",
    ) -> None:
        """切入反思阶段（reflecting），锁定写工具直到完成反思或重规划。"""
        clean_reason = reason.strip() if isinstance(reason, str) else ""
        if not clean_reason:
            clean_reason = "执行过程出现异常或需要交付前验证自检。"
        self.phase = "reflecting"
        self.pending_reflection_reason = clean_reason
        self.pending_reflection_trigger = trigger

    def record_reflection(
        self,
        observation: str,
        diagnosis: str,
        strategy: str,
        next_action: str,
        *,
        step_id: str = "",
        step_status: str = "",
        trigger: ReflectionTrigger | None = None,
    ) -> ReflectionRecord:
        """记录一次结构化反思，并在计划存在时恢复至 executing 阶段。"""

        clean_obs = observation.strip() if isinstance(observation, str) else ""
        if not clean_obs:
            raise ValueError("反思记录的 observation（现象观察）不能为空。")

        clean_diag = diagnosis.strip() if isinstance(diagnosis, str) else ""
        if not clean_diag:
            raise ValueError("反思记录的 diagnosis（根因诊断/质量评估）不能为空。")

        clean_strategy = strategy.strip() if isinstance(strategy, str) else ""
        if clean_strategy not in VALID_REFLECTION_STRATEGIES:
            allowed = ", ".join(VALID_REFLECTION_STRATEGIES)
            raise ValueError(
                f"反思策略 `{clean_strategy}` 无效，允许值：{allowed}。"
            )

        clean_next = next_action.strip() if isinstance(next_action, str) else ""
        if not clean_next:
            raise ValueError("反思记录的 next_action（下一步行动）不能为空。")

        clean_step_id = step_id.strip() if isinstance(step_id, str) else ""
        clean_step_status = (
            step_status.strip() if isinstance(step_status, str) else ""
        )
        if clean_step_id and clean_step_status:
            if not self.steps:
                raise ValueError("当前尚未创建执行计划，无法在反思中更新步骤状态。")
            self.update_step(
                step_id=clean_step_id,
                status=clean_step_status,
                notes=clean_diag,
            )
        # 推断触发源 active_trigger 并生成记录
        active_trigger: ReflectionTrigger = (
            trigger
            if trigger is not None
            else (
                self.pending_reflection_trigger
                if self.pending_reflection_reason
                else "manual"
            )
        )
        typed_strategy = cast(ReflectionStrategy, clean_strategy)
        record = ReflectionRecord(
            id=f"ref-{len(self.reflections) + 1}",
            observation=clean_obs,
            diagnosis=clean_diag,
            strategy=typed_strategy,
            next_action=clean_next,
            trigger=active_trigger,
            step_id=clean_step_id,
        )
        self.reflections.append(record)
        self.pending_reflection_reason = ""
        self.pending_reflection_trigger = "manual"
        if typed_strategy == "complete":
            self.unverified_edits.clear()
        self.phase = "executing" if self.steps else "planning"
        return record

    def record_edit(self, target: str) -> None:
        """记录修改后的还没验证的文件。"""
        clean_target = target.strip() if isinstance(target, str) else ""
        label = clean_target or "<unknown>"
        if label not in self.unverified_edits:
            self.unverified_edits.append(label)

    def mark_verified(self) -> None:
        """标记之前的代码编辑已完成读取确认或测试验证。"""
        self.unverified_edits.clear()

    def needs_verification(self) -> bool:
        """是否存在尚未验证的代码编辑操作。"""
        return bool(self.unverified_edits)

    def has_state(self) -> bool:
        """执行计划是否有内容"""
        return bool(
            self.steps or self.reflections or self.pending_reflection_reason
        )

    def has_unfinished_steps(self) -> bool:
        return bool(self.steps) and any(
            step.status in {"pending", "in_progress"} for step in self.steps
        )

    def is_completed(self) -> bool:
        return bool(self.steps) and not self.has_unfinished_steps()

    def completed_count(self) -> int:
        """
        统计当前计划中“已经走完的步骤数量”
        """
        return sum(
            1 for step in self.steps if step.status in {"completed", "skipped"}
        )

    def snapshot(self) -> ExecutionPlan:
        return ExecutionPlan(
            goal=self.goal,
            steps=list(self.steps),
            phase=self.phase,
            revision=self.revision,
            replan_reason=self.replan_reason,
            reflections=list(self.reflections),
            pending_reflection_reason=self.pending_reflection_reason,
            pending_reflection_trigger=self.pending_reflection_trigger,
            unverified_edits=list(self.unverified_edits),
        )

    def format_markdown(self) -> str:
        if not self.has_state():
            return "当前尚未创建执行计划（阶段：规划探索中）。"

        lines: list[str] = []
        if self.steps:
            done = self.completed_count()
            total = len(self.steps)
            lines.extend(
                [
                    f"### 📋 执行计划（v{self.revision} | 进度 {done}/{total}）",
                    f"**目标**：{self.goal}",
                ]
            )
            if self.replan_reason:
                lines.append(f"**最新重规划原因**：{self.replan_reason}")
            lines.append("")
            for step in self.steps:
                badge = _STATUS_BADGE[step.status]
                label = _STATUS_LABEL[step.status]
                line = f"- {badge} `{step.id}` **{step.title}**（{label}）"
                if step.notes:
                    line += f" — _{step.notes}_"
                lines.append(line)

        if self.phase == "reflecting" and self.pending_reflection_reason:
            if lines:
                lines.append("")
            lines.extend(
                [
                    "### ⚠️ 待反思事项（当前处于反思阶段，写工具已锁定）",
                    f"- **触发原因**：{self.pending_reflection_reason}",
                ]
            )

        if self.reflections:
            latest = self.reflections[-1]
            strategy_label = _STRATEGY_LABEL[latest.strategy]
            if lines:
                lines.append("")
            lines.extend(
                [
                    f"### 🤔 反思记录（共 {len(self.reflections)} 次 | 最新 `{latest.id}`）",
                    f"- **现象观察**：{latest.observation}",
                    f"- **根因/评估**：{latest.diagnosis}",
                    f"- **应对策略**：{strategy_label}（`{latest.strategy}`）— {latest.next_action}",
                ]
            )

        return "\n".join(lines)
