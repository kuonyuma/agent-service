import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing

from google.genai import types

from agent_service.errors import AgentRuntimeError, AgentServiceError
from agent_service.kernel.context.history import RunContext, runtime_notice
from agent_service.kernel.dispatch.approval import PermissionCheck
from agent_service.kernel.dispatch.dispatcher import EventRelay, ToolDispatcher
from agent_service.kernel.dispatch.executor import tool_result_part
from agent_service.kernel.model.interaction import ModelInteraction
from agent_service.kernel.model.port import ModelGateway
from agent_service.kernel.runtime.bindings import (
    BindingFactory,
    RunBindings,
    ToolRegistry,
)
from agent_service.kernel.runtime.control import RunControl
from agent_service.kernel.runtime.events import LoopEvent, LoopResult
from agent_service.kernel.strategy.policy import TaskPolicy

MAX_TURNS = 10


def turn_limit(requested: int) -> int:
    if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
        raise ValueError("最大运行轮数必须是正整数。")
    return min(requested, MAX_TURNS)


async def query(
    contents: list[types.Content],
    tools: list[types.Tool],
    *,
    model: ModelGateway,
    tool_registry: ToolRegistry,
    system_prompt: str,
    max_tokens: int = 4096,
    binding_factory: BindingFactory | None = None,
    permission_check: PermissionCheck | None = None,
    allowed_tool_names: frozenset[str] | None = None,
    max_turns: int = MAX_TURNS,
    plan_and_execute: bool = True,
    graceful_final_turn: bool = False,
    inbox: asyncio.Queue[str] | None = None,
    run_control: RunControl | None = None,
) -> AsyncGenerator[LoopEvent]:
    """只编排一次运行：上下文 → 模型 → 调度 → 策略 → 检查点/终态。"""
    max_turns = turn_limit(max_turns)
    context = RunContext(contents)
    declared = frozenset(
        decl.name
        for group in tools
        for decl in group.function_declarations or []
        if decl.name
    )
    allowed = declared & frozenset(tool_registry)
    if allowed_tool_names is not None:
        allowed &= allowed_tool_names
    policy = TaskPolicy(allowed, plan_and_execute)
    plan = policy.plan
    interaction = ModelInteraction(model, system_prompt, max_tokens)
    relay = EventRelay()
    bindings = RunBindings(tool_registry)
    completed_turns = 0

    def checkpoint() -> None:
        if run_control is not None:
            run_control.ensure_running()
            run_control.capture(
                context.history,
                context.new_contents,
                completed_turns,
                interaction.total_usage(bindings.usage),
            )

    def terminal(reason: str, error: AgentServiceError | None = None) -> LoopEvent:
        return LoopEvent(
            type="run.completed" if reason == "completed" else "run.failed",
            turn=completed_turns,
            phase=plan.phase,
            plan=plan.snapshot() if plan.has_state() else None,
            result=LoopResult(
                reason="completed"
                if reason == "completed"
                else "max_turns"
                if reason == "max_turns"
                else "error",
                history=list(context.history),
                new_contents=list(context.new_contents),
                turns=completed_turns,
                usage=interaction.total_usage(bindings.usage),
                error=error,
                plan=plan.snapshot() if plan.has_state() else None,
            ),
        )

    try:
        checkpoint()
        if binding_factory is not None:
            bindings = binding_factory(
                registry=tool_registry,
                allowed_tool_names=allowed,
                system_prompt=system_prompt,
                plan=plan,
                permission_check=permission_check,
                event_forwarder=relay.forward,
                run_control=run_control,
            )
        dispatcher = ToolDispatcher(
            bindings.registry,
            allowed,
            policy,
            relay,
            permission_check,
            run_control,
        )
        context.notice(policy.start_notice(context.history))
        for turn in range(1, max_turns + 1):
            completed_turns = turn
            if run_control is not None:
                run_control.ensure_running()
            context.receive(inbox)
            phase = plan.phase
            final_turn = graceful_final_turn and turn == max_turns
            if final_turn:
                context.notice(
                    runtime_notice(
                        "## 最终轮总结指令（Final Turn）\n"
                        "当前已到达子代理最大运行步数，工具调用已关闭。"
                        "请立即基于目前已掌握的上下文、证据与已完成的操作，输出完整、精炼的最终总结报告。"
                    )
                )
            turn_tools = [] if final_turn else dispatcher.declarations(tools, phase)
            async with aclosing(
                interaction.stream(context.history, turn_tools)
            ) as stream:
                async for event in stream:
                    if run_control is not None:
                        run_control.ensure_running()
                    if event.type == "text":
                        yield LoopEvent(
                            type="text.delta",
                            text=event.text,
                            turn=turn,
                            phase=plan.phase,
                            plan=plan.snapshot() if plan.has_state() else None,
                        )
            result = interaction.result
            assert result is not None
            dispatcher.prepare_calls(result.function_calls)
            if run_control is not None:
                run_control.turns = turn
                run_control.usage = interaction.total_usage(bindings.usage)
            if not result.function_calls:
                context.append_model(result.raw_parts)
                checkpoint()
                decision = policy.before_completion(turn, max_turns)
                for event in decision.events:
                    yield event
                if decision.notice:
                    context.notice(decision.notice)
                    continue
                yield terminal("completed")
                return

            async with aclosing(
                dispatcher.dispatch(
                    result.function_calls,
                    phase,
                    turn,
                    disabled=final_turn,
                )
            ) as dispatch_stream:
                async for event in dispatch_stream:
                    yield event
            decision = policy.observe(
                dispatcher.executions, dispatcher.ignored_ids, turn
            )
            for event in decision.events:
                yield event
            # 工具请求与结果成对入历史；审批异常时不能留下孤立的函数调用。
            context.append_model(result.raw_parts)
            context.append_tools(
                [
                    tool_result_part(item.name, item.result, item.call_id)
                    for item in dispatcher.executions
                ],
                decision.notice,
            )
            checkpoint()

        yield terminal(
            "max_turns",
            AgentRuntimeError(f"Agent 已达到最大运行轮数 {max_turns}，任务仍未完成。"),
        )
    except AgentServiceError as exc:
        yield terminal("error", exc)
    except Exception as exc:  # noqa: BLE001 - 运行边界统一整理异常，取消仍向上传播
        yield terminal("error", AgentRuntimeError(f"Agent 运行失败：{exc}"))
    finally:
        await bindings.close()
