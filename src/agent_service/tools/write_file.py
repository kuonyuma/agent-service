from pathlib import Path
from agent_service.tools.base import Tool, ToolResult
from typing import Any


class WriteFileTool(Tool):
    name = "write_file"
    description = "创建一个新文件并写入内容，或者覆盖已有的原文件"
    input_schema = {
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
        absolute_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(absolute_path, "w", encoding="utf-8") as f:
                f.write(content)
                return ToolResult(
                    content=f"操作提示：\nwrite_file成功写入{content}", is_error=False
                )
        except Exception as e:
            return ToolResult(content=f"写入操作出错\n出错信息:{e}", is_error=True)
