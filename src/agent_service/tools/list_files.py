from pathlib import Path
from typing import Any, ClassVar

from agent_service.tools.base import Tool, ToolResult
from agent_service.tools.workspace import WorkspacePathError, WorkspacePathPolicy


class ListFilesTool(Tool):
    name = "list_files"
    description = (
        "获取指定目录下的所有文件和子目录列表。如果不传路径，默认列出当前目录."
    )
    read_only = True
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "path": {"type": "STRING", "description": "要查看的目录绝对路径或相对路径"}
        },
    }

    def __init__(self, workspace_policy: WorkspacePathPolicy | None = None) -> None:
        self.workspace_policy = workspace_policy

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        raw_path = parameter.get("path", ".")
        if self.workspace_policy is not None:
            try:
                path = self.workspace_policy.resolve_existing(
                    raw_path,
                    expected="directory",
                )
                items = self.workspace_policy.list_directory(path)
                display_path = self.workspace_policy.display_path(path)
                content = f"路径:{display_path}下的内容为:" + "\n".join(items)
                self.workspace_policy.ensure_output(content)
            except WorkspacePathError as exc:
                return ToolResult(content=f"列出目录被拒绝：{exc}", is_error=True)
            return ToolResult(content=content, is_error=False)

        path = Path(raw_path)
        absolute_path = path.resolve()
        if not absolute_path.exists():
            content = "不存在该路径"
            return ToolResult(content=content, is_error=True)
        items: list = []
        for item in absolute_path.iterdir():
            if item.is_dir():
                items.append(item.name + "/")
            else:
                items.append(item.name)
        return ToolResult(
            content=f"路径:{absolute_path}下的内容为:" + "\n".join(items),
            is_error=False,
        )
