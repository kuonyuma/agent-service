from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from google.genai import types


@dataclass
class ToolResult:
    content: str
    is_error: bool


class Tool(ABC):
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = True

    @abstractmethod
    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        pass

    def to_function_declaration(self):
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=self.input_schema,
        )
