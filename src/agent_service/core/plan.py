from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

AgentPhase = Literal["planning", "executing"]
PlanStepStatus = Literal["pending", "in_progress", "completed", "skipped"]
#  运行时合法状态元组
VALID_STEP_STATUSES: tuple[PlanStepStatus, ...] = (
    "pending", # 待执行
    "in_progress", # 进行中
    "completed", # 已完成
    "skipped", # 已跳过
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


@dataclass(frozen=True)
class PlanStep:
    """计划中的单个可执行步骤。"""

    id: str
    title: str
    status: PlanStepStatus = "pending"
    notes: str = ""


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
    """单次 Agent 运行的 Plan-and-Execute 状态机容器。"""

    goal: str = "" # 本次任务的总体目标（如 "修复登录接口的超时问题"）。
    steps: list[PlanStep] = field(default_factory=list)
    phase: AgentPhase = "planning"
    revision: int = 0
    replan_reason: str = ""  # 记录最近一次为什么修改计划的原因。

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
        """重新规划plan"""
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
        )

    def format_markdown(self) -> str:
        if not self.steps:
            return "当前尚未创建执行计划（阶段：规划探索中）。"

        done = self.completed_count()
        total = len(self.steps)
        lines = [
            f"### 📋 执行计划（v{self.revision} | 进度 {done}/{total}）",
            f"**目标**：{self.goal}",
        ]
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
        return "\n".join(lines)
