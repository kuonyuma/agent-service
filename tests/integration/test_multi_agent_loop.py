"""Multi-Agent 协同工作流、并发执行、审批穿透与优雅收尾的集成测试。"""

import asyncio

import pytest
from google.genai import types

import agent_service.core.agentic_loop
from agent_service.client.stream_message import StreamEvent, StreamResult
from agent_service.core.agentic_loop import query
from agent_service.tools.index import get_function_declarations


def message_done(
    *,
    contents: str = "",
    function_calls: list[types.FunctionCall] | None = None,
    raw_parts: list[types.Part] | None = None,
    usage: dict[str, int] | None = None,
) -> StreamEvent:
    parts = (
        raw_parts
        if raw_parts is not None
        else ([types.Part.from_text(text=contents)] if contents else [])
    )
    return StreamEvent(
        type="message_done",
        result=StreamResult(
            contents=contents,
            stop_reason="STOP",
            usage=usage or {"input_len": 10, "output_len": 5},
            function_calls=function_calls or [],
            raw_parts=parts,
        ),
    )


@pytest.mark.asyncio
async def test_concurrent_explorers_and_worker_handoff_in_full_loop(
    monkeypatch,
    tmp_path,
):
    """验证主 Agent 在规划期通过 asyncio.gather 并发派发两个 explorer，
    创建计划后派发 worker（通过 context_refs 接收两个 explorer 的证据并修改代码），
    且子代理修改代码同样触发审批穿透与使用量累计。
    """

    file_a = tmp_path / "service_a.py"
    file_b = tmp_path / "service_b.py"
    file_a.write_text("PORT = 8000\n", encoding="utf-8")
    file_b.write_text("TIMEOUT = 30\n", encoding="utf-8")

    active_explorers = 0
    max_concurrent_explorers = 0
    approval_requests: list[tuple[str, dict]] = []

    async def fake_stream_message(*args, **kwargs):
        system_prompt: str = kwargs.get("system_prompt") or ""
        contents: list[types.Content] = kwargs.get("contents") or []
        last_user_parts = contents[-1].parts or []
        last_text = "".join(p.text for p in last_user_parts if p.text)
        has_tool_response = any(
            p.function_response is not None for p in last_user_parts
        )

        # 1. Explorer 子代理分支
        if "子代理角色：Explorer" in system_prompt:
            nonlocal active_explorers, max_concurrent_explorers
            if not has_tool_response:
                active_explorers += 1
                max_concurrent_explorers = max(
                    max_concurrent_explorers, active_explorers
                )
                await asyncio.sleep(0.03)
                active_explorers -= 1
                target_file = file_a if "service_a" in last_text else file_b
                yield message_done(
                    function_calls=[
                        types.FunctionCall(
                            name="read_file",
                            args={"path": str(target_file)},
                        )
                    ]
                )
            else:
                fr = last_user_parts[0].function_response
                assert fr is not None
                read_out = (fr.response or {}).get("result", "")
                yield message_done(
                    contents=f"取证完毕：{read_out.strip()}",
                )
            return

        # 2. Worker 子代理分支
        if "子代理角色：Worker" in system_prompt:
            if not has_tool_response:
                # 验证 context_refs 已自动注入两个 explorer 的报告
                assert "PORT = 8000" in last_text
                assert "TIMEOUT = 30" in last_text
                yield message_done(
                    function_calls=[
                        types.FunctionCall(
                            name="edit_file",
                            args={
                                "path": str(file_a),
                                "old_string": "PORT = 8000",
                                "new_string": "PORT = 9000",
                            },
                        )
                    ]
                )
            elif len(contents) == 3:
                yield message_done(
                    function_calls=[
                        types.FunctionCall(
                            name="read_file",
                            args={"path": str(file_a)},
                        )
                    ]
                )
            else:
                yield message_done(
                    contents="Worker 已将 PORT 修改为 9000 并完成读取验证。",
                )
            return

        # 3. 主 Agent 分支
        turn_count = sum(1 for c in contents if c.role == "model") + 1
        if turn_count == 1:
            # 规划阶段：同时并发派发 2 个 explorer + 创建计划
            yield message_done(
                function_calls=[
                    types.FunctionCall(
                        name="subagent",
                        args={
                            "role": "explorer",
                            "description": "检查 service_a",
                            "task": "读取并分析 service_a",
                        },
                    ),
                    types.FunctionCall(
                        name="subagent",
                        args={
                            "role": "explorer",
                            "description": "检查 service_b",
                            "task": "读取并分析 service_b",
                        },
                    ),
                    types.FunctionCall(
                        name="update_plan",
                        args={
                            "action": "create",
                            "goal": "更新服务端口配置",
                            "steps": [
                                {"id": "step-1", "title": "委派 worker 更新端口"}
                            ],
                        },
                    ),
                ]
            )
        elif turn_count == 2:
            # 执行阶段：派发 worker 并通过 context_refs 零损耗引用 explorer-1 和 explorer-2
            yield message_done(
                function_calls=[
                    types.FunctionCall(
                        name="subagent",
                        args={
                            "role": "worker",
                            "description": "修改端口",
                            "task": "请根据前置报告修改端口配置",
                            "context_refs": ["explorer-1", "explorer-2"],
                        },
                    ),
                    types.FunctionCall(
                        name="update_plan",
                        args={
                            "action": "update_step",
                            "step_id": "step-1",
                            "status": "completed",
                        },
                    ),
                ]
            )
        else:
            yield message_done(contents="主控已完成所有多代理协同任务。")

    monkeypatch.setattr(
        agent_service.core.agentic_loop,
        "stream_message",
        fake_stream_message,
    )

    async def allow(request):
        approval_requests.append((request.tool_name, request.arguments))
        return True

    initial = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text="请并发探索并修改端口")],
        )
    ]
    events = [
        event
        async for event in query(
            initial,
            tools=get_function_declarations(),
            permission_check=allow,
        )
    ]

    # 验证两个 explorer 确实通过 asyncio.gather 并发执行
    assert max_concurrent_explorers == 2
    # 验证 worker 内部的 edit_file 触发了父级审批回调且已成功修改文件
    assert len(approval_requests) == 1
    assert approval_requests[0][0] == "edit_file"
    assert file_a.read_text(encoding="utf-8") == "PORT = 9000\n"

    # 验证子代理内部审批事件也实时转发到了主事件流
    forwarded_edit_requests = [
        e
        for e in events
        if e.type == "tool.requested" and e.tool_name == "edit_file"
    ]
    assert len(forwarded_edit_requests) == 1
    assert forwarded_edit_requests[0].requires_approval is True

    assert events[-1].type == "run.completed"
    assert events[-1].result is not None
    # 主代理 3 轮 + explorer-1 (2 轮) + explorer-2 (2 轮) + worker-1 (3 轮) = 共 10 次模型请求
    assert events[-1].result.usage["input_len"] == 100
    assert events[-1].result.usage["output_len"] == 50


@pytest.mark.asyncio
async def test_subagent_graceful_final_turn_strips_tools_and_summarizes(
    monkeypatch,
):
    """验证子代理到达 max_turns 最后一轮时自动剥离工具声明，强制模型输出文本总结而非报错。"""

    tools_by_turn: list[list[types.Tool]] = []

    async def fake_stream_message(*args, **kwargs):
        tools_arg = kwargs.get("tools") or []
        tools_by_turn.append(tools_arg)
        system_prompt: str = kwargs.get("system_prompt") or ""

        if "最终轮总结指令" in system_prompt:
            assert tools_arg == []
            yield message_done(
                contents="已达最大步数，输出阶段性探索总结报告。"
            )
        else:
            yield message_done(
                function_calls=[
                    types.FunctionCall(
                        name="list_files",
                        args={"path": "."},
                    )
                ]
            )

    monkeypatch.setattr(
        agent_service.core.agentic_loop,
        "stream_message",
        fake_stream_message,
    )

    initial = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text="持续探索")],
        )
    ]
    events = [
        event
        async for event in query(
            initial,
            tools=get_function_declarations(),
            max_turns=3,
            plan_and_execute=False,
            graceful_final_turn=True,
        )
    ]

    assert len(tools_by_turn) == 3
    assert len(tools_by_turn[0]) > 0
    assert len(tools_by_turn[1]) > 0
    assert tools_by_turn[2] == []
    assert events[-1].type == "run.completed"
    assert events[-1].result is not None
    assert events[-1].result.reason == "completed"


@pytest.mark.asyncio
async def test_concurrent_workers_editing_same_file_are_serialized_by_file_lock(
    tmp_path,
):
    """验证两个并发 worker 同时调用 EditFileTool 修改同一文件的不同位置时，
    文件互斥锁保证读-改-写原子串行，两处修改均完整保留、不发生覆盖丢失。
    """

    from agent_service.tools.edit_file import EditFileTool

    shared_file = tmp_path / "shared.py"
    shared_file.write_text("A = 1\nB = 2\n", encoding="utf-8")
    edit_tool = EditFileTool()

    res_a, res_b = await asyncio.gather(
        edit_tool.run(
            {
                "path": str(shared_file),
                "old_string": "A = 1",
                "new_string": "A = 100",
            }
        ),
        edit_tool.run(
            {
                "path": str(shared_file),
                "old_string": "B = 2",
                "new_string": "B = 200",
            }
        ),
    )

    assert not res_a.is_error
    assert not res_b.is_error
    assert shared_file.read_text(encoding="utf-8") == "A = 100\nB = 200\n"
