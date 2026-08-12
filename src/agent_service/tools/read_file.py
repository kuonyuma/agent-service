from agent_service.tools.base import ToolResult, Tool
from pathlib import Path
from typing import Any


class ReadFileTool(Tool):
    name = "read_file"
    description = "读取文件内容"
    input_schema = {
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
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
                return ToolResult(content=text, is_error=False)
        except Exception as e:
            return ToolResult(content=f"读取文件出错\n错误信息:{e}", is_error=True)
