from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from google.genai import types


@dataclass
class ToolResult:
    content: str
    is_error: bool


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[dict[str, Any]]
    read_only: ClassVar[bool] = True

    @abstractmethod
    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        pass

    def to_function_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=types.Schema(**self.input_schema),
        )
