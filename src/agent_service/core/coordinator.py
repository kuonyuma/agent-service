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
    config: SafetyConfig # 安全审核参数

    changed: asyncio.Event = field(default_factory=asyncio.Event) # _review 所属
    rejected: asyncio.Event = field(default_factory=asyncio.Event) # 输入 run_trun
    model_done: bool = False # 标志底层是否彻底完成
    agent_task: asyncio.Task[None] | None = None # 生产者的协程句柄

    pending: str = "" # 还未审批的文字
    context: str = "" # 上下文
    unreviewed_chars: int = 0 # 正在审批的文字 + 还未审批的文字
    pending_since: float = 0 # 第一个字进入pending 的起始时间

    error: SafetyBlockedError | SafetyReviewError | None = None
    category: str = ""
    final: LoopEvent | None = None # 由底层传递的最终事件
    safety_usage: dict[str, int] = field(default_factory=dict)

    def reject(
        self, error: SafetyBlockedError | SafetyReviewError, category: str = ""
    ) -> None:
        """先封闭工具入口，再取消生产者，不等待界面或网络消费。"""
        if self.error is not None: # 防止后续报错覆盖信息
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
            self.reject(SafetyReviewError(UNAVAILABLE_MESSAGE)) # 超限，立刻调用 self.reject拉闸停机，不再接收这段文字。
            return
        if not self.pending:
            self.pending_since = asyncio.get_running_loop().time()
        self.pending += text
        self.unreviewed_chars += len(text)
        self.changed.set() # 通知_review()开始审查


class RunCoordinator:
    """独占读取 Agent 事件，并发审核文本，延迟发布唯一终态。"""

    def __init__(
        self,
        runner: AgentRunner,
        reviewer: TextReviewer | None = None,
        config: SafetyConfig | None = None,
    ) -> None:
        """初始化运行协调器，准备好底层的 Agent 运行器、安全审核器和审核配置。

        协调器本身不直接跟大模型聊天，也不自己写审核逻辑，而是像一个总调度员：
        把负责干活的 `runner` 和负责检查文字安全性的 `reviewer` 组合到一起。

        参数:
            runner: 真正负责驱动 Agent 对话和调工具的运行器。
            reviewer: 负责检查输出文字是否违规的审核器；传 None 时默认使用 GeminiTextReviewer。
            config: 安全审核的阈值和超时配置；传 None 时使用全局默认配置 settings.safety。

        返回:
            None。
        """
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
        """启动一轮完整的用户对话，一边转发 Agent 事件，一边在后台并发审核模型输出的文字。

        你可以把它理解成挡在 Agent 和外部界面（CLI 或 Web）之间的“安检调度站”：
        Agent 每吐出一段文字，协调器都会先喂给后台审核任务检查；普通事件照常往外发，
        但最后的结束事件（run.completed / run.failed）必须等所有文字都审核通过后才放行。
        一旦审核发现违规或审核服务出错，就会立刻拉闸停掉 Agent，清理未通过的文字并抛出拦截结果。

        参数:
            history: 本次提问之前的旧聊天记录列表。
            user_query: 用户这一轮刚输入的问题或指令。
            permission_check: 当模型想执行改文件、跑命令等危险工具时，用来找人确认的函数；
                返回 True 才放行，传 None 时默认拒绝所有需要确认的工具。
            run_control: 用于控制运行停止和保存历史快照的控制器；传 None 时会自动新建一个。

        返回:
            一个不断往外产出运行事件（LoopEvent）的异步生成器。正常情况下最后一条是
            "run.completed" 或 "run.failed"；如果触发安全拦截或审核失败，会先产出
            "safety.blocked"，最后再以 "run.failed" 收尾。
        """
        control = run_control if run_control is not None else RunControl()
        user_part = types.Part.from_text(text=user_query)
        user_message = types.Content(
            role="user",
            parts=[user_part],
        )
        initial_history = [*history, user_message]
        control.capture(
            history=initial_history,
            new_contents=[],
            turns=0,
            usage={},
        )
        state = _RunState(control, self.config)
        queue: asyncio.Queue[tuple[LoopEvent, asyncio.Event]] = asyncio.Queue(maxsize=1)

        async def guarded_permission(request):
            """在工具审批前后加装安全门闩检查的包装函数。

            等用户点“同意”可能要花几秒甚至几十秒，这期间后台安全审核可能已经判定违规并拉闸了。
            所以在真正找用户确认前、以及用户刚点完确认后，都必须检查一次运行是否已被叫停。

            参数:
                request: 打包好的工具执行申请单（ToolApprovalRequest）。

            返回:
                True 表示用户同意且运行未被叫停；False 表示拒绝执行。
            """
            control.ensure_running()
            allowed = await permission_check(request) if permission_check else False
            control.ensure_running()
            return allowed

        async def produce() -> None:
            """在后台独占读取 AgentRunner 的事件流，把文字送去审核、把普通事件塞进队列。

            这个内部任务相当于“搬运工”：
            1. 拿到模型输出的文字片段（text.delta）时，先喂给审核缓冲区；
            2. 拿到普通事件时，放进队列交给外层生成器往外发，并等待外层确认收到（ack）；
            3. 拿到最终事件（run.completed / run.failed）时，先存进 state.final 扣留下来，
               等审核任务全部检查通过后再由外层统一发布。

            返回:
                None（结果和异常都会记录到 state 中，最后唤醒审核任务收尾）。
            """
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
                        await ack.wait()
                raise AgentRuntimeError("Agent 事件流未产生终态。")
            except Exception as exc:  
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
        """在后台循环检查待审核文字，按字数阈值、时间间隔或模型结束信号分批送审。

        为了不让模型每吐一个字就调一次审核接口，这个函数会先攒文字：
        只有攒够了字数（chunk_chars）、或者等够了时间（interval_seconds）、又或者模型已经全部说完了，
        才会把“上一段末尾留下的上下文 + 这一批新文字”一起发给审核器检查。
        只要审核返回拦截（block）、报错（error）或超时，就立刻调用 `state.reject()` 拉闸中止整次运行。

        参数:
            state: 当前这轮运行的共享状态盒，里面装着待审文字、上下文、配置和停止开关。

        返回:
            None（审核全部通过时正常退出；拦截或出错时通过修改 state 通知外层）。
        """
        try:
            while state.error is None:
                state.changed.clear()
                if not state.pending:
                    if state.model_done:
                        return
                    await state.changed.wait()
                    continue
                remaining = ( # 时间
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
                    result = await self.reviewer.check(text) # 调用审核llm
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
        """把安全审核消耗的 token 数量合并进最终运行结果里。

        除了在单独的 `safety_usage` 字段里记一笔安全审核开销外，也会把这部分 token
        加到总账本 `usage` 里，方便外层统计整次运行一共花了多少 token。

        参数:
            result: Agent 原本生成的运行结果对象。
            state: 当前运行的共享状态盒，里面记着安全审核累计消耗的 token（safety_usage）。

        返回:
            更新了总 token 用量和安全审核用量的新结果对象（LoopResult）。
        """
        usage = dict(result.usage)
        for key, value in state.safety_usage.items():
            usage[key] = usage.get(key, 0) + value
        return replace(result, usage=usage, safety_usage=dict(state.safety_usage))

    def _blocked_result(self, state: _RunState) -> LoopResult:
        """在触发安全拦截或审核失败时，清理掉本次不安全的模型文字，打包一份安全的失败结果。

        它会从 `RunControl` 保存的检查点里恢复历史：保留这次运行之前的旧聊天记录，
        对于本次运行新增的记录，只留下工具调用和工具返回结果（把模型的普通说话内容和系统提示抹掉），
        最后补上一条统一的“内容无法展示 / 运行已中止”提示语，防止违规文字留在历史记录里。

        参数:
            state: 当前运行的共享状态盒，包含控制器快照和具体的安全错误对象。

        返回:
            清理过聊天记录、并附上安全错误和 token 统计的最终失败结果（LoopResult）。
        """
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
