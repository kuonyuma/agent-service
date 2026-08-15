from pathlib import Path
from typing import Any, ClassVar

from agent_service.tools.base import Tool, ToolResult


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Edit a file by replacing an exact string with a new string. "
        "old_string must appear exactly once in the file — include enough "
        "surrounding lines to make it unique. Read the file first so "
        "old_string matches the current content exactly."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "path": {
                "type": "STRING",
                "description": "Path to the file to edit.",
            },
            "old_string": {
                "type": "STRING",
                "description": "The exact text to replace. Must occur exactly once.",
            },
            "new_string": {
                "type": "STRING",
                "description": "The text to replace it with.",
            },
        },
        "required": ["path", "old_string", "new_string"],
    }
    read_only = False

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        path_str = parameter.get("path", "")
        old_str = parameter.get("old_string", "")
        new_str = parameter.get("new_string", "")

        path = Path(path_str).resolve()

        if not path.exists():
            content = "路径不存在"
            return ToolResult(content=content, is_error=True)
        file_text = path.read_text(encoding="utf-8")

        count = file_text.count(old_str)

        if count > 1:
            content = f"被修改字符串{old_str}在文中出现{count}次，无法修改"
            return ToolResult(content=content, is_error=True)
        if count == 0:
            content = f"未找到被修改字符串{old_str}，无法修改"
            return ToolResult(content=content, is_error=True)
        result = file_text.replace(old_str, new_str)
        path.write_text(result, encoding="utf-8")
        return ToolResult(content=f"成功！文件 {path.name} 已被修改。", is_error=False)
