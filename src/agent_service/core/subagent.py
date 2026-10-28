import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from google.genai import types

from agent_service.core.plan import ExecutionPlan
from agent_service.tools.base import Tool

SubagentRole = Literal["default", "worker", "explorer", "monitor"]
SubagentStatus = Literal["running", "completed", "failed"]

VALID_SUBAGENT_ROLES: tuple[SubagentRole, ...] = (
    "default",
    "worker",
    "explorer",
    "monitor",
)




@dataclass(frozen=True)
class AgentRoleProfile:
    """内置子代理角色配置与权限边界定义。"""

    role: SubagentRole # 子代理角色
    title: str
    description: str
    system_prompt_addon: str
    allowed_tools: frozenset[str] | None
    max_turns: int
    plan_and_execute: bool
    graceful_final_turn: bool = True
    read_only_role: bool = False


ROLE_PROFILES: dict[SubagentRole, AgentRoleProfile] = {
    "explorer": AgentRoleProfile(
        role="explorer",
        title="代码探索子代理",
        description="只读代码库搜索、调用链追踪与证据收集代理。",
        system_prompt_addon=(
            "## 子代理角色：Explorer（只读探索与取证专家）\n"
            "你是一个专注于代码追踪与证据搜集的只读子代理。\n"
            "- 仅使用只读工具（`list_files`、`read_file`、`load_yaml`、`web_search`、`fetch_web_page`）快速定位目标文件、函数定义、调用链或外部技术文档。\n"
            "- 如需检索网络技术资料，先用 `web_search` 获取候选列表，再对关键链接调用 `fetch_web_page` 深入阅读。\n"
            "- 不要尝试修改任何文件或执行外部命令。\n"
            "- 完成探索后，向主代理输出精炼、结构化的证据报告，必须包含准确的文件路径（或来源 URL）、关键符号与核心结论。"
        ),
        allowed_tools=frozenset(
            {
                "list_files",
                "read_file",
                "load_yaml",
                "web_search",
                "fetch_web_page",
            }
        ),
        max_turns=10,
        plan_and_execute=False,
        graceful_final_turn=True,
        read_only_role=True,
    ),
    "worker": AgentRoleProfile(
        role="worker",
        title="编码执行子代理",
        description="专注于密集代码实现、模块修改与缺陷修复的执行代理。",
        system_prompt_addon=(
            "## 子代理角色：Worker（密集实现与修复专家）\n"
            "你是一个专注于代码实现与修复的执行子代理。\n"
            "- 严格围绕主代理分配的子任务目标进行编码实现或修复，不要发散修改无关模块。\n"
            "- 修改任何文件前必须先用 `read_file` 确认上下文；修改后必须读取或运行检查验证结果。\n"
            "- 完成后向主代理清晰汇报：修改了哪些文件、核心改动逻辑以及验证结果。"
        ),
        allowed_tools=frozenset(
            {
                "list_files",
                "read_file",
                "write_file",
                "edit_file",
                "quality_check",
                "run_command",
            }
        ),
        max_turns=10,
        plan_and_execute=False,
        graceful_final_turn=True,
        read_only_role=False,
    ),
    "monitor": AgentRoleProfile(
        role="monitor",
        title="监控验证子代理",
        description="专注于运行测试、质量检查、监控长时间任务输出与验证状态的代理。",
        system_prompt_addon=(
            "## 子代理角色：Monitor（长任务监控与质量验证专家）\n"
            "你是一个专注于监控测试运行、质量检查与日志分析的验证子代理。\n"
            "- 使用命令执行、质量检查或只读文件工具验证系统状态、运行测试或检查命令输出。\n"
            "- 不要直接修改业务源码文件；若发现报错或异常，准确提取报错堆栈、失败用例和根因诊断汇报给主代理。"
        ),
        allowed_tools=frozenset(
            {
                "list_files",
                "read_file",
                "quality_check",
                "run_command",
            }
        ),
        max_turns=6,
        plan_and_execute=False,
        graceful_final_turn=True,
        read_only_role=False,
    ),
    "default": AgentRoleProfile(
        role="default",
        title="通用全能子代理",
        description="拥有完整读写与规划能力的通用子代理，适合独立处理多步骤综合子任务。",
        system_prompt_addon=(
            "## 子代理角色：Default（通用全能子代理）\n"
            "你是一个拥有完整读写与规划能力的通用子代理。\n"
            "- 独立完成主代理委派的综合子任务，并在完成后输出清晰的交付总结。"
        ),
        allowed_tools=None,
        max_turns=10,
        plan_and_execute=True,
        graceful_final_turn=True,
        read_only_role=False,
    ),
}


def resolve_subagent_tools(
    profile: AgentRoleProfile,
    parent_registry: Mapping[str, Tool],
    parent_allowed_tool_names: frozenset[str] | None = None,
) -> tuple[dict[str, Tool], frozenset[str], list[types.Tool]]:
    """按权限衰减原则计算子代理的工具注册表、允许工具名集合及声明列表。

    子代理永远不会获得 `subagent` 工具（强制 max_depth = 1），且可用工具不会超出父代理范围。
    """

    if parent_allowed_tool_names is not None:
        candidate_names = set(parent_allowed_tool_names) & set(
            parent_registry.keys()
        )
    else:
        candidate_names = set(parent_registry.keys())

    # 强制剔除 subagent 工具，防止递归套娃（max_depth = 1）
    candidate_names.discard("subagent")

    if profile.allowed_tools is not None:
        candidate_names &= profile.allowed_tools

    if not profile.plan_and_execute:
        candidate_names.discard("update_plan")
        candidate_names.discard("reflect")

    child_registry: dict[str, Tool] = {
        name: parent_registry[name]
        for name in parent_registry
        if name in candidate_names
    }
    child_allowed_names = frozenset(child_registry.keys())
    declarations = [
        tool.to_function_declaration() for tool in child_registry.values()
    ]
    child_tools = (
        [types.Tool(function_declarations=declarations)]
        if declarations
        else []
    )
    return child_registry, child_allowed_names, child_tools


def _extract_final_text(
    new_contents: Sequence[types.Content],
    streamed_text: str,
) -> str:
    """从子代理新增历史或流式文本中提取最终汇报文本。"""

    for content in reversed(new_contents):
        if content.role == "model" and content.parts:
            texts = [part.text for part in content.parts if part.text]
            if texts:
                return "".join(texts).strip()
    return streamed_text.strip()


@dataclass
class SubagentSession:
    """单个子代理实例的独立会话上下文与运行状态。"""

    agent_id: str
    role: SubagentRole
    description: str
    history: list[types.Content] = field(default_factory=list)
    inbox: asyncio.Queue[str] = field(default_factory=asyncio.Queue) # 后台运行中的子代理与主代理之间的实时跨线程/跨协程通信
    status: SubagentStatus = "running"
    last_output: str = ""
    turns_used: int = 0
    usage: dict[str, int] = field(
        default_factory=lambda: {"input_len": 0, "output_len": 0}
    )
    error_message: str = ""
    background_task: asyncio.Task[str] | None = None # 异步事件的句柄
    run_lock: asyncio.Lock = field(default_factory=asyncio.Lock) # 互斥锁


QueryRunner = Callable[..., AsyncGenerator[Any]]


class SubagentManager:
    """管理当前运行周期内所有子代理的派发、并发控制、上下文续接与消息通信。"""

    def __init__(
        self,
        parent_registry: Mapping[str, Tool],
        *,
        parent_allowed_tool_names: frozenset[str] | None = None,
        parent_system_prompt: str = "",
        parent_plan: ExecutionPlan | None = None,
        permission_check: Callable[[Any], Awaitable[bool]] | None = None,
        event_forwarder: Callable[[Any], Awaitable[None]] | None = None,
        query_runner: QueryRunner | None = None,
        max_concurrent_subagents: int = 4,
    ) -> None:
        # 来自父级的资源
        self.parent_registry = dict(parent_registry) #父级工具仓库
        self.parent_allowed_tool_names = parent_allowed_tool_names # 父级工具白名单
        self.parent_system_prompt = parent_system_prompt # （父级基础人设）
        self.parent_plan = parent_plan # 父级的执行计划
        self.permission_check = permission_check  # （危险操作审批员
        self.event_forwarder = event_forwarder # 事件转发回调函数
        self._query_runner = query_runner # 大模型循环执行器

        #调度器特有的字段
        self.max_concurrent_subagents = max(1, max_concurrent_subagents) # 最大并发上限
        self._semaphore = asyncio.Semaphore(self.max_concurrent_subagents) # 工位信号量
        self.sessions: dict[str, SubagentSession] = {} # 员工档案花名册
        self._role_counters: dict[SubagentRole, int] = { # 角色发号机
            role: 0 for role in VALID_SUBAGENT_ROLES
        }
        self.total_usage: dict[str, int] = {"input_len": 0, "output_len": 0} # 子代理的tokens

    def _next_agent_id(self, role: SubagentRole) -> str:
            """给子代理取名字"""
            self._role_counters[role] = self._role_counters.get(role, 0) + 1
            return f"{role}-{self._role_counters[role]}"

    def _validate_phase_for_role(self, profile: AgentRoleProfile) -> None:
        if self.parent_plan is None:
            return
        phase = self.parent_plan.phase
        if phase in {"planning", "reflecting"} and not profile.read_only_role:
            raise ValueError(
                f"当前主代理处于只读阶段（{phase}），禁止派发具有写入或执行权限的子代理角色 `{profile.role}`。"
                "在只读阶段仅允许派发 `explorer` 子代理收集证据；"
                "如需派发 `worker`、`monitor` 或 `default`，请先完成计划创建或反思以进入 `executing` 阶段。"
            )

    async def _build_prompt_with_refs(
        self,
        task: str,
        context_refs: Sequence[str] | None = None, # 上下文引用
    ) -> str:
        """
        将上一个子代理的总结汇报传给下一个subagent 
        """
        # 参数校验，task不可以为空
        clean_task = task.strip()
        if not clean_task:
            raise ValueError("子代理任务指令 task 不能为空。")
        if not context_refs:
            return clean_task # clean_task 就是主代理下达的原始任务指令本身

        ref_sections: list[str] = []
        for raw_ref in context_refs:
            ref_id = str(raw_ref).strip()
            if not ref_id:
                continue # 这里会把空字符给过滤掉 比如： ["", "   "]
            ref_session = self.sessions.get(ref_id)
            if ref_session is None:
                raise ValueError(
                    f"context_refs 引用的子代理 `{ref_id}` 不存在，请先确认 agent_id。"
                )
            if (
                ref_session.status == "running"
                and ref_session.background_task is not None # 后台任务句柄存在
                and not ref_session.background_task.done() # 任务孩子事件循环中
            ):
                await ref_session.background_task # 等待sub运行结束

            ref_output = ref_session.last_output or ref_session.error_message or "（无输出）"
            ref_sections.append(
                f"### 来自子代理 `{ref_session.agent_id}`（角色：{ref_session.role}）的前置报告\n"
                f"{ref_output}"
            )

        if not ref_sections:
            return clean_task

        joined_refs = "\n\n".join(ref_sections)
        return (
            "## 关联子代理交接上下文（由系统自动注入）\n"
            f"{joined_refs}\n\n"
            "## 当前子代理任务指令\n"
            f"{clean_task}"
        )

    async def spawn(
        self,
        *,
        role: str = "explorer",
        task: str,
        description: str = "",# 任务的标题
        agent_id: str = "",
        context_refs: Sequence[str] | None = None,
        background: bool = False,
    ) -> str:
        """创建新子代理或续接已有子代理执行任务。"""

        clean_agent_id = agent_id.strip()
        if clean_agent_id:
            return await self.send(
                agent_id=clean_agent_id,
                message=task,
                context_refs=context_refs,
                wait=not background,
            )

        clean_role = role.strip().lower() or "explorer"
        if clean_role not in VALID_SUBAGENT_ROLES:
            allowed = ", ".join(VALID_SUBAGENT_ROLES)
            raise ValueError(
                f"未知的子代理角色 `{clean_role}`，允许的角色为：{allowed}。"
            )
        typed_role = cast(SubagentRole, clean_role)
        profile = ROLE_PROFILES[typed_role]
        self._validate_phase_for_role(profile) # 防止manager调用权限大于自己的sub

        full_prompt = await self._build_prompt_with_refs(task, context_refs)
        new_id = self._next_agent_id(typed_role)
        clean_desc = description.strip() or profile.title
        session = SubagentSession( # 正式创建sub
            agent_id=new_id,
            role=typed_role,
            description=clean_desc,
            status="running",
        )
        self.sessions[new_id] = session

        if background:
            session.background_task = asyncio.create_task(
                self._execute_session(session, full_prompt)
            )
            return (
                f"已启动后台子代理 `{session.agent_id}`（角色：`{session.role}` | 任务：{session.description}）。\n"
                f"- 状态：`running`\n"
                f"- 如需等待结果，请调用 `subagent(action='wait', agent_ids=['{session.agent_id}'])`\n"
                f"- 如需实时追加指令，请调用 `subagent(action='send', agent_id='{session.agent_id}', task=...)`"
            )

        return await self._execute_session(session, full_prompt)

    async def send(
        self,
        *,
        agent_id: str,
        message: str,
        context_refs: Sequence[str] | None = None,
        wait: bool = True,
    ) -> str:
        """向正在运行的后台子代理投递实时消息，或唤醒已完成的子代理继续对话。"""

        clean_id = agent_id.strip()
        if not clean_id:
            raise ValueError("必须提供有效的子代理标识 agent_id。")
        session = self.sessions.get(clean_id)
        if session is None:
            raise ValueError(f"未找到子代理 `{clean_id}`，请检查 agent_id 是否正确。")

        profile = ROLE_PROFILES[session.role]
        self._validate_phase_for_role(profile)
        full_prompt = await self._build_prompt_with_refs(message, context_refs)

        if (
            session.status == "running"
            and session.background_task is not None
            and not session.background_task.done()
        ):
            session.inbox.put_nowait(full_prompt)
            if wait:
                return await session.background_task
            return (
                f"已将实时指令投递至运行中的子代理 `{session.agent_id}` 收件箱，"
                "将在其下一轮思考前自动注入上下文。"
            )

        session.status = "running"
        if not wait:
            session.background_task = asyncio.create_task(
                self._execute_session(session, full_prompt)
            )
            return (
                f"已后台唤醒子代理 `{session.agent_id}` 继续执行新指令（状态：`running`）。\n"
                f"可调用 `subagent(action='wait', agent_ids=['{session.agent_id}'])` 获取结果。"
            )

        return await self._execute_session(session, full_prompt)

    async def wait(
        self,
        agent_ids: Sequence[str] | None = None,
    ) -> str:
        """等待一个或多个子代理完成并返回汇总报告。"""

        if agent_ids:
            targets: list[SubagentSession] = []
            for raw_id in agent_ids:
                clean_id = str(raw_id).strip()
                if not clean_id:
                    continue
                session = self.sessions.get(clean_id)
                if session is None:
                    raise ValueError(f"未找到要等待的子代理 `{clean_id}`。")
                targets.append(session)
        else:
            targets = [
                s
                for s in self.sessions.values()
                if s.status == "running" or s.background_task is not None
            ]

        if not targets:
            return "当前没有正在运行或可等待的子代理。"

        async def _await_one(session: SubagentSession) -> str:
            if session.background_task is not None:
                return await session.background_task
            return self._format_session_report(session)

        reports = await asyncio.gather(*(_await_one(s) for s in targets))
        return "\n\n---\n\n".join(reports)

    def list_sessions(self) -> str:
        """返回当前所有已创建子代理的状态概览。"""

        if not self.sessions:
            return "当前尚未创建任何子代理。"

        lines = ["### 🤖 子代理状态列表"]
        for session in self.sessions.values():
            preview = (session.last_output or session.error_message or "运行中...").splitlines()[0]
            if len(preview) > 80:
                preview = preview[:80] + "..."
            lines.append(
                f"- **`{session.agent_id}`**（角色：`{session.role}` | 状态：`{session.status}` | "
                f"累计轮数：{session.turns_used}）— {session.description}：{preview}"
            )
        return "\n".join(lines)

    async def close(self) -> None:
        """取消并清理所有尚未结束的后台子代理任务。"""

        running_tasks = [
            s.background_task
            for s in self.sessions.values()
            if s.background_task is not None and not s.background_task.done()
        ]
        for task in running_tasks:
            task.cancel()
        if running_tasks:
            await asyncio.gather(*running_tasks, return_exceptions=True)

    async def _execute_session(
        self,
        session: SubagentSession,
        prompt_text: str, # 来自  _build_prompt_with_refs
    ) -> str:# 返回一段排版工整的 Markdown 格式结项汇总结
        
        # ROLE_PROFILES 是一个全局角色字典说明用 str->AgentRoleProfile查找
        profile = ROLE_PROFILES[session.role]
        child_registry, child_allowed_names, child_tools = resolve_subagent_tools(
            profile,
            self.parent_registry,
            self.parent_allowed_tool_names,
        )
        base_prompt = self.parent_system_prompt.strip()
        child_system_prompt = (
            f"{base_prompt}\n\n{profile.system_prompt_addon}"
            if base_prompt
            else profile.system_prompt_addon
        )

        query_fn = self._query_runner
        if query_fn is None: #防止循环导入，比如：loop需要subagent，subagent又需要loop，两个模块左右脚上天
            from agent_service.core.agentic_loop import query as default_query

            query_fn = default_query

        async with session.run_lock, self._semaphore:
            next_history = [
                *session.history,
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=prompt_text)],
                ),
            ]
            streamed_chunks: list[str] = []
            forwarded_call_ids: set[str] = set()
            final_result = None

            async for event in query_fn(
                contents=next_history,
                tools=child_tools,
                permission_check=self.permission_check,
                allowed_tool_names=child_allowed_names,
                tool_registry=child_registry,
                system_prompt=child_system_prompt,
                max_turns=profile.max_turns,
                plan_and_execute=profile.plan_and_execute,
                graceful_final_turn=profile.graceful_final_turn,
                inbox=session.inbox,
            ):
                if event.type == "text.delta" and event.text:
                    streamed_chunks.append(event.text)
                elif (
                    event.type == "tool.requested"
                    and event.requires_approval# 危险工具需要人工审批
                    and self.event_forwarder is not None # 存在向外传声的通道存在
                ):
                    forwarded_call_ids.add(event.tool_call_id)
                    await self.event_forwarder(event) # 将事件推给外面
                elif event.type == "tool.completed":
                    if (
                        event.tool_result is not None
                        and not event.tool_result.is_error
                        and self.parent_plan is not None
                    ):
                        if event.tool_name in {"edit_file", "write_file"}:
                            self.parent_plan.record_edit(
                                str(event.tool_arguments.get("path", "")) # 标记修改
                            )
                        elif event.tool_name in {
                            "read_file",
                            "quality_check",
                            "run_command",
                        }:
                            self.parent_plan.mark_verified() # 验证修改完毕
                    if (
                        event.tool_call_id in forwarded_call_ids
                        and self.event_forwarder is not None
                    ):
                        await self.event_forwarder(event) # 向外传递工具执行完毕事件
                elif event.type in {"run.completed", "run.failed"}:
                    final_result = event.result

            if final_result is not None:
                session.history = list(final_result.history)
                session.turns_used += final_result.turns
                in_tokens = int(final_result.usage.get("input_len", 0))
                out_tokens = int(final_result.usage.get("output_len", 0))
                session.usage["input_len"] += in_tokens
                session.usage["output_len"] += out_tokens
                self.total_usage["input_len"] += in_tokens
                self.total_usage["output_len"] += out_tokens

                output_text = _extract_final_text(
                    final_result.new_contents,
                    "".join(streamed_chunks),
                )
                if final_result.reason == "completed":
                    session.status = "completed"
                    session.last_output = output_text or "（子代理已完成任务，无额外文本输出）"
                    session.error_message = ""
                else:
                    session.status = "failed"
                    err_str = (
                        str(final_result.error)
                        if final_result.error is not None
                        else f"子代理因 {final_result.reason} 结束"
                    )
                    session.error_message = err_str
                    if output_text:
                        session.last_output = f"{output_text}\n\n[运行终止原因]: {err_str}"
                    else:
                        session.last_output = f"[运行失败]: {err_str}"
            else:
                session.status = "failed"
                session.error_message = "子代理未返回结果。"
                session.last_output = "[运行失败]: 子代理未返回结果。"

            return self._format_session_report(session)

    @staticmethod
    def _format_session_report(session: SubagentSession) -> str:
        header = (
            f"### 🤖 子代理报告 `{session.agent_id}`\n"
            f"- **角色**：`{session.role}`（{session.description}）\n"
            f"- **状态**：`{session.status}`（累计轮数：{session.turns_used}）"
        )
        body = session.last_output or session.error_message or "（无内容）"
        return f"{header}\n\n{body}"
