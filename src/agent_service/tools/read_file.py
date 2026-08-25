import asyncio
from pathlib import Path
from typing import Any, ClassVar

from agent_service.tools.base import Tool, ToolResult
from agent_service.tools.workspace import WorkspacePathError, WorkspacePathPolicy


class ReadFileTool(Tool):
    name = "read_file"
    description = "读取文件内容"
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {"path": {"type": "STRING", "description": "读取文件的路径"}},
        "required": ["path"],
    }
    read_only = True

    def __init__(self, workspace_policy: WorkspacePathPolicy | None = None) -> None:
        self.workspace_policy = workspace_policy

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        raw_path = parameter.get("path", ".")

        if self.workspace_policy is not None:
            try:
                path = self.workspace_policy.resolve_existing(
                    raw_path,
                    expected="file",
                )
                text = await asyncio.to_thread(self.workspace_policy.read_text, path)
            except WorkspacePathError as exc:
                return ToolResult(content=f"读取文件被拒绝：{exc}", is_error=True)
            return ToolResult(content=text, is_error=False)

        path = Path(raw_path)

        if not path.exists():
            return ToolResult(content=f"该路径{path}不存在或路径为空", is_error=True)
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return ToolResult(content=f"读取文件出错\n错误信息:{exc}", is_error=True)
        return ToolResult(content=text, is_error=False)
