import asyncio
import subprocess
from typing import Any, ClassVar

from agent_service.tools.base import Tool, ToolResult


class RunCommandTool(Tool):
    name = "run_command"
    description = (
        "在系统终端中执行一条 shell 命令并返回输出结果。"
        "适用于运行脚本、安装依赖、查看系统信息等操作。"
        "命令会在当前工作目录下执行。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "command": {
                "type": "STRING",
                "description": "要执行的 shell 命令，例如 'ls -la' 或 'pip install rich'",
            },
        },
        "required": ["command"],
    }
    read_only = False

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        command = parameter.get("command", "")

        if command == "":
            return ToolResult(is_error=True, content="执行的命令command为空")

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                shell=True,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            return ToolResult(content=f"执行命令出错\n错误信息:{exc}", is_error=True)

        out = result.stdout + result.stderr + f"returncode{result.returncode}"

        return ToolResult(content=out, is_error=False)
