from collections.abc import Mapping

from google.genai import types

from agent_service.adapters.security.workspace import WorkspacePathPolicy
from agent_service.adapters.tools.edit_file import EditFileTool
from agent_service.adapters.tools.fetch_web_page import FetchWebPageTool
from agent_service.adapters.tools.list_files import ListFilesTool
from agent_service.adapters.tools.plan_tool import UpdatePlanTool
from agent_service.adapters.tools.quality_check import QualityCheckTool
from agent_service.adapters.tools.read_file import ReadFileTool
from agent_service.adapters.tools.reflect_tool import ReflectTool
from agent_service.adapters.tools.run_command import RunCommandTool
from agent_service.adapters.tools.subagent_tool import SubagentTool
from agent_service.adapters.tools.web_search import WebSearchTool
from agent_service.adapters.tools.write_file import WriteFileTool
from agent_service.adapters.tools.yaml_loader import LoadYamlTool
from agent_service.kernel.dispatch.base import Tool


def create_tool_registry(
    workspace: WorkspacePathPolicy | None = None,
) -> dict[str, Tool]:
    """每次组装创建独立工具实例；工作区能力由调用方显式配置。"""
    tools: list[Tool] = [
        ListFilesTool(workspace),
        ReadFileTool(workspace),
        WriteFileTool(workspace),
        EditFileTool(workspace),
        LoadYamlTool(workspace),
        RunCommandTool(),
        UpdatePlanTool(),
        ReflectTool(),
        SubagentTool(),
        WebSearchTool(),
        FetchWebPageTool(),
    ]
    if workspace is not None:
        tools.append(QualityCheckTool(workspace))
    registry = {tool.name: tool for tool in tools}
    if len(registry) != len(tools):
        raise ValueError("工具注册失败：工具名称不能重复")
    return registry


def get_function_declarations(registry: Mapping[str, Tool]) -> list[types.Tool]:
    declarations = [tool.to_function_declaration() for tool in registry.values()]
    return [types.Tool(function_declarations=declarations)] if declarations else []
