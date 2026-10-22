"""ExecutionPlan 状态机的单元测试。"""

import pytest

from agent_service.core.plan import ExecutionPlan, PlanStep


def test_execution_plan_lifecycle_and_completion():
    plan = ExecutionPlan()
    assert plan.phase == "planning"
    assert not plan.has_unfinished_steps()
    assert not plan.is_completed()

    plan.create_plan(
        goal="修复配置解析问题",
        steps=[
            {"id": "step-1", "title": "阅读配置文件"},
            {"id": "step-2", "title": "修改解析逻辑"},
        ],
    )
    assert plan.phase == "executing"
    assert plan.revision == 1
    assert len(plan.steps) == 2
    assert plan.has_unfinished_steps()
    assert not plan.is_completed()

    plan.update_step("step-1", "in_progress", notes="正在读取")
    assert plan.steps[0].status == "in_progress"
    assert plan.steps[0].notes == "正在读取"

    plan.update_step("step-1", "completed")
    assert plan.steps[0].status == "completed"
    # 未传新 notes 时保留原 notes
    assert plan.steps[0].notes == "正在读取"
    assert plan.has_unfinished_steps()

    plan.update_step("step-2", "skipped", notes="无需修改")
    assert plan.is_completed()
    assert plan.completed_count() == 2


def test_execution_plan_replan_increments_revision():
    plan = ExecutionPlan()
    plan.create_plan(
        goal="初始目标",
        steps=[PlanStep(id="step-1", title="步骤一")],
    )
    assert plan.revision == 1

    plan.replan(
        goal="调整后的目标",
        steps=[
            {"id": "step-1", "title": "步骤一", "status": "completed"},
            {"id": "step-2", "title": "新增验证步骤", "status": "pending"},
        ],
        reason="发现需要补充单元测试验证",
    )
    assert plan.revision == 2
    assert plan.goal == "调整后的目标"
    assert plan.replan_reason == "发现需要补充单元测试验证"
    assert plan.has_unfinished_steps()
    markdown = plan.format_markdown()
    assert "v2" in markdown
    assert "发现需要补充单元测试验证" in markdown


def test_execution_plan_rejects_invalid_inputs():
    plan = ExecutionPlan()

    with pytest.raises(ValueError, match="goal 不能为空"):
        plan.create_plan(goal="   ", steps=[{"id": "s1", "title": "步骤"}])

    with pytest.raises(ValueError, match="不能为空"):
        plan.create_plan(goal="目标", steps=[])

    with pytest.raises(ValueError, match="重复的步骤 id"):
        plan.create_plan(
            goal="目标",
            steps=[
                {"id": "step-1", "title": "一"},
                {"id": "step-1", "title": "二"},
            ],
        )

    with pytest.raises(ValueError, match="尚未创建执行计划"):
        plan.update_step("step-1", "completed")

    plan.create_plan(goal="目标", steps=[{"id": "step-1", "title": "一"}])

    with pytest.raises(ValueError, match="无效"):
        plan.update_step("step-1", "invalid_status")

    with pytest.raises(ValueError, match="未找到步骤"):
        plan.update_step("step-99", "completed")


def test_execution_plan_reflection_and_verification_lifecycle():
    plan = ExecutionPlan()
    plan.create_plan(
        goal="修复文件编辑问题",
        steps=[{"id": "step-1", "title": "修改目标文件"}],
    )
    assert plan.phase == "executing"

    # 进入反思阶段
    plan.enter_reflection("工具 edit_file 匹配失败", trigger="tool_error")
    assert plan.phase == "reflecting"
    assert plan.pending_reflection_reason == "工具 edit_file 匹配失败"
    assert "待反思事项" in plan.format_markdown()

    # 提交结构化反思并同步更新步骤状态，恢复至 executing
    record = plan.record_reflection(
        observation="old_string 在文件中未找到",
        diagnosis="未先读取文件确认真实缩进",
        strategy="retry",
        next_action="先调用 read_file 查看内容后重新调用 edit_file",
        step_id="step-1",
        step_status="in_progress",
    )
    assert record.id == "ref-1"
    assert record.trigger == "tool_error"
    assert plan.phase == "executing"
    assert plan.pending_reflection_reason == ""
    assert plan.steps[0].status == "in_progress"
    assert plan.steps[0].notes == "未先读取文件确认真实缩进"

    # 记录未验证编辑与终局自检核销
    plan.record_edit("src/app.py")
    assert plan.needs_verification()
    plan.record_reflection(
        observation="已核对修改后的代码",
        diagnosis="改动符合预期且无副作用",
        strategy="complete",
        next_action="输出最终总结",
    )
    assert not plan.needs_verification()
    markdown = plan.format_markdown()
    assert "反思记录" in markdown
    assert "ref-2" in markdown


def test_execution_plan_reflection_rejects_invalid_inputs():
    plan = ExecutionPlan()

    with pytest.raises(ValueError, match="observation"):
        plan.record_reflection(
            observation="  ",
            diagnosis="原因",
            strategy="retry",
            next_action="重试",
        )

    with pytest.raises(ValueError, match="diagnosis"):
        plan.record_reflection(
            observation="现象",
            diagnosis="",
            strategy="retry",
            next_action="重试",
        )

    with pytest.raises(ValueError, match="反思策略"):
        plan.record_reflection(
            observation="现象",
            diagnosis="原因",
            strategy="abort",
            next_action="放弃",
        )

    with pytest.raises(ValueError, match="next_action"):
        plan.record_reflection(
            observation="现象",
            diagnosis="原因",
            strategy="retry",
            next_action=" ",
        )
