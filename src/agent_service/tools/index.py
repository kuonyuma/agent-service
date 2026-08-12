from agent_service.tools.list_files import ListFilesTool
from agent_service.tools.read_file import ReadFileTool
from agent_service.tools.write_file import WriteFileTool
from agent_service.tools.edit_file import EditFileTool
from agent_service.tools.yaml_loader import LoadYamlTool
from agent_service.tools.run_command import RunCommandTool
from agent_service.tools.base import Tool
from google.genai import types


ALL_TOOLS: list[Tool] = [
    ListFilesTool(),
    ReadFileTool(),
    WriteFileTool(),
    EditFileTool(),
    LoadYamlTool(),
    RunCommandTool(),
]


def find_tool(name: str) -> Tool | None:
    for i in ALL_TOOLS:
        if i.name == name:
            return i
    return None


def get_function_declarations() -> list[types.Tool]:
    declarations = [i.to_function_declaration() for i in ALL_TOOLS]
    return [types.Tool(function_declarations=declarations)]
