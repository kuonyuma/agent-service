from typing import Any, ClassVar

from agent_service.core.plan import ExecutionPlan
from agent_service.tools.base import Tool, ToolResult


class ReflectTool(Tool):
    """对工具执行故障、计划受阻或任务交付质量进行结构化反思的内置工具。"""

    name = "reflect"
    description = (
        "提交结构化反思（Reflection），用于故障根因诊断与交付前质量自检。"
        "当工具执行报错进入反思阶段（reflecting）时，必须先调用此工具分析原因并明确下一步策略"
        "（retry/replan/verify/complete），恢复至执行阶段后再继续操作；"
        "在完成代码修改准备结束任务前，也可调用此工具进行终局交付审查。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "observation": {
                "type": "STRING",
                "description": "客观观察到的现象：工具报错详情、测试输出或当前代码修改结果",
            },
            "diagnosis": {
                "type": "STRING",
                "description": "根因诊断或质量评估：分析失败的具体原因，或评估当前修改是否完整、安全、符合要求",
            },
            "strategy": {
                "type": "STRING",
                "description": (
                    "下一步应对策略：'retry'（修正参数后重试）、"
                    "'replan'（需要调整后续计划步骤）、"
                    "'verify'（继续使用只读或测试工具验证）、"
                    "'complete'（验证通过，准备输出最终总结）"
                ),
            },
            "next_action": {
                "type": "STRING",
                "description": "下一步将执行的具体动作说明",
            },
            "step_id": {
                "type": "STRING",
                "description": "关联的计划步骤 ID（可选，如 'step-1'）",
            },
            "step_status": {
                "type": "STRING",
                "description": "可选：同步更新关联步骤的状态（pending / in_progress / completed / skipped）",
            },
        },
        "required": ["observation", "diagnosis", "strategy", "next_action"],
    }
    read_only = True

    def __init__(self, plan: ExecutionPlan | None = None) -> None:
        self.plan = plan if plan is not None else ExecutionPlan()

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        observation = parameter.get("observation", "")
        diagnosis = parameter.get("diagnosis", "")
        strategy = parameter.get("strategy", "")
        next_action = parameter.get("next_action", "")
        step_id = parameter.get("step_id", "")
        step_status = parameter.get("step_status", "")

        try:
            record = self.plan.record_reflection(
                observation=str(observation) if observation is not None else "",
                diagnosis=str(diagnosis) if diagnosis is not None else "",
                strategy=str(strategy) if strategy is not None else "",
                next_action=str(next_action) if next_action is not None else "",
                step_id=str(step_id) if step_id is not None else "",
                step_status=str(step_status) if step_status is not None else "",
            )
            phase_hint = (
                "已恢复至执行阶段（executing），读写与命令工具已重新解锁。"
                if self.plan.phase == "executing"
                else "当前处于规划阶段（planning），请先调用 update_plan 创建执行计划。"
            )
            return ToolResult(
                content=(
                    f"已记录反思 `{record.id}`（策略：`{record.strategy}`）。{phase_hint}\n\n"
                    f"{self.plan.format_markdown()}"
                ),
                is_error=False,
            )
        except (ValueError, TypeError) as exc:
            return ToolResult(content=f"反思记录失败：{exc}", is_error=True)
