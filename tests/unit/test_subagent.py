"""Multi-Agent 角色配置、权限衰减、子代理管理器与 SubagentTool 的单元测试。"""

import asyncio

import pytest
from google.genai import types

from agent_service.core.agentic_loop import LoopEvent, LoopResult
from agent_service.core.plan import ExecutionPlan
from agent_service.core.subagent import (
    ROLE_PROFILES,
    SubagentManager,
    resolve_subagent_tools,
)
from agent_service.tools.index import TOOL_REGISTRY
from agent_service.tools.quality_check import QualityCheckTool
from agent_service.tools.subagent_tool import SubagentTool
from agent_service.tools.workspace import WorkspacePathPolicy


def test_role_profiles_enforce_max_depth_and_tool_scoping(tmp_path):
    full_registry = {
        **TOOL_REGISTRY,
        "quality_check": QualityCheckTool(WorkspacePathPolicy(tmp_path)),
    }

    for role_name, profile in ROLE_PROFILES.items():
        child_reg, allowed_names, child_tools = resolve_subagent_tools(
            profile,
            full_registry,
        )
        # 任何子代理都绝不能包含 subagent 工具（强制 max_depth = 1）
        assert "subagent" not in child_reg, f"{role_name} 不应包含 subagent"
        assert "subagent" not in allowed_names
        declared = {
            decl.name
            for group in child_tools
            for decl in (group.function_declarations or [])
            if decl.name
        }
        assert "subagent" not in declared

    # explorer 仅包含只读探索工具
    _, explorer_names, _ = resolve_subagent_tools(
        ROLE_PROFILES["explorer"],
        full_registry,
    )
    assert explorer_names == frozenset(
        {
            "list_files",
            "read_file",
            "load_yaml",
            "web_search",
            "fetch_web_page",
        }
    )

    # worker 包含读写与检查工具，不包含规划/反思内部工具
    _, worker_names, _ = resolve_subagent_tools(
        ROLE_PROFILES["worker"],
        full_registry,
    )
    assert worker_names == frozenset(
        {
            "list_files",
            "read_file",
            "write_file",
            "edit_file",
            "quality_check",
            "run_command",
        }
    )

    # monitor 包含只读与检查/命令工具，不包含写文件工具
    _, monitor_names, _ = resolve_subagent_tools(
        ROLE_PROFILES["monitor"],
        full_registry,
    )
    assert monitor_names == frozenset(
        {"list_files", "read_file", "quality_check", "run_command"}
    )
    assert "write_file" not in monitor_names
    assert "edit_file" not in monitor_names

    # default 包含除 subagent 外的全部父级工具（含 update_plan 与 reflect）
    _, default_names, _ = resolve_subagent_tools(
        ROLE_PROFILES["default"],
        full_registry,
    )
    assert "update_plan" in default_names
    assert "reflect" in default_names
    assert "write_file" in default_names


def test_privilege_attenuation_never_exceeds_parent_allowlist():
    parent_allowed = frozenset({"list_files", "read_file"})

    _, worker_names, _ = resolve_subagent_tools(
        ROLE_PROFILES["worker"],
        TOOL_REGISTRY,
        parent_allowed_tool_names=parent_allowed,
    )
    # 父代理未开放 write_file/edit_file 时，worker 子代理也绝不能越权获得
    assert worker_names == frozenset({"list_files", "read_file"})


@pytest.mark.asyncio
async def test_subagent_tool_blocks_non_readonly_roles_in_planning_and_reflecting():
    plan = ExecutionPlan(phase="planning")

    async def fake_query(**kwargs):
        contents = kwargs["contents"]
        reply = types.Content(
            role="model",
            parts=[types.Part.from_text(text="已完成只读探索")],
        )
        yield LoopEvent(
            type="run.completed",
            turn=1,
            result=LoopResult(
                reason="completed",
                history=[*contents, reply],
                new_contents=[reply],
                turns=1,
                usage={"input_len": 10, "output_len": 5},
            ),
        )

    manager = SubagentManager(
        TOOL_REGISTRY,
        parent_plan=plan,
        query_runner=fake_query,
    )
    tool = SubagentTool(manager)

    # planning 阶段调用 explorer 成功
    ok_res = await tool.run(
        {"role": "explorer", "task": "查看目录结构", "description": "探索目录"}
    )
    assert not ok_res.is_error
    assert "explorer-1" in ok_res.content

    # planning 阶段调用 worker / monitor / default 被拦截
    for blocked_role in ("worker", "monitor", "default"):
        err_res = await tool.run(
            {"role": blocked_role, "task": "尝试在规划期执行"}
        )
        assert err_res.is_error
        assert "只读阶段" in err_res.content

    # 进入 executing 阶段后解锁 worker
    plan.create_plan("测试目标", [{"id": "step-1", "title": "修改代码"}])
    worker_res = await tool.run(
        {"role": "worker", "task": "执行修改任务"}
    )
    assert not worker_res.is_error
    assert "worker-1" in worker_res.content


@pytest.mark.asyncio
async def test_subagent_context_refs_and_session_resumption():
    recorded_histories: list[list[types.Content]] = []

    async def fake_query(**kwargs):
        contents = list(kwargs["contents"])
        recorded_histories.append(contents)
        last_user_text = contents[-1].parts[0].text or ""
        if "寻找根因" in last_user_text:
            reply_text = "定位到 src/auth.py 第 42 行 token 校验缺失"
        elif "继续补充单元测试" in last_user_text:
            reply_text = "已在上一轮修复基础上补充测试"
        else:
            reply_text = f"收到前置信息并完成修复：{last_user_text}"
        reply = types.Content(
            role="model",
            parts=[types.Part.from_text(text=reply_text)],
        )
        yield LoopEvent(
            type="run.completed",
            turn=1,
            result=LoopResult(
                reason="completed",
                history=[*contents, reply],
                new_contents=[reply],
                turns=1,
                usage={"input_len": 20, "output_len": 10},
            ),
        )

    manager = SubagentManager(TOOL_REGISTRY, query_runner=fake_query)
    tool = SubagentTool(manager)

    # 1. 先派发 explorer-1
    exp_res = await tool.run({"role": "explorer", "task": "寻找根因"})
    assert not exp_res.is_error
    assert "explorer-1" in exp_res.content

    # 2. 派发 worker-1 并通过 context_refs 引用 explorer-1
    work_res = await tool.run(
        {
            "role": "worker",
            "task": "修复 token 校验",
            "context_refs": ["explorer-1"],
        }
    )
    assert not work_res.is_error
    assert "src/auth.py 第 42 行" in work_res.content

    # 3. 续接 worker-1 会话（保留上一轮历史记忆）
    resume_res = await tool.run(
        {
            "action": "send",
            "agent_id": "worker-1",
            "task": "继续补充单元测试",
        }
    )
    assert not resume_res.is_error
    # 第三次调用时，worker-1 的初始 contents 应包含第一轮 user + model 以及本轮新的 user（共 3 条）
    assert len(recorded_histories[2]) == 3
    assert manager.sessions["worker-1"].turns_used == 2
    assert manager.total_usage == {"input_len": 60, "output_len": 30}


@pytest.mark.asyncio
async def test_background_subagent_inbox_and_wait():
    gate = asyncio.Event()
    received_inbox_prompts: list[str] = []

    async def fake_query(**kwargs):
        inbox: asyncio.Queue[str] | None = kwargs.get("inbox")
        contents = list(kwargs["contents"])
        # 等待外部通过 action="send" 投递实时消息
        await gate.wait()
        if inbox is not None and not inbox.empty():
            received_inbox_prompts.append(inbox.get_nowait())
        reply = types.Content(
            role="model",
            parts=[types.Part.from_text(text="后台监控已完成并处理实时指令")],
        )
        yield LoopEvent(
            type="run.completed",
            turn=2,
            result=LoopResult(
                reason="completed",
                history=[*contents, reply],
                new_contents=[reply],
                turns=2,
                usage={"input_len": 15, "output_len": 8},
            ),
        )

    manager = SubagentManager(TOOL_REGISTRY, query_runner=fake_query)
    tool = SubagentTool(manager)

    # 后台启动 monitor-1
    spawn_res = await tool.run(
        {
            "role": "monitor",
            "task": "监控集成测试进程",
            "background": True,
        }
    )
    assert not spawn_res.is_error
    assert "monitor-1" in spawn_res.content
    assert manager.sessions["monitor-1"].status == "running"

    # 查看列表
    list_res = await tool.run({"action": "list"})
    assert "monitor-1" in list_res.content
    assert "running" in list_res.content

    # 向正在后台运行的 monitor-1 发送实时指令（不阻塞等待）
    send_res = await tool.run(
        {
            "action": "send",
            "agent_id": "monitor-1",
            "task": "重点关注超时日志",
            "background": True,
        }
    )
    assert not send_res.is_error
    assert "收件箱" in send_res.content

    # 放行后台任务并调用 wait 收集结果
    gate.set()
    wait_res = await tool.run(
        {"action": "wait", "agent_ids": ["monitor-1"]}
    )
    assert not wait_res.is_error
    assert "后台监控已完成并处理实时指令" in wait_res.content
    assert received_inbox_prompts == ["重点关注超时日志"]
    assert manager.sessions["monitor-1"].status == "completed"
