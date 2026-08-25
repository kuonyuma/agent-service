from google.genai import types
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

from agent_service.core.agentic_loop import LoopResult, ToolApprovalRequest
from agent_service.core.runner import AgentRunner

console = Console()


class App:
    def __init__(self, runner: AgentRunner | None = None) -> None:
        self.contents: list[types.Content] = []
        self.session = PromptSession()
        self.runner = runner or AgentRunner()

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
        with Live(Markdown(full_text), console=console, refresh_per_second=15) as live:
            async for loop_event in self.runner.run_turn(
                history=self.contents,
                user_query=user_query,
                permission_check=self.permission_check,
            ):
                if loop_event.type == "text.delta":
                    full_text += loop_event.text
                    live.update(Markdown(full_text))
                elif loop_event.type == "tool.requested":
                    live.update(Markdown(full_text + "\n\n模型使用工具中.."))
                elif loop_event.type in {"run.completed", "run.failed"}:
                    result = loop_event.result

        if result:
            self.contents = result.history
            if result.reason == "error":
                error_message = str(result.error) if result.error else "未知错误"
                console.print(f"[red]任务出错：{error_message}[/red]")
            elif result.reason == "max_turns":
                console.print("[yellow]代理陷入死循环[/yellow]")

    async def permission_check(self, request: ToolApprovalRequest) -> bool:
        console.print(
            "\n[red]⚠️ 警告：大模型申请执行危险写入工具 "
            f"`{request.tool_name}`[/red]"
        )
        console.print(f"[yellow]执行参数：{request.arguments}[/yellow]")

        answer: str = await self.session.prompt_async("是否允许执行？(y/n) > ")
        return answer.strip().lower() == "y"
