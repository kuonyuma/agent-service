import asyncio
from pathlib import Path
from typing import Any, ClassVar

from agent_service.tools.base import Tool, ToolResult


class ReadFileTool(Tool):
    name = "read_file"
    description = "读取文件内容"
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {"path": {"type": "STRING", "description": "读取文件的路径"}},
        "required": ["path"],
    }
    read_only = True

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        path = Path(parameter.get("path", "."))

        if not path.exists():
            return ToolResult(content=f"该路径{path}不存在或路径为空", is_error=True)
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return ToolResult(content=f"读取文件出错\n错误信息:{exc}", is_error=True)
        return ToolResult(content=text, is_error=False)
