"""UpdatePlanTool 规划工具的单元测试。"""

import pytest

from agent_service.core.plan import ExecutionPlan
from agent_service.tools.plan_tool import UpdatePlanTool


@pytest.mark.asyncio
async def test_update_plan_tool_create_update_and_replan():
    plan = ExecutionPlan()
    tool = UpdatePlanTool(plan)
    assert tool.read_only is True

    created = await tool.run(
        {
            "action": "create",
            "goal": "重构日志模块",
            "steps": [
                {"id": "step-1", "title": "检查现有日志格式"},
                {"id": "step-2", "title": "更新格式化函数"},
            ],
        }
    )
    assert not created.is_error
    assert plan.phase == "executing"
    assert "step-1" in created.content

    updated = await tool.run(
        {
            "action": "update_step",
            "step_id": "step-1",
            "status": "completed",
            "notes": "已确认 JSON 日志字段",
        }
    )
    assert not updated.is_error
    assert plan.steps[0].status == "completed"
    assert "已确认 JSON 日志字段" in updated.content

    replanned = await tool.run(
        {
            "action": "replan",
            "goal": "重构日志模块并补充测试",
            "steps": [
                {"id": "step-1", "title": "检查现有日志格式", "status": "completed"},
                {"id": "step-2", "title": "更新格式化函数", "status": "completed"},
                {"id": "step-3", "title": "运行 pytest 验证", "status": "pending"},
            ],
            "reason": "增加回归测试步骤",
        }
    )
    assert not replanned.is_error
    assert plan.revision == 2
    assert len(plan.steps) == 3
    assert "v2" in replanned.content


@pytest.mark.asyncio
async def test_update_plan_tool_handles_invalid_parameters():
    tool = UpdatePlanTool()

    unknown = await tool.run({"action": "delete"})
    assert unknown.is_error
    assert "未知的计划操作" in unknown.content

    invalid_steps = await tool.run(
        {"action": "create", "goal": "目标", "steps": "not-a-list"}
    )
    assert invalid_steps.is_error
    assert "必须是步骤对象列表" in invalid_steps.content

    missing_plan = await tool.run(
        {"action": "update_step", "step_id": "step-1", "status": "completed"}
    )
    assert missing_plan.is_error
    assert "尚未创建执行计划" in missing_plan.content
