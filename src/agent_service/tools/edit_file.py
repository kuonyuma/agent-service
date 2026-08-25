import asyncio
from pathlib import Path
from typing import Any, ClassVar

from agent_service.tools.base import Tool, ToolResult
from agent_service.tools.workspace import WorkspacePathError, WorkspacePathPolicy


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

    def __init__(self, workspace_policy: WorkspacePathPolicy | None = None) -> None:
        self.workspace_policy = workspace_policy

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        path_str = parameter.get("path", "")
        old_str = parameter.get("old_string", "")
        new_str = parameter.get("new_string", "")

        if not all(isinstance(value, str) for value in (path_str, old_str, new_str)):
            return ToolResult(content="路径和替换内容必须是字符串", is_error=True)

        if self.workspace_policy is not None:
            try:
                path = self.workspace_policy.resolve_existing(
                    path_str,
                    expected="file",
                )
                file_text = await asyncio.to_thread(
                    self.workspace_policy.read_text,
                    path,
                )
            except WorkspacePathError as exc:
                return ToolResult(content=f"修改文件被拒绝：{exc}", is_error=True)
        else:
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
        if self.workspace_policy is not None:
            try:
                self.workspace_policy.validate_write_content(result)
                await asyncio.to_thread(path.write_text, result, encoding="utf-8")
            except (WorkspacePathError, OSError, UnicodeEncodeError) as exc:
                return ToolResult(content=f"修改文件被拒绝：{exc}", is_error=True)
        else:
            path.write_text(result, encoding="utf-8")
        return ToolResult(content=f"成功！文件 {path.name} 已被修改。", is_error=False)
