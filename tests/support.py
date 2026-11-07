"""测试组装：显式使用无工作区约束的临时文件工具，不隐藏生产内核的依赖。"""

from typing import Any

from agent_service.adapters.tools.index import create_tool_registry
from agent_service.adapters.tools.index import get_function_declarations as declarations
from agent_service.bootstrap import create_runner as assemble_runner
from agent_service.kernel.runtime.loop import query as run_query


def create_runner(**kwargs: Any):
    kwargs.setdefault("tool_registry", create_tool_registry())
    return assemble_runner(**kwargs)


def get_function_declarations(registry=None):
    return declarations(registry if registry is not None else create_tool_registry())


def query(contents, tools, **kwargs: Any):
    runner = create_runner(
        tool_registry=kwargs.pop("tool_registry", None) or create_tool_registry(),
        system_prompt=kwargs.pop("system_prompt", "你是一位编程助手"),
    )
    return run_query(
        contents,
        tools,
        model=runner.model,
        tool_registry=runner.tool_registry,
        system_prompt=runner.system_prompt,
        binding_factory=runner.binding_factory,
        **kwargs,
    )
