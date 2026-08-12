from pathlib import Path
from agent_service.tools.base import Tool, ToolResult
from typing import Any
import yaml


class LoadYamlTool(Tool):
    name = "load_yaml"
    description = "读取config/config.yaml文件"
    input_schema = {
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
            with open(absolute_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            return ToolResult(content=f"YAML 解析失败: {e}", is_error=True)
        if config is None:
            return ToolResult(content=f"文件为空: {absolute_path}", is_error=True)
        content = yaml.dump(config, allow_unicode=True)
        return ToolResult(content=content, is_error=False)
