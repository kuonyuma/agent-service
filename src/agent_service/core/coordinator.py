import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass, field, replace

from google.genai import types

from agent_service.config.settings import SafetyConfig, settings
from agent_service.core.agentic_loop import LoopEvent, LoopResult, PermissionCheck
from agent_service.core.control import RunControl
from agent_service.core.runner import AgentRunner
from agent_service.errors import (
    AgentRuntimeError,
    SafetyBlockedError,
    SafetyReviewError,
)
from agent_service.safety.reviewer import GeminiTextReviewer, TextReviewer

BLOCKED_MESSAGE = "本次内容因安全限制无法展示，请调整请求后重试。"
UNAVAILABLE_MESSAGE = "安全审核暂时不可用，本次运行已停止，请稍后重试。"
INTERRUPTED_MESSAGE = "本次运行已中止；部分工具操作可能已发生，请核实实际状态后再继续。"


@dataclass
class _RunState:
    control: RunControl
    config: SafetyConfig
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    rejected: asyncio.Event = field(default_factory=asyncio.Event)
    model_done: bool = False
    pending: str = ""
    context: str = ""
    unreviewed_chars: int = 0
    pending_since: float = 0
    error: SafetyBlockedError | SafetyReviewError | None = None
    category: str = ""
    final: LoopEvent | None = None
    agent_task: asyncio.Task[None] | None = None
    safety_usage: dict[str, int] = field(default_factory=dict)

    def reject(
        self, error: SafetyBlockedError | SafetyReviewError, category: str = ""
    ) -> None:
        """先封闭工具入口，再取消生产者，不等待界面或网络消费。"""
        if self.error is not None:
            return
        self.error, self.category = error, category
        self.control.stopped = True
        self.control.safety_stopped = True
        self.changed.set()
        self.rejected.set()
        if self.agent_task is not None:
            self.agent_task.cancel()

    def feed(self, text: str) -> None:
        if not text:
            return
        if self.unreviewed_chars + len(text) > self.config.max_buffer_chars:
            self.reject(SafetyReviewError(UNAVAILABLE_MESSAGE))
            return
        if not self.pending:
            self.pending_since = asyncio.get_running_loop().time()
        self.pending += text
        self.unreviewed_chars += len(text)
        self.changed.set()


class RunCoordinator:
    """独占读取 Agent 事件，并发审核文本，延迟发布唯一终态。"""

    def __init__(
        self,
        runner: AgentRunner,
        reviewer: TextReviewer | None = None,
        config: SafetyConfig | None = None,
    ) -> None:
        self.runner = runner
        self.reviewer = reviewer if reviewer is not None else GeminiTextReviewer()
        self.config = config if config is not None else settings.safety

    async def run_turn(
        self,
        history: list[types.Content],
        user_query: str,
        permission_check: PermissionCheck | None = None,
        run_control: RunControl | None = None,
    ) -> AsyncGenerator[LoopEvent]:
        control = run_control if run_control is not None else RunControl()
        control.capture(
            [
                *history,
                types.Content(
                    role="user", parts=[types.Part.from_text(text=user_query)]
                ),
            ],
            [],
            0,
            {},
        )
        state = _RunState(control, self.config)
        queue: asyncio.Queue[tuple[LoopEvent, asyncio.Event]] = asyncio.Queue(maxsize=1)

        async def guarded_permission(request):
            control.ensure_running()
            allowed = await permission_check(request) if permission_check else False
            control.ensure_running()
            return allowed

        async def produce() -> None:
            try:
                async with aclosing(
                    self.runner.run_turn(
                        history=history,
                        user_query=user_query,
                        permission_check=guarded_permission,
                        run_control=control,
                    )
                ) as stream:
                    async for event in stream:
                        control.ensure_running()
                        if event.type == "text.delta":
                            state.feed(event.text)
                            control.ensure_running()
                        if event.type in {"run.completed", "run.failed"}:
                            state.final = event
                            return
                        ack = asyncio.Event()
                        await queue.put((event, ack))
                        # Web 提交工具请求后，生产者才能进入审批回调。
                        await ack.wait()
                raise AgentRuntimeError("Agent 事件流未产生终态。")
            except Exception as exc:  # noqa: BLE001 - 运行边界转换异常
                state.final = LoopEvent(
                    type="run.failed",
                    result=LoopResult(
                        reason="error",
                        history=control.history,
                        new_contents=control.new_contents,
                        turns=control.turns,
                        usage=control.usage,
                        error=AgentRuntimeError(f"Agent 运行失败：{exc}"),
                    ),
                )
            finally:
                state.model_done = True
                state.changed.set()

        agent_task = asyncio.create_task(produce(), name="agent-producer")
        state.agent_task = agent_task
        review_task = asyncio.create_task(self._review(state), name="safety-reviewer")
        rejected_task = asyncio.create_task(state.rejected.wait())
        receive_task: asyncio.Task[tuple[LoopEvent, asyncio.Event]] | None = None
        try:
            while True:
                receive_task = asyncio.create_task(queue.get())
                await asyncio.wait(
                    {receive_task, review_task, rejected_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if state.error is not None:
                    receive_task.cancel()
                    review_task.cancel()
                    blocked_result = self._blocked_result(state)
                    yield LoopEvent(
                        type="safety.blocked",
                        text=str(state.error),
                        safety_code="safety_blocked"
                        if isinstance(state.error, SafetyBlockedError)
                        else "safety_review_failed",
                        safety_category=state.category,
                        result=blocked_result,
                    )
                    await asyncio.gather(
                        agent_task, review_task, return_exceptions=True
                    )
                    yield LoopEvent(type="run.failed", result=blocked_result)
                    return
                if receive_task.done():
                    event, ack = receive_task.result()
                    try:
                        yield event
                    finally:
                        ack.set()
                    continue
                receive_task.cancel()
                await asyncio.gather(receive_task, return_exceptions=True)
                await review_task
                final = state.final
                if final is None or final.result is None:
                    raise AgentRuntimeError("审核完成后缺少 Agent 终态。")
                yield replace(final, result=self._add_usage(final.result, state))
                return
        finally:
            control.stopped = True
            tasks: list[asyncio.Task] = [agent_task, review_task, rejected_task]
            if receive_task is not None:
                tasks.append(receive_task)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _review(self, state: _RunState) -> None:
        try:
            while state.error is None:
                state.changed.clear()
                if not state.pending:
                    if state.model_done:
                        return
                    await state.changed.wait()
                    continue
                remaining = (
                    state.pending_since
                    + state.config.interval_seconds
                    - asyncio.get_running_loop().time()
                )
                if (
                    len(state.pending) < state.config.chunk_chars
                    and not state.model_done
                    and remaining > 0
                ):
                    try:
                        await asyncio.wait_for(state.changed.wait(), remaining)
                    except TimeoutError:
                        pass
                    continue
                batch, state.pending = state.pending, ""
                text = state.context + batch
                async with asyncio.timeout(state.config.timeout_seconds):
                    result = await self.reviewer.check(text)
                for key, value in result.usage.items():
                    state.safety_usage[key] = state.safety_usage.get(key, 0) + value
                if result.verdict == "block":
                    state.reject(SafetyBlockedError(BLOCKED_MESSAGE), result.category)
                    return
                if result.verdict != "allow":
                    state.reject(SafetyReviewError(UNAVAILABLE_MESSAGE))
                    return
                state.unreviewed_chars -= len(batch)
                state.context = (
                    text[-state.config.context_chars :]
                    if state.config.context_chars
                    else ""
                )
        except Exception:  # noqa: BLE001 - 超时及异常不放行，也不暴露原文
            state.reject(SafetyReviewError(UNAVAILABLE_MESSAGE))

    @staticmethod
    def _add_usage(result: LoopResult, state: _RunState) -> LoopResult:
        usage = dict(result.usage)
        for key, value in state.safety_usage.items():
            usage[key] = usage.get(key, 0) + value
        return replace(result, usage=usage, safety_usage=dict(state.safety_usage))

    def _blocked_result(self, state: _RunState) -> LoopResult:
        control = state.control
        generated: list[types.Content] = []
        # 检查点只含完整消息链；清除本次普通模型文本及运行时提示。
        for content in control.new_contents:
            parts = [
                part.model_copy(deep=True, update={"text": None})
                for part in content.parts or []
                if part.function_call is not None or part.function_response is not None
            ]
            if parts:
                generated.append(types.Content(role=content.role, parts=parts))
        generated.append(
            types.Content(
                role="model",
                parts=[
                    types.Part.from_text(
                        text=f"{state.error}\n{INTERRUPTED_MESSAGE}",
                    )
                ],
            )
        )
        prefix_length = len(control.history) - len(control.new_contents)
        result = LoopResult(
            reason="error",
            history=[*control.history[:prefix_length], *generated],
            new_contents=generated,
            turns=control.turns,
            usage=control.usage,
            error=state.error,
        )
        return self._add_usage(result, state)
