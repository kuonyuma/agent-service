from google.genai import types
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

from agent_service.core.agentic_loop import LoopResult, ToolApprovalRequest
from agent_service.core.plan import AgentPhase, ExecutionPlan
from agent_service.core.runner import AgentRunner

console = Console()


def _build_display_markdown(
    full_text: str,
    plan: ExecutionPlan | None,
    status_hint: str = "",
) -> Markdown:
    sections: list[str] = []
    if plan is not None and plan.has_state():
        sections.append(plan.format_markdown())
    if full_text:
        sections.append(full_text)
    if status_hint:
        sections.append(status_hint)
    return Markdown("\n\n---\n\n".join(sections) if sections else "")


def _phase_label(phase: AgentPhase) -> str:
    if phase == "planning":
        return "🔍 规划阶段"
    if phase == "reflecting":
        return "🤔 反思阶段"
    return "⚙️ 执行阶段"


class App:
    def __init__(self, runner: AgentRunner | None = None) -> None:
        self.contents: list[types.Content] = []
        self.session = PromptSession()
        self.runner = runner or AgentRunner()
        self._active_live: Live | None = None

    async def run(self):

        while True:
            with patch_stdout():
                user_input: str = await self.session.prompt_async(">")
                user_text = user_input.strip()
            if not user_text:
                continue

            await self.run_turn(user_text)

    async def run_turn(self, user_query: str):
        result: LoopResult | None = None
        full_text: str = ""
        current_plan: ExecutionPlan | None = None
        with Live(
            _build_display_markdown(full_text, current_plan),
            console=console,
            refresh_per_second=15,
        ) as live:
            self._active_live = live
            try:
                async for loop_event in self.runner.run_turn(
                    history=self.contents,
                    user_query=user_query,
                    permission_check=self.permission_check,
                ):
                    if loop_event.plan is not None:
                        current_plan = loop_event.plan

                    if loop_event.type == "text.delta":
                        full_text += loop_event.text
                        live.update(_build_display_markdown(full_text, current_plan))
                    elif loop_event.type == "plan.updated":
                        live.update(
                            _build_display_markdown(
                                full_text,
                                current_plan,
                                f"{_phase_label(loop_event.phase)}：执行计划已更新..",
                            )
                        )
                    elif loop_event.type == "reflection.updated":
                        live.update(
                            _build_display_markdown(
                                full_text,
                                current_plan,
                                f"{_phase_label(loop_event.phase)}：反思状态已更新..",
                            )
                        )
                    elif loop_event.type == "tool.requested":
                        live.update(
                            _build_display_markdown(
                                full_text,
                                current_plan,
                                f"{_phase_label(loop_event.phase)}：模型调用工具 `{loop_event.tool_name}` 中..",
                            )
                        )
                    elif loop_event.type in {"run.completed", "run.failed"}:
                        result = loop_event.result
                        if result and result.plan is not None:
                            current_plan = result.plan
                        live.update(_build_display_markdown(full_text, current_plan))
            finally:
                self._active_live = None

        if result:
            self.contents = result.history
            if result.reason == "error":
                error_message = str(result.error) if result.error else "未知错误"
                console.print(f"[red]任务出错：{error_message}[/red]")
            elif result.reason == "max_turns":
                console.print("[yellow]代理陷入死循环[/yellow]")

    async def permission_check(self, request: ToolApprovalRequest) -> bool:
        if self._active_live is not None:
            self._active_live.stop()
        try:
            console.print(
                f"\n[red]⚠️ 警告：大模型申请执行危险写入工具 `{request.tool_name}`[/red]"
            )
            console.print(f"[yellow]执行参数：{request.arguments}[/yellow]")

            answer: str = await self.session.prompt_async("是否允许执行？(y/n) > ")
            return answer.strip().lower() == "y"
        finally:
            if self._active_live is not None:
                self._active_live.start()
