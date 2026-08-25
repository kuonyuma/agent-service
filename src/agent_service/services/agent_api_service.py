import asyncio
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid7

from google.genai import types
from sqlalchemy import CursorResult, func, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_service.core.agentic_loop import (
    LoopEvent,
    LoopResult,
    ToolApprovalRequest,
)
from agent_service.core.runner import AgentRunner
from agent_service.db import (
    AgentRun,
    AsyncSessionFactory,
    Conversation,
    Database,
    Message,
    ToolCall,
)
from agent_service.errors import (
    AgentRuntimeError,
    ApprovalConflictError,
    ConversationBusyError,
    ConversationNotFoundError,
    PersistenceError,
    RunNotFoundError,
    ToolCallNotFoundError,
)
from agent_service.services.content_mapper import (
    content_kind,
    content_text,
    dump_content,
    load_content,
)
from agent_service.services.events import RunStreamEvent, RunStreamEventType

logger = logging.getLogger("agent_service.runs")


def _utc_now() -> datetime:
    """MySQL DATETIME 使用无时区标记的 UTC 时间。"""

    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(frozen=True)
class PreparedRun:
    """在流开始前已经提交到数据库的运行快照。"""

    run_id: str
    conversation_id: str
    message: str
    history: list[types.Content]
    request_id: str


class AgentAPIService:
    """编排短事务、Agent Runtime 和 SSE 事件，不持有长事务。"""

    def __init__(
        self,
        database: Database,
        runner: AgentRunner,
        *,
        run_timeout_seconds: float = 120.0,
        approval_timeout_seconds: float = 60.0,
        approval_poll_interval_seconds: float = 0.25,
        stream_event_buffer_size: int = 256,
    ) -> None:
        self.database = database
        self.session_factory: AsyncSessionFactory = database.session_factory
        self.runner = runner
        self.run_timeout_seconds = run_timeout_seconds
        self.approval_timeout_seconds = approval_timeout_seconds
        self.approval_poll_interval_seconds = approval_poll_interval_seconds
        self.stream_event_buffer_size = stream_event_buffer_size
        self._approval_waiters: dict[tuple[str, str], asyncio.Event] = {}
        self._approval_waiters_lock = asyncio.Lock()

    async def create_conversation(self, title: str) -> Conversation:
        async with self.session_factory.begin() as session:
            conversation = Conversation(title=title)
            session.add(conversation)
            await session.flush()
            await session.refresh(conversation)
            return conversation

    async def get_conversation(self, conversation_id: str) -> Conversation:
        async with self.session_factory() as session:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(f"会话不存在：{conversation_id}")
            return conversation

    async def list_messages(self, conversation_id: str) -> list[Message]:
        async with self.session_factory() as session:
            exists = await session.scalar(
                select(Conversation.id).where(Conversation.id == conversation_id)
            )
            if exists is None:
                raise ConversationNotFoundError(f"会话不存在：{conversation_id}")
            result = await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.sequence_no)
            )
            return list(result.all())

    async def get_run(self, run_id: str) -> AgentRun:
        async with self.session_factory() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise RunNotFoundError(f"运行不存在：{run_id}")
            return run

    async def list_tool_calls(self, run_id: str) -> list[ToolCall]:
        """读取一次 run 的工具调用及审批审计记录。"""

        async with self.session_factory() as session:
            run_exists = await session.scalar(
                select(AgentRun.id).where(AgentRun.id == run_id)
            )
            if run_exists is None:
                raise RunNotFoundError(f"运行不存在：{run_id}")
            result = await session.scalars(
                select(ToolCall)
                .where(ToolCall.run_id == run_id)
                .order_by(ToolCall.created_at, ToolCall.id)
            )
            return list(result.all())

    async def decide_tool_call(
        self,
        run_id: str,
        call_id: str,
        decision: str,
        reason: str | None,
    ) -> ToolCall:
        """用一次条件更新记录本地操作员的批准或拒绝。"""

        if decision not in {"approve", "reject"}:
            raise ApprovalConflictError(f"未知审批决定：{decision}")

        now = _utc_now()
        outcome = "approved" if decision == "approve" else "rejected"
        expired = False
        saved: ToolCall | None = None
        async with self.session_factory.begin() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise RunNotFoundError(f"运行不存在：{run_id}")
            tool_call = await session.scalar(
                select(ToolCall).where(
                    ToolCall.run_id == run_id,
                    ToolCall.call_id == call_id,
                )
            )
            if tool_call is None:
                raise ToolCallNotFoundError(
                    f"工具调用不存在：run={run_id}, call={call_id}"
                )
            if not tool_call.requires_approval:
                raise ApprovalConflictError("该工具调用不需要人工审批。")
            if run.status not in {"running", "waiting_approval"}:
                raise ApprovalConflictError("运行已经终结，不能继续审批。")
            if tool_call.approval_status != "pending":
                raise ApprovalConflictError(
                    f"工具审批已经终结：{tool_call.approval_status}"
                )

            expires_at = tool_call.approval_expires_at
            if expires_at is None or expires_at <= now:
                expire_result = cast(
                    CursorResult[Any],
                    await session.execute(
                        update(ToolCall)
                        .where(
                            ToolCall.run_id == run_id,
                            ToolCall.call_id == call_id,
                            ToolCall.approval_status == "pending",
                        )
                        .values(
                            status="rejected",
                            approval_status="expired",
                            approval_decided_at=now,
                            approval_decided_by="system_timeout",
                            approval_reason="审批等待超时，已自动拒绝。",
                        )
                    ),
                )
                expired = expire_result.rowcount == 1
            else:
                decision_result = cast(
                    CursorResult[Any],
                    await session.execute(
                        update(ToolCall)
                        .where(
                            ToolCall.run_id == run_id,
                            ToolCall.call_id == call_id,
                            ToolCall.approval_status == "pending",
                            ToolCall.approval_expires_at > now,
                        )
                        .values(
                            status=(
                                "requested" if outcome == "approved" else "rejected"
                            ),
                            approval_status=outcome,
                            approval_decided_at=now,
                            approval_decided_by="local_operator",
                            approval_reason=reason,
                        )
                    ),
                )
                if decision_result.rowcount != 1:
                    raise ApprovalConflictError("审批已被其他请求处理。")

            await self._resume_run_if_no_pending_approval(session, run_id)
            await session.refresh(tool_call)
            saved = tool_call

        await self._notify_approval(run_id, call_id)
        if expired:
            raise ApprovalConflictError("审批已超时并自动拒绝。")
        if saved is None:
            raise PersistenceError("审批已提交，但无法读取工具调用。")
        logger.info(
            "工具审批已提交：%s",
            outcome,
            extra={"run_id": run_id},
        )
        return saved

    async def _resume_run_if_no_pending_approval(
        self,
        session: AsyncSession,
        run_id: str,
    ) -> None:
        pending_count = await session.scalar(
            select(func.count(ToolCall.id)).where(
                ToolCall.run_id == run_id,
                ToolCall.approval_status == "pending",
            )
        )
        if pending_count == 0:
            await session.execute(
                update(AgentRun)
                .where(
                    AgentRun.id == run_id,
                    AgentRun.status == "waiting_approval",
                )
                .values(status="running")
            )

    async def _register_approval_waiter(
        self,
        run_id: str,
        call_id: str,
    ) -> asyncio.Event:
        key = (run_id, call_id)
        async with self._approval_waiters_lock:
            if key in self._approval_waiters:
                raise PersistenceError(f"工具审批等待器重复：{call_id}")
            waiter = asyncio.Event()
            self._approval_waiters[key] = waiter
            return waiter

    async def _remove_approval_waiter(self, run_id: str, call_id: str) -> None:
        async with self._approval_waiters_lock:
            self._approval_waiters.pop((run_id, call_id), None)

    async def _notify_approval(self, run_id: str, call_id: str) -> None:
        async with self._approval_waiters_lock:
            waiter = self._approval_waiters.get((run_id, call_id))
        if waiter is not None:
            waiter.set()

    async def _drop_run_waiters(self, run_id: str) -> None:
        async with self._approval_waiters_lock:
            keys = [key for key in self._approval_waiters if key[0] == run_id]
            waiters = [self._approval_waiters.pop(key) for key in keys]
        for waiter in waiters:
            waiter.set()

    async def _wait_for_approval(
        self,
        prepared: PreparedRun,
        request: ToolApprovalRequest,
    ) -> bool:
        """事务外等待；通知用于低延迟，轮询用于跨 worker 可见性。"""

        key = (prepared.run_id, request.call_id)
        async with self._approval_waiters_lock:
            waiter = self._approval_waiters.get(key)
        if waiter is None:
            raise PersistenceError(f"找不到工具审批等待器：{request.call_id}")

        try:
            while True:
                waiter.clear()
                async with self.session_factory() as session:
                    tool_call = await session.scalar(
                        select(ToolCall).where(
                            ToolCall.run_id == prepared.run_id,
                            ToolCall.call_id == request.call_id,
                        )
                    )
                if tool_call is None:
                    raise PersistenceError(
                        f"找不到工具审批记录：{request.call_id}"
                    )
                if tool_call.approval_request_hash != request.request_hash:
                    raise PersistenceError("工具审批参数指纹不一致，已拒绝执行。")

                status = tool_call.approval_status
                if status == "approved":
                    return True
                if status in {"rejected", "expired", "cancelled"}:
                    return False
                if status != "pending":
                    raise PersistenceError(f"未知的工具审批状态：{status}")

                expires_at = tool_call.approval_expires_at
                if expires_at is None:
                    raise PersistenceError("工具审批记录缺少过期时间。")
                remaining = (expires_at - _utc_now()).total_seconds()
                if remaining <= 0:
                    expired = await self._expire_approval(
                        prepared.run_id,
                        request.call_id,
                    )
                    if expired:
                        return False
                    continue

                wait_seconds = min(
                    self.approval_poll_interval_seconds,
                    remaining,
                )
                try:
                    await asyncio.wait_for(waiter.wait(), timeout=wait_seconds)
                except TimeoutError:
                    pass
        finally:
            await self._remove_approval_waiter(
                prepared.run_id,
                request.call_id,
            )

    async def _expire_approval(self, run_id: str, call_id: str) -> bool:
        now = _utc_now()
        async with self.session_factory.begin() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(ToolCall)
                    .where(
                        ToolCall.run_id == run_id,
                        ToolCall.call_id == call_id,
                        ToolCall.approval_status == "pending",
                        ToolCall.approval_expires_at <= now,
                    )
                    .values(
                        status="rejected",
                        approval_status="expired",
                        approval_decided_at=now,
                        approval_decided_by="system_timeout",
                        approval_reason="审批等待超时，已自动拒绝。",
                    )
                ),
            )
            if result.rowcount == 1:
                await self._resume_run_if_no_pending_approval(session, run_id)
        if result.rowcount == 1:
            await self._notify_approval(run_id, call_id)
            logger.info(
                "工具审批超时，已自动拒绝",
                extra={"run_id": run_id},
            )
            return True
        return False

    async def ready(self) -> bool:
        try:
            async with self.database.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except OSError, SQLAlchemyError:
            logger.warning("数据库就绪检查失败", exc_info=True)
            return False

    async def prepare_run(
        self,
        conversation_id: str,
        message: str,
        *,
        request_id: str = "",
    ) -> PreparedRun:
        """短事务：占用会话、创建 run 并保存用户消息。"""

        run_id = str(uuid7())
        user_content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=message)],
        )

        async with self.session_factory.begin() as session:
            claim_result = await self._claim_conversation(
                session,
                conversation_id,
                run_id,
            )
            if claim_result.rowcount != 1:
                active_run_id = await session.scalar(
                    select(Conversation.active_run_id).where(
                        Conversation.id == conversation_id
                    )
                )
                if active_run_id is None:
                    exists = await session.scalar(
                        select(Conversation.id).where(
                            Conversation.id == conversation_id
                        )
                    )
                    if exists is None:
                        raise ConversationNotFoundError(
                            f"会话不存在：{conversation_id}"
                        )
                recovered = bool(active_run_id) and await self._recover_expired_run(
                    session,
                    conversation_id,
                    active_run_id,
                )
                if recovered:
                    claim_result = await self._claim_conversation(
                        session,
                        conversation_id,
                        run_id,
                    )
                if claim_result.rowcount != 1:
                    raise ConversationBusyError(f"会话已有活跃运行：{conversation_id}")

            next_sequence = await session.scalar(
                select(Conversation.next_message_seq).where(
                    Conversation.id == conversation_id
                )
            )
            if next_sequence is None:
                raise PersistenceError("占用会话后无法读取消息序号。")
            user_sequence = next_sequence - 1

            payloads = await session.scalars(
                select(Message.payload_json)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.sequence_no)
            )
            history = [load_content(payload) for payload in payloads.all()]

            session.add_all(
                [
                    AgentRun(
                        id=run_id,
                        conversation_id=conversation_id,
                        status="running",
                    ),
                    Message(
                        conversation_id=conversation_id,
                        run_id=run_id,
                        sequence_no=user_sequence,
                        role="user",
                        kind="user_message",
                        content=message,
                        payload_json=dump_content(user_content),
                    ),
                ]
            )

        logger.info(
            "运行已创建",
            extra={
                "request_id": request_id,
                "conversation_id": conversation_id,
                "run_id": run_id,
            },
        )
        return PreparedRun(
            run_id=run_id,
            conversation_id=conversation_id,
            message=message,
            history=history,
            request_id=request_id,
        )

    async def _claim_conversation(
        self,
        session: AsyncSession,
        conversation_id: str,
        run_id: str,
    ) -> CursorResult[Any]:
        return cast(
            CursorResult[Any],
            await session.execute(
                update(Conversation)
                .where(
                    Conversation.id == conversation_id,
                    Conversation.active_run_id.is_(None),
                )
                .values(
                    active_run_id=run_id,
                    next_message_seq=Conversation.next_message_seq + 1,
                )
            ),
        )

    async def _recover_expired_run(
        self,
        session: AsyncSession,
        conversation_id: str,
        active_run_id: str,
    ) -> bool:
        """将超过运行时限的孤儿 run 作为过期租约回收。"""

        now = _utc_now()
        cutoff = now - timedelta(seconds=self.run_timeout_seconds)
        unexpired_approvals = await session.scalar(
            select(func.count(ToolCall.id)).where(
                ToolCall.run_id == active_run_id,
                ToolCall.approval_status == "pending",
                ToolCall.approval_expires_at > now,
            )
        )
        if unexpired_approvals:
            return False
        run_result = cast(
            CursorResult[Any],
            await session.execute(
                update(AgentRun)
                .where(
                    AgentRun.id == active_run_id,
                    AgentRun.conversation_id == conversation_id,
                    AgentRun.status.in_(("running", "waiting_approval")),
                    AgentRun.started_at <= cutoff,
                )
                .values(
                    status="failed",
                    error_code="RunExpired",
                    error_message="运行进程中断且已超过运行时限，租约被回收。",
                    finished_at=now,
                )
            ),
        )
        if run_result.rowcount != 1:
            return False

        await self._cancel_pending_approvals(
            session,
            active_run_id,
            now=now,
            actor="system_recovery",
            reason="运行进程中断，审批等待已取消。",
        )
        await session.execute(
            update(ToolCall)
            .where(
                ToolCall.run_id == active_run_id,
                ToolCall.status.in_(("requested", "waiting_approval")),
            )
            .values(
                status="failed",
                is_error=True,
                completed_at=now,
            )
        )
        release_result = cast(
            CursorResult[Any],
            await session.execute(
                update(Conversation)
                .where(
                    Conversation.id == conversation_id,
                    Conversation.active_run_id == active_run_id,
                )
                .values(active_run_id=None)
            ),
        )
        if release_result.rowcount != 1:
            raise PersistenceError("过期运行已终结，但会话租约回收失败。")
        logger.warning(
            "已回收过期运行",
            extra={
                "conversation_id": conversation_id,
                "run_id": active_run_id,
            },
        )
        return True

    async def stream_run(
        self,
        prepared: PreparedRun,
    ) -> AsyncGenerator[RunStreamEvent]:
        """消费独立生产任务的事件，网络背压不会进入运行超时作用域。"""

        queue: asyncio.Queue[RunStreamEvent | None] = asyncio.Queue(
            maxsize=self.stream_event_buffer_size
        )
        producer = asyncio.create_task(self._produce_run(prepared, queue))
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            if not producer.done():
                producer.cancel()
            try:
                await producer
            except asyncio.CancelledError:
                pass

    async def _produce_run(
        self,
        prepared: PreparedRun,
        queue: asyncio.Queue[RunStreamEvent | None],
    ) -> None:
        """事务外生产事件；Runner 等待与 SSE 背压分别计时。"""

        sequence = 0
        terminal_saved = False
        runner_stream = self.runner.run_turn(
            history=prepared.history,
            user_query=prepared.message,
            permission_check=lambda request: self._wait_for_approval(
                prepared,
                request,
            ),
        )
        try:
            remaining_runner_seconds = self.run_timeout_seconds
            while remaining_runner_seconds > 0:
                wait_started = asyncio.get_running_loop().time()
                try:
                    async with asyncio.timeout(remaining_runner_seconds):
                        event = await anext(runner_stream)
                except StopAsyncIteration:
                    break
                finally:
                    remaining_runner_seconds -= (
                        asyncio.get_running_loop().time() - wait_started
                    )

                approval_expires_at: datetime | None = None
                sequence += 1
                if event.type == "tool.requested":
                    approval_expires_at = await self._save_tool_requested(
                        prepared.run_id,
                        event,
                    )
                elif event.type == "tool.completed":
                    await self._save_tool_completed(prepared.run_id, event)
                elif event.type in {"run.completed", "run.failed"}:
                    result = event.result
                    if result is None:
                        raise AgentRuntimeError("终态事件缺少运行结果。")
                    status = (
                        "completed"
                        if event.type == "run.completed"
                        and result.reason == "completed"
                        else "failed"
                    )
                    await self._finish_run(prepared, result, status)
                    terminal_saved = True

                await queue.put(
                    self._to_stream_event(
                        prepared.run_id,
                        sequence,
                        event,
                    )
                )
                if event.requires_approval:
                    sequence += 1
                    await queue.put(
                        self._approval_required_stream_event(
                            prepared.run_id,
                            sequence,
                            event,
                            approval_expires_at,
                        )
                    )
                if terminal_saved:
                    return

            if remaining_runner_seconds <= 0:
                raise TimeoutError
            raise AgentRuntimeError("Agent 事件流意外结束，未产生终态事件。")
        except TimeoutError:
            error = AgentRuntimeError(
                f"Agent 运行超过 {self.run_timeout_seconds:g} 秒。"
            )
            terminal_saved = await self._safe_mark_failed(prepared, error)
            sequence += 1
            await queue.put(
                self._failed_stream_event(prepared.run_id, sequence, error)
            )
        except asyncio.CancelledError:
            try:
                await self._cancel_with_shield(prepared)
                terminal_saved = True
            except Exception:
                logger.exception(
                    "运行取消状态持久化失败",
                    extra=self._log_context(prepared),
                )
            raise
        except Exception as exc:  # noqa: BLE001 - HTTP 运行边界统一转终态
            error = (
                exc
                if isinstance(exc, AgentRuntimeError)
                else AgentRuntimeError(f"运行服务失败：{exc}")
            )
            terminal_saved = await self._safe_mark_failed(prepared, error)
            sequence += 1
            await queue.put(
                self._failed_stream_event(prepared.run_id, sequence, error)
            )
        finally:
            try:
                try:
                    await runner_stream.aclose()
                except Exception:
                    logger.exception(
                        "关闭 Runner 事件流失败",
                        extra=self._log_context(prepared),
                    )
            finally:
                if not terminal_saved:
                    try:
                        await self._cancel_with_shield(prepared)
                    except Exception:
                        logger.exception(
                            "运行取消状态持久化失败",
                            extra=self._log_context(prepared),
                        )
                await self._drop_run_waiters(prepared.run_id)
                self._close_event_queue(queue)

    async def cancel_run(self, prepared: PreparedRun) -> None:
        """供 ASGI Response 边界调用的幂等取消兜底。"""

        await self._cancel_with_shield(prepared)

    async def _save_tool_requested(
        self,
        run_id: str,
        event: LoopEvent,
    ) -> datetime | None:
        if not event.tool_call_id:
            raise PersistenceError("工具请求缺少 tool_call_id。")
        now = _utc_now()
        approval_expires_at = (
            now + timedelta(seconds=self.approval_timeout_seconds)
            if event.requires_approval
            else None
        )
        if event.requires_approval:
            await self._register_approval_waiter(run_id, event.tool_call_id)
        try:
            async with self.session_factory.begin() as session:
                session.add(
                    ToolCall(
                        run_id=run_id,
                        call_id=event.tool_call_id,
                        tool_name=event.tool_name,
                        arguments_json=event.tool_arguments,
                        status=(
                            "waiting_approval"
                            if event.requires_approval
                            else "requested"
                        ),
                        requires_approval=event.requires_approval,
                        approval_status=(
                            "pending" if event.requires_approval else None
                        ),
                        approval_request_hash=(
                            event.approval_request_hash
                            if event.requires_approval
                            else None
                        ),
                        approval_requested_at=now if event.requires_approval else None,
                        approval_expires_at=approval_expires_at,
                    )
                )
                if event.requires_approval:
                    run_result = cast(
                        CursorResult[Any],
                        await session.execute(
                            update(AgentRun)
                            .where(
                                AgentRun.id == run_id,
                                AgentRun.status.in_(
                                    ("running", "waiting_approval")
                                ),
                            )
                            .values(status="waiting_approval")
                        ),
                    )
                    if run_result.rowcount != 1:
                        raise PersistenceError("运行已终结，无法创建工具审批。")
        except Exception:
            if event.requires_approval:
                await self._remove_approval_waiter(run_id, event.tool_call_id)
            raise
        return approval_expires_at

    async def _save_tool_completed(self, run_id: str, event: LoopEvent) -> None:
        if not event.tool_call_id or event.tool_result is None:
            raise PersistenceError("工具完成事件缺少调用标识或结果。")
        tool_result = event.tool_result
        async with self.session_factory.begin() as session:
            approval_status = await session.scalar(
                select(ToolCall.approval_status).where(
                    ToolCall.run_id == run_id,
                    ToolCall.call_id == event.tool_call_id,
                )
            )
            completed_status = (
                "rejected"
                if approval_status in {"rejected", "expired"}
                else "failed" if tool_result.is_error else "completed"
            )
            update_result = cast(
                CursorResult[Any],
                await session.execute(
                    update(ToolCall)
                    .where(
                        ToolCall.run_id == run_id,
                        ToolCall.call_id == event.tool_call_id,
                    )
                    .values(
                        status=completed_status,
                        result_json={
                            "content": tool_result.content,
                            "is_error": tool_result.is_error,
                        },
                        result_content=tool_result.content,
                        is_error=tool_result.is_error,
                        completed_at=_utc_now(),
                    )
                ),
            )
            if update_result.rowcount != 1:
                raise PersistenceError(f"找不到待完成的工具调用：{event.tool_call_id}")

    async def _finish_run(
        self,
        prepared: PreparedRun,
        result: LoopResult,
        status: str,
    ) -> None:
        generated = result.new_contents
        async with self.session_factory.begin() as session:
            start_sequence = 0
            if generated:
                sequence_result = cast(
                    CursorResult[Any],
                    await session.execute(
                        update(Conversation)
                        .where(
                            Conversation.id == prepared.conversation_id,
                            Conversation.active_run_id == prepared.run_id,
                        )
                        .values(
                            next_message_seq=(
                                Conversation.next_message_seq + len(generated)
                            )
                        )
                    ),
                )
                if sequence_result.rowcount != 1:
                    raise PersistenceError("运行已失去会话占用权，无法保存回复。")
                next_sequence = await session.scalar(
                    select(Conversation.next_message_seq).where(
                        Conversation.id == prepared.conversation_id
                    )
                )
                if next_sequence is None:
                    raise PersistenceError("无法读取模型回复序号。")
                start_sequence = next_sequence - len(generated)

            for offset, content in enumerate(generated):
                session.add(
                    Message(
                        conversation_id=prepared.conversation_id,
                        run_id=prepared.run_id,
                        sequence_no=start_sequence + offset,
                        role=content.role or "model",
                        kind=content_kind(content),
                        content=content_text(content),
                        payload_json=dump_content(content),
                    )
                )

            run_update = cast(
                CursorResult[Any],
                await session.execute(
                    update(AgentRun)
                    .where(
                        AgentRun.id == prepared.run_id,
                        AgentRun.status.in_(("running", "waiting_approval")),
                    )
                    .values(
                        status=status,
                        turn_count=result.turns,
                        input_tokens=result.usage.get("input_len", 0),
                        output_tokens=result.usage.get("output_len", 0),
                        error_code=(
                            type(result.error).__name__ if result.error else None
                        ),
                        error_message=str(result.error) if result.error else None,
                        finished_at=_utc_now(),
                    )
                ),
            )
            if run_update.rowcount != 1:
                raise PersistenceError("运行已被其他流程终结。")

            if status == "failed":
                now = _utc_now()
                await self._cancel_pending_approvals(
                    session,
                    prepared.run_id,
                    now=now,
                    actor="system_failure",
                    reason="运行失败，待审批调用已取消。",
                )
                await session.execute(
                    update(ToolCall)
                    .where(
                        ToolCall.run_id == prepared.run_id,
                        ToolCall.status.in_(("requested", "waiting_approval")),
                    )
                    .values(
                        status="failed",
                        is_error=True,
                        completed_at=now,
                    )
                )

            release_result = cast(
                CursorResult[Any],
                await session.execute(
                    update(Conversation)
                    .where(
                        Conversation.id == prepared.conversation_id,
                        Conversation.active_run_id == prepared.run_id,
                    )
                    .values(active_run_id=None)
                ),
            )
            if release_result.rowcount != 1:
                raise PersistenceError("运行终结时无法释放会话占用权。")

        logger.info(
            "运行已终结：%s",
            status,
            extra=self._log_context(prepared),
        )

    async def _mark_failed(
        self,
        prepared: PreparedRun,
        error: Exception,
    ) -> None:
        async with self.session_factory.begin() as session:
            now = _utc_now()
            await session.execute(
                update(AgentRun)
                .where(
                    AgentRun.id == prepared.run_id,
                    AgentRun.status.in_(("running", "waiting_approval")),
                )
                .values(
                    status="failed",
                    error_code=type(error).__name__,
                    error_message=str(error),
                    finished_at=now,
                )
            )
            await session.execute(
                update(ToolCall)
                .where(
                    ToolCall.run_id == prepared.run_id,
                    ToolCall.approval_status == "pending",
                )
                .values(
                    approval_status="cancelled",
                    approval_decided_at=now,
                    approval_decided_by="system_failure",
                    approval_reason="运行失败，待审批调用已取消。",
                )
            )
            await session.execute(
                update(ToolCall)
                .where(
                    ToolCall.run_id == prepared.run_id,
                    ToolCall.status.in_(("requested", "waiting_approval")),
                )
                .values(
                    status="failed",
                    is_error=True,
                    completed_at=now,
                )
            )
            await session.execute(
                update(Conversation)
                .where(
                    Conversation.id == prepared.conversation_id,
                    Conversation.active_run_id == prepared.run_id,
                )
                .values(active_run_id=None)
            )
        logger.error(
            "运行失败：%s",
            error,
            extra=self._log_context(prepared),
        )

    async def _safe_mark_failed(
        self,
        prepared: PreparedRun,
        error: Exception,
    ) -> bool:
        """持久化失败也不能阻止 run.failed 和 SSE 结束标记。"""

        try:
            await self._mark_failed(prepared, error)
            return True
        except Exception:
            logger.exception(
                "运行失败状态无法持久化",
                extra=self._log_context(prepared),
            )
            return False

    async def _mark_cancelled(self, prepared: PreparedRun) -> None:
        async with self.session_factory.begin() as session:
            now = _utc_now()
            run_result = cast(
                CursorResult[Any],
                await session.execute(
                    update(AgentRun)
                    .where(
                        AgentRun.id == prepared.run_id,
                        AgentRun.status.in_(("running", "waiting_approval")),
                    )
                    .values(
                        status="cancelled",
                        error_code="ClientDisconnected",
                        error_message="客户端断开或流式响应被取消。",
                        finished_at=now,
                    )
                ),
            )
            if run_result.rowcount != 1:
                return
            await self._cancel_pending_approvals(
                session,
                prepared.run_id,
                now=now,
                actor="client_disconnect",
                reason="客户端断开，审批等待已取消。",
            )
            await session.execute(
                update(ToolCall)
                .where(
                    ToolCall.run_id == prepared.run_id,
                    ToolCall.status.in_(("requested", "waiting_approval")),
                )
                .values(
                    status="cancelled",
                    is_error=True,
                    completed_at=now,
                )
            )
            await session.execute(
                update(Conversation)
                .where(
                    Conversation.id == prepared.conversation_id,
                    Conversation.active_run_id == prepared.run_id,
                )
                .values(active_run_id=None)
            )
        logger.info("运行已取消", extra=self._log_context(prepared))

    @staticmethod
    async def _cancel_pending_approvals(
        session: AsyncSession,
        run_id: str,
        *,
        now: datetime,
        actor: str,
        reason: str,
    ) -> None:
        await session.execute(
            update(ToolCall)
            .where(
                ToolCall.run_id == run_id,
                ToolCall.approval_status == "pending",
            )
            .values(
                approval_status="cancelled",
                approval_decided_at=now,
                approval_decided_by=actor,
                approval_reason=reason,
            )
        )

    async def _cancel_with_shield(self, prepared: PreparedRun) -> None:
        cleanup = asyncio.create_task(self._mark_cancelled(prepared))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup

    @staticmethod
    def _close_event_queue(
        queue: asyncio.Queue[RunStreamEvent | None],
    ) -> None:
        """保证消费者最终收到结束标记；满队列时丢弃最旧的非终态事件。"""

        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(None)

    @staticmethod
    def _to_stream_event(
        run_id: str,
        sequence: int,
        event: LoopEvent,
    ) -> RunStreamEvent:
        data: dict[str, Any] = {
            "run_id": run_id,
            "turn": event.turn,
        }
        if event.type == "text.delta":
            data["text"] = event.text
        elif event.type == "tool.requested":
            data.update(
                {
                    "tool_call_id": event.tool_call_id,
                    "tool_name": event.tool_name,
                    "arguments": event.tool_arguments,
                    "requires_approval": event.requires_approval,
                }
            )
        elif event.type == "tool.completed":
            data.update(
                {
                    "tool_call_id": event.tool_call_id,
                    "tool_name": event.tool_name,
                    "arguments": event.tool_arguments,
                    "content": (event.tool_result.content if event.tool_result else ""),
                    "is_error": (
                        event.tool_result.is_error if event.tool_result else True
                    ),
                }
            )
        elif event.result is not None:
            result = event.result
            data.update(
                {
                    "status": (
                        "completed" if event.type == "run.completed" else "failed"
                    ),
                    "reason": result.reason,
                    "turns": result.turns,
                    "usage": result.usage,
                    "error": str(result.error) if result.error else None,
                }
            )
        return RunStreamEvent(
            type=cast(RunStreamEventType, event.type),
            sequence=sequence,
            data=data,
        )

    @staticmethod
    def _approval_required_stream_event(
        run_id: str,
        sequence: int,
        event: LoopEvent,
        expires_at: datetime | None,
    ) -> RunStreamEvent:
        return RunStreamEvent(
            type="tool.approval_required",
            sequence=sequence,
            data={
                "run_id": run_id,
                "turn": event.turn,
                "tool_call_id": event.tool_call_id,
                "tool_name": event.tool_name,
                "arguments": event.tool_arguments,
                "status": "pending",
                "expires_at": (
                    expires_at.isoformat(timespec="microseconds") + "Z"
                    if expires_at is not None
                    else None
                ),
            },
        )

    @staticmethod
    def _failed_stream_event(
        run_id: str,
        sequence: int,
        error: Exception,
    ) -> RunStreamEvent:
        return RunStreamEvent(
            type="run.failed",
            sequence=sequence,
            data={
                "run_id": run_id,
                "status": "failed",
                "reason": "error",
                "error": str(error),
            },
        )

    @staticmethod
    def _log_context(prepared: PreparedRun) -> dict[str, str]:
        return {
            "request_id": prepared.request_id,
            "conversation_id": prepared.conversation_id,
            "run_id": prepared.run_id,
        }
