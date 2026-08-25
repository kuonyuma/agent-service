from collections.abc import Mapping

from google.genai import types

from agent_service.tools.base import Tool
from agent_service.tools.edit_file import EditFileTool
from agent_service.tools.list_files import ListFilesTool
from agent_service.tools.quality_check import QualityCheckTool
from agent_service.tools.read_file import ReadFileTool
from agent_service.tools.run_command import RunCommandTool
from agent_service.tools.workspace import WorkspacePathPolicy
from agent_service.tools.write_file import WriteFileTool
from agent_service.tools.yaml_loader import LoadYamlTool

WEB_AUTO_TOOL_NAMES = frozenset({"list_files", "read_file"})
WEB_APPROVAL_TOOL_NAMES = frozenset({"write_file", "edit_file"})

ALL_TOOLS: list[Tool] = [
    ListFilesTool(),
    ReadFileTool(),
    WriteFileTool(),
    EditFileTool(),
    LoadYamlTool(),
    RunCommandTool(),
]

TOOL_REGISTRY: dict[str, Tool] = {tool.name: tool for tool in ALL_TOOLS}
if len(TOOL_REGISTRY) != len(ALL_TOOLS):
    raise ValueError("工具注册失败：工具名称不能重复")


def find_tool(
    name: str,
    registry: Mapping[str, Tool] | None = None,
) -> Tool | None:
    active_registry = registry if registry is not None else TOOL_REGISTRY
    return active_registry.get(name)


def get_function_declarations(
    registry: Mapping[str, Tool] | None = None,
) -> list[types.Tool]:
    active_registry = registry if registry is not None else TOOL_REGISTRY
    declarations = [
        tool.to_function_declaration() for tool in active_registry.values()
    ]
    return [types.Tool(function_declarations=declarations)]


def get_read_only_function_declarations() -> list[types.Tool]:
    """返回 Web 运行时允许暴露的只读工具声明。"""

    declarations = [
        tool.to_function_declaration()
        for tool in TOOL_REGISTRY.values()
        if tool.name in WEB_AUTO_TOOL_NAMES
    ]
    return [types.Tool(function_declarations=declarations)] if declarations else []


def get_read_only_tool_names() -> frozenset[str]:
    """返回只读工具名称，供实际执行层再次校验。"""

    return WEB_AUTO_TOOL_NAMES


def create_web_tool_registry(
    workspace_policy: WorkspacePathPolicy,
    *,
    enable_mutating_tools: bool = False,
    enable_quality_tool: bool = False,
) -> dict[str, Tool]:
    """显式构造 Web 工具表，新增全局工具不会被自动暴露。"""

    tools: list[Tool] = [
        ListFilesTool(workspace_policy),
        ReadFileTool(workspace_policy),
    ]
    if enable_mutating_tools:
        tools.extend(
            [
                WriteFileTool(workspace_policy),
                EditFileTool(workspace_policy),
            ]
        )
    if enable_quality_tool:
        tools.append(QualityCheckTool(workspace_policy))
    return {tool.name: tool for tool in tools}
