import asyncio
from pathlib import Path
from typing import Any, ClassVar

import yaml

from agent_service.tools.base import Tool, ToolResult


class LoadYamlTool(Tool):
    name = "load_yaml"
    description = "读取config/config.yaml文件"
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "path": {"type": "STRING", "description": "查看配置文件的绝对或相对路径"}
        },
    }

    read_only = True

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        str_path = parameter.get("path", "")

        if str_path == "":
            content = "查询配置文件的字符串路径为空"
            return ToolResult(content=content, is_error=True)
        absolute_path = Path(str_path).resolve()
        if not absolute_path.exists():
            content = f" 路径:{absolute_path}下文件不存在"
            return ToolResult(content=content, is_error=True)
        try:
            yaml_text = await asyncio.to_thread(
                absolute_path.read_text, encoding="utf-8"
            )
        except (OSError, UnicodeDecodeError) as exc:
            return ToolResult(content=f"读取配置文件失败: {exc}", is_error=True)
        try:
            config = await asyncio.to_thread(yaml.safe_load, yaml_text)
        except yaml.YAMLError as exc:
            return ToolResult(content=f"YAML 解析失败: {exc}", is_error=True)
        if config is None:
            return ToolResult(content=f"文件为空: {absolute_path}", is_error=True)
        content = yaml.dump(config, allow_unicode=True)
        return ToolResult(content=content, is_error=False)
