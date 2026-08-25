import asyncio
import os
import subprocess
import sys
from typing import Any, ClassVar

from agent_service.tools.base import Tool, ToolResult
from agent_service.tools.workspace import WorkspacePathPolicy


class QualityCheckTool(Tool):
    """只执行服务端固定 argv 的静态质量检查，不接受命令字符串。"""

    name = "run_quality_check"
    description = "在当前 Web 工作区运行白名单中的静态代码质量检查"
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "check": {
                "type": "STRING",
                "enum": ["ruff", "pyright"],
                "description": "要运行的固定检查类型",
            }
        },
        "required": ["check"],
    }
    read_only = False

    def __init__(
        self,
        workspace_policy: WorkspacePathPolicy,
        *,
        timeout_seconds: float = 60.0,
        max_output_bytes: int = 100_000,
    ) -> None:
        self.workspace_policy = workspace_policy
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        check = parameter.get("check")
        if not isinstance(check, str):
            return ToolResult(content="质量检查类型必须是字符串。", is_error=True)
        commands = {
            "ruff": [sys.executable, "-m", "ruff", "check", "."],
            "pyright": [sys.executable, "-m", "pyright"],
        }
        command = commands.get(check)
        if command is None:
            return ToolResult(content="不允许的质量检查类型。", is_error=True)

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                shell=False,
                cwd=self.workspace_policy.root,
                env=self._sanitized_environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                content=f"质量检查超过 {self.timeout_seconds:g} 秒，已终止。",
                is_error=True,
            )
        except OSError as exc:
            return ToolResult(content=f"质量检查启动失败：{exc}", is_error=True)

        output = result.stdout + result.stderr
        output = self._truncate_output(output)
        return ToolResult(
            content=(output or "检查没有输出。") + f"\nreturncode={result.returncode}",
            is_error=result.returncode != 0,
        )

    @staticmethod
    def _sanitized_environment() -> dict[str, str]:
        allowed = {
            "SYSTEMROOT",
            "WINDIR",
            "PATH",
            "PATHEXT",
            "TEMP",
            "TMP",
            "LANG",
            "LC_ALL",
        }
        environment = {key: value for key, value in os.environ.items() if key in allowed}
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONNOUSERSITE"] = "1"
        return environment

    def _truncate_output(self, output: str) -> str:
        raw = output.encode("utf-8")
        if len(raw) <= self.max_output_bytes:
            return output
        truncated = raw[: self.max_output_bytes].decode("utf-8", errors="ignore")
        return truncated + "\n[输出已按字节上限截断]"
