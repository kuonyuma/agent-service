from typing import Any, ClassVar

from agent_service.core.plan import ExecutionPlan
from agent_service.tools.base import Tool, ToolResult


class UpdatePlanTool(Tool):
    """管理 Plan-and-Execute 计划创建、步骤状态更新与动态重规划的内置工具。"""

    name = "update_plan"
    description = (
        "管理当前任务的执行计划（Plan-and-Execute）。"
        "在规划阶段完成代码探索后，调用 action='create' 创建步骤计划并进入执行阶段；"
        "在执行阶段调用 action='update_step' 更新步骤状态（in_progress/completed/skipped），"
        "或调用 action='replan' 动态调整步骤计划。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "操作类型：'create'（创建初始计划）、'update_step'（更新步骤状态）、'replan'（动态重规划）",
            },
            "goal": {
                "type": "STRING",
                "description": "任务总体目标（action 为 create 或 replan 时使用）",
            },
            "steps": {
                "type": "ARRAY",
                "description": "完整步骤列表（action 为 create 或 replan 时必填）",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "id": {
                            "type": "STRING",
                            "description": "唯一步骤标识，如 'step-1'",
                        },
                        "title": {
                            "type": "STRING",
                            "description": "步骤具体目标与操作描述",
                        },
                        "status": {
                            "type": "STRING",
                            "description": "步骤状态：pending / in_progress / completed / skipped",
                        },
                        "notes": {
                            "type": "STRING",
                            "description": "步骤备注或结果摘要",
                        },
                    },
                    "required": ["id", "title"],
                },
            },
            "step_id": {
                "type": "STRING",
                "description": "要更新的步骤 ID（action 为 update_step 时必填）",
            },
            "status": {
                "type": "STRING",
                "description": "目标状态：pending / in_progress / completed / skipped（action 为 update_step 时必填）",
            },
            "notes": {
                "type": "STRING",
                "description": "步骤执行备注或结果说明（可选）",
            },
            "reason": {
                "type": "STRING",
                "description": "触发动态重规划的原因说明（action 为 replan 时可选）",
            },
        },
        "required": ["action"],
    }
    read_only = True

    def __init__(self, plan: ExecutionPlan | None = None) -> None:
        self.plan = plan if plan is not None else ExecutionPlan()

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        raw_action = parameter.get("action", "")
        action = raw_action.strip() if isinstance(raw_action, str) else ""

        try:
            if action == "create":
                goal = parameter.get("goal", "")
                steps = parameter.get("steps", [])
                if not isinstance(steps, list):
                    return ToolResult(
                        content="计划操作失败：steps 必须是步骤对象列表。",
                        is_error=True,
                    )
                self.plan.create_plan(goal=str(goal), steps=steps)
                return ToolResult(
                    content=(
                        "计划已创建，已切换至执行阶段（executing），现在可以按步骤调用读写与执行工具。\n\n"
                        f"{self.plan.format_markdown()}"
                    ),
                    is_error=False,
                )

            if action == "update_step":
                step_id = parameter.get("step_id", "")
                status = parameter.get("status", "")
                notes = parameter.get("notes", "")
                updated = self.plan.update_step(
                    step_id=str(step_id),
                    status=str(status),
                    notes=str(notes) if notes is not None else "",
                )
                return ToolResult(
                    content=(
                        f"已更新步骤 `{updated.id}` 状态为 `{updated.status}`。\n\n"
                        f"{self.plan.format_markdown()}"
                    ),
                    is_error=False,
                )

            if action == "replan":
                goal = parameter.get("goal", "") or self.plan.goal
                steps = parameter.get("steps", [])
                reason = parameter.get("reason", "")
                if not isinstance(steps, list):
                    return ToolResult(
                        content="计划操作失败：steps 必须是步骤对象列表。",
                        is_error=True,
                    )
                self.plan.replan(
                    goal=str(goal),
                    steps=steps,
                    reason=str(reason) if reason is not None else "",
                )
                return ToolResult(
                    content=(
                        f"已完成动态重规划（版本 v{self.plan.revision}）。\n\n"
                        f"{self.plan.format_markdown()}"
                    ),
                    is_error=False,
                )

            return ToolResult(
                content=(
                    f"未知的计划操作 action='{action}'，"
                    "支持的操作为：'create'、'update_step'、'replan'。"
                ),
                is_error=True,
            )
        except (ValueError, TypeError) as exc:
            return ToolResult(content=f"计划操作失败：{exc}", is_error=True)
