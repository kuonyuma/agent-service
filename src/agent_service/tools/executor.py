import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from google.genai import types

from agent_service.tools.base import Tool, ToolResult
from agent_service.tools.index import TOOL_REGISTRY


@dataclass(frozen=True)
class ToolExecution:
    """一次工具执行及其结构化结果。"""

    call_id: str
    name: str
    arguments: dict[str, Any]
    result: ToolResult


@dataclass(frozen=True)
class ToolExecutionBatch:
    """返回给模型的内容，以及供调用方审计的执行明细。"""

    content: types.Content
    executions: list[ToolExecution]


def tool_result_part(
    name: str,
    result: ToolResult,
    call_id: str = "",
) -> types.Part:
    """把工具结果转换成 Gemini FunctionResponse，同时保留错误标记。"""

    part = types.Part.from_function_response(
        name=name,
        response={"result": result.content, "is_error": result.is_error},
    )
    if call_id and part.function_response is not None:
        part.function_response.id = call_id
    return part


async def execute_tools(
    function_calls: list[types.FunctionCall],
    allowed_tool_names: frozenset[str] | None = None,
    approved_tool_call_ids: frozenset[str] = frozenset(),
    tool_registry: Mapping[str, Tool] | None = None,
    max_concurrency: int = 4,
) -> ToolExecutionBatch:
    active_registry = tool_registry if tool_registry is not None else TOOL_REGISTRY
    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def _run_single(
        fc: types.FunctionCall,
    ) -> tuple[ToolExecution, types.Part]:
        name = fc.name or ""
        call_id = fc.id or ""
        args = dict(fc.args or {})
        t = active_registry.get(name)
        if allowed_tool_names is not None and name not in allowed_tool_names:
            result = ToolResult(
                content=f"当前运行环境未开放工具：{name}",
                is_error=True,
            )
        elif t is None:
            result = ToolResult(content=f"未知的工具{name}", is_error=True)
        elif not t.read_only and call_id not in approved_tool_call_ids:
            result = ToolResult(
                content="该工具调用未获得本次审批，已拒绝执行。",
                is_error=True,
            )
        else:
            async with semaphore:
                result = await t.run(parameter=args)

        execution = ToolExecution(
            call_id=call_id,
            name=name,
            arguments=args,
            result=result,
        )
        return execution, tool_result_part(name, result, call_id)

    pairs = await asyncio.gather(*(_run_single(fc) for fc in function_calls))
    executions = [pair[0] for pair in pairs]
    parts = [pair[1] for pair in pairs]

    return ToolExecutionBatch(
        content=types.Content(role="user", parts=parts),
        executions=executions,
    )
