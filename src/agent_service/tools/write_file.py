import asyncio
from pathlib import Path
from typing import Any, ClassVar

from agent_service.tools.base import Tool, ToolResult


class WriteFileTool(Tool):
    name = "write_file"
    description = "创建一个新文件并写入内容，或者覆盖已有的原文件"
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "path": {"type": "STRING", "description": "写入文件的绝对或相对路径"},
            "content": {"type": "STRING", "description": "写入文件的内容"},
        },
        "required": ["path", "content"],
    }
    read_only = False

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        str_path = parameter.get("path", "")
        content = parameter.get("content", "")
        if str_path == "" or content == "":
            return ToolResult(content="路径path或内容content为空", is_error=True)
        absolute_path = Path(str_path).resolve()

        try:
            await asyncio.to_thread(
                absolute_path.parent.mkdir, parents=True, exist_ok=True
            )
            await asyncio.to_thread(absolute_path.write_text, content, encoding="utf-8")
        except (OSError, UnicodeEncodeError) as exc:
            return ToolResult(content=f"写入操作出错\n出错信息:{exc}", is_error=True)
        return ToolResult(
            content=f"操作提示：\nwrite_file成功写入{content}", is_error=False
        )
