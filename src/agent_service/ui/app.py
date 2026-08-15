from google.genai import types
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

from agent_service.core.agentic_loop import LoopResult, query
from agent_service.core.context import compress_context
from agent_service.tools.index import get_function_declarations

console = Console()


class App:
    def __init__(self) -> None:
        self.contents: list[types.Content] = []
        self.session = PromptSession()

    async def run(self):

        while True:
            with patch_stdout():
                user_input: str = await self.session.prompt_async(">")
                user_text = user_input.strip()
            if not user_text:
                continue

            await self.run_turn(user_text)

    async def run_turn(self, user_query: str):

        user_content = types.Content(
            role="user", parts=[types.Part.from_text(text=user_query)]
        )
        self.contents.append(user_content)

        self.contents = await compress_context(self.contents)

        result: LoopResult | None = None
        full_text: str = ""
        with Live(Markdown(full_text), console=console, refresh_per_second=15) as live:
            async for loop_event in query(
                contents=self.contents,
                tools=get_function_declarations(),
                permission_check=self.permission_check,
            ):
                if loop_event.type == "text":
                    full_text += loop_event.text
                    live.update(Markdown(full_text))
                elif loop_event.type == "tool_start":
                    live.update(Markdown(full_text + "\n\n模型使用工具中.."))
                elif loop_event.type == "turn_complete":
                    result = loop_event.result

        if result:
            if result.reason == "error":
                console.print("[red]任务出错[/red]")
            elif result.reason == "max_turns":
                console.print("[yellow]代理陷入死循环[/yellow]")

    async def permission_check(self, name: str, parameter: dict) -> bool:
        console.print(f"\n[red]⚠️ 警告：大模型申请执行危险写入工具 `{name}`[/red]")
        console.print(f"[yellow]执行参数：{parameter}[/yellow]")

        answer: str = await self.session.prompt_async("是否允许执行？(y/n) > ")
        return answer.strip().lower() == "y"
