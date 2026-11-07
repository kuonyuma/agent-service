import asyncio
from collections.abc import AsyncGenerator, Awaitable, Mapping
from copy import deepcopy
from uuid import uuid7

from google.genai import types

from agent_service.errors import InvalidToolCallError
from agent_service.kernel.dispatch.approval import PermissionCheck, ToolApprovalRequest
from agent_service.kernel.dispatch.base import Tool, ToolResult
from agent_service.kernel.dispatch.executor import ToolExecution, execute_tools
from agent_service.kernel.runtime.control import RunControl
from agent_service.kernel.runtime.events import LoopEvent
from agent_service.kernel.strategy.plan import AgentPhase
from agent_service.kernel.strategy.policy import TaskPolicy


class EventRelay:
    """让协作适配器透传事件，并等待消费者确实收到后再继续审批。"""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[LoopEvent, asyncio.Event]] = asyncio.Queue()

    async def forward(self, event: LoopEvent) -> None:
        ack = asyncio.Event()
        await self.queue.put((event, ack))
        await ack.wait()


class ToolDispatcher:
    def __init__(
        self,
        registry: Mapping[str, Tool],
        allowed_names: frozenset[str],
        policy: TaskPolicy,
        relay: EventRelay,
        permission_check: PermissionCheck | None,
        control: RunControl | None,
    ) -> None:
        self.registry = registry
        self.allowed_names = allowed_names
        self.policy = policy
        self.relay = relay
        self.permission_check = permission_check
        self.control = control
        self.seen_ids: set[str] = set()
        self.executions: list[ToolExecution] = []
        self.ignored_ids: set[str] = set()

    def declarations(
        self, tools: list[types.Tool], phase: AgentPhase
    ) -> list[types.Tool]:
        groups: list[types.Tool] = []
        for group in tools:
            declarations = [
                decl
                for decl in group.function_declarations or []
                if decl.name in self.allowed_names
                and (tool := self.registry.get(decl.name)) is not None
                and not self.policy.blocked_reason(decl.name, tool, phase)
            ]
            if declarations:
                groups.append(types.Tool(function_declarations=declarations))
        return groups

    def prepare_calls(self, calls: list[types.FunctionCall]) -> None:
        """在写入模型历史前分配唯一 ID，使调用与响应始终一致。"""
        for call in calls:
            if not call.name:
                raise InvalidToolCallError("模型返回的工具调用缺少名称，无法执行。")
            if not call.id or call.id in self.seen_ids:
                call.id = str(uuid7())
            self.seen_ids.add(call.id)

    async def dispatch(
        self,
        calls: list[types.FunctionCall],
        phase: AgentPhase,
        turn: int,
        *,
        disabled: bool = False,
    ) -> AsyncGenerator[LoopEvent]:
        """先生成请求与审批，再执行；阶段约束固定在本轮开始时。"""
        # 所有调用一起快照；消费者和审批回调不能篡改后续待执行参数。
        calls = deepcopy(calls)
        self.executions = []
        self.ignored_ids = set()
        pending: list[tuple[types.FunctionCall, ToolApprovalRequest | None]] = []
        by_id: dict[str, ToolExecution] = {}
        for call in calls:
            if not call.name or not call.id:
                raise InvalidToolCallError("工具调用尚未分配名称和 ID。")
            args = deepcopy(call.args or {})
            tool = self.registry.get(call.name)
            blocked = "当前为最终总结轮，工具调用已关闭。" if disabled else ""
            if tool is not None and not blocked:
                blocked = self.policy.blocked_reason(call.name, tool, phase)
            needs_approval = bool(
                not blocked
                and tool is not None
                and not tool.read_only
                and call.name in self.allowed_names
            )
            request = (
                ToolApprovalRequest.create(call.id, call.name, args)
                if needs_approval
                else None
            )
            yield LoopEvent(
                type="tool.requested",
                turn=turn,
                phase=self.policy.plan.phase,
                plan=self.policy.plan.snapshot()
                if self.policy.plan.has_state()
                else None,
                tool_call_id=call.id,
                tool_name=call.name,
                tool_arguments=args,
                requires_approval=needs_approval,
                approval_request_hash=request.request_hash if request else "",
            )
            if blocked:
                self.ignored_ids.add(call.id)
                by_id[call.id] = ToolExecution(
                    call.id,
                    call.name,
                    deepcopy(call.args or {}),
                    ToolResult(blocked, is_error=True),
                )
            else:
                pending.append((call, request))

        approved: set[str] = set()
        for call, request in pending:
            if self.control:
                self.control.ensure_running()
            if request is not None:
                allowed = (
                    await self.permission_check(request)
                    if self.permission_check
                    else False
                )
                # 审批展示的数据与将要执行的数据必须仍是同一份申请。
                current = ToolApprovalRequest.create(
                    call.id or "", call.name or "", call.args or {}
                )
                displayed = ToolApprovalRequest.create(
                    request.call_id, request.tool_name, request.arguments
                )
                if (
                    allowed
                    and current.request_hash
                    == request.request_hash
                    == displayed.request_hash
                ):
                    approved.add(current.call_id)
                else:
                    self.ignored_ids.add(call.id or "")

        if pending:
            task = asyncio.create_task(
                execute_tools(
                    [call for call, _ in pending],
                    tool_registry=self.registry,
                    allowed_tool_names=self.allowed_names,
                    approved_tool_call_ids=frozenset(approved),
                    run_control=self.control,
                )
            )
            queue_task = None
            try:
                while not task.done():
                    queue_task = asyncio.create_task(self.relay.queue.get())
                    done, _ = await asyncio.wait(
                        {task, queue_task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if queue_task in done:
                        event, ack = queue_task.result()
                        yield event
                        ack.set()
                    else:
                        queue_task.cancel()
                        await asyncio.gather(queue_task, return_exceptions=True)
                while not self.relay.queue.empty():
                    event, ack = self.relay.queue.get_nowait()
                    yield event
                    ack.set()
                batch = await task
                by_id.update((item.call_id, item) for item in batch.executions)
            finally:
                tasks = [task] + ([queue_task] if queue_task is not None else [])
                for child in tasks:
                    if not child.done():
                        child.cancel()
                pending_tasks: list[Awaitable[object]] = list(tasks)
                await asyncio.gather(*pending_tasks, return_exceptions=True)

        for call in calls:
            execution = by_id[call.id or ""]
            self.executions.append(execution)
            yield LoopEvent(
                type="tool.completed",
                turn=turn,
                phase=self.policy.plan.phase,
                plan=self.policy.plan.snapshot()
                if self.policy.plan.has_state()
                else None,
                tool_call_id=execution.call_id,
                tool_name=execution.name,
                tool_arguments=deepcopy(execution.arguments),
                tool_result=execution.result,
            )
