"""ReflectTool 反思工具的单元测试。"""

import pytest

from agent_service.core.plan import ExecutionPlan
from agent_service.tools.reflect_tool import ReflectTool


@pytest.mark.asyncio
async def test_reflect_tool_records_reflection_and_recovers_phase():
    plan = ExecutionPlan()
    plan.create_plan(
        goal="重构配置模块",
        steps=[{"id": "step-1", "title": "修改配置加载逻辑"}],
    )
    plan.enter_reflection("工具 edit_file 执行失败")
    assert plan.phase == "reflecting"

    tool = ReflectTool(plan)
    assert tool.read_only is True

    result = await tool.run(
        {
            "observation": "edit_file 返回未找到匹配的 old_string",
            "diagnosis": "目标文件包含额外的类型注解，原匹配文本过旧",
            "strategy": "retry",
            "next_action": "使用最新读取的片段重新执行 edit_file",
            "step_id": "step-1",
            "step_status": "in_progress",
        }
    )

    assert not result.is_error
    assert plan.phase == "executing"
    assert len(plan.reflections) == 1
    assert plan.reflections[0].strategy == "retry"
    assert plan.steps[0].status == "in_progress"
    assert "ref-1" in result.content
    assert "已恢复至执行阶段" in result.content


@pytest.mark.asyncio
async def test_reflect_tool_handles_invalid_parameters():
    tool = ReflectTool()

    missing_field = await tool.run(
        {
            "observation": "工具报错",
            "diagnosis": "",
            "strategy": "retry",
            "next_action": "重试",
        }
    )
    assert missing_field.is_error
    assert "diagnosis" in missing_field.content

    invalid_strategy = await tool.run(
        {
            "observation": "工具报错",
            "diagnosis": "参数错误",
            "strategy": "unknown_strategy",
            "next_action": "重试",
        }
    )
    assert invalid_strategy.is_error
    assert "反思策略" in invalid_strategy.content
