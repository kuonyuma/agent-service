import asyncio
from pathlib import Path
from typing import Any, ClassVar

from agent_service.tools.base import Tool, ToolResult
from agent_service.tools.workspace import WorkspacePathError, WorkspacePathPolicy


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

    def __init__(self, workspace_policy: WorkspacePathPolicy | None = None) -> None:
        self.workspace_policy = workspace_policy

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        str_path = parameter.get("path", "")
        content = parameter.get("content", "")
        if str_path == "" or content == "":
            return ToolResult(content="路径path或内容content为空", is_error=True)

        if not isinstance(content, str):
            return ToolResult(content="写入内容content必须是字符串", is_error=True)

        if self.workspace_policy is not None:
            try:
                absolute_path = self.workspace_policy.resolve_for_write(str_path)
                self.workspace_policy.validate_write_content(content)
                await asyncio.to_thread(
                    absolute_path.write_text,
                    content,
                    encoding="utf-8",
                )
            except (WorkspacePathError, OSError, UnicodeEncodeError) as exc:
                return ToolResult(content=f"写入文件被拒绝：{exc}", is_error=True)
            byte_count = len(content.encode("utf-8"))
            display_path = self.workspace_policy.display_path(absolute_path)
            return ToolResult(
                content=f"write_file 成功写入 {display_path}（{byte_count} 字节）",
                is_error=False,
            )

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
