"""唯一组装入口：选择外部实现，向内核注入模型、工具表和按次绑定工厂。"""

from collections.abc import AsyncGenerator, Mapping
from pathlib import Path
from typing import Any

from google.genai import types

from agent_service.adapters.cli.app import App
from agent_service.adapters.collaboration.bindings import ToolBindingFactory
from agent_service.adapters.gemini.model import GeminiModel
from agent_service.adapters.security.coordinator import RunCoordinator
from agent_service.adapters.security.reviewer import TextReviewer
from agent_service.adapters.security.workspace import WorkspacePathPolicy
from agent_service.adapters.tools.index import create_tool_registry
from agent_service.config.settings import settings
from agent_service.kernel.dispatch.base import Tool
from agent_service.kernel.model.port import ModelGateway
from agent_service.kernel.runtime.events import LoopEvent
from agent_service.kernel.runtime.loop import query
from agent_service.kernel.runtime.runner import AgentRunner


def create_runner(
    *,
    model: ModelGateway | None = None,
    tools: list[types.Tool] | None = None,
    tool_registry: Mapping[str, Tool] | None = None,
    allowed_tool_names: frozenset[str] | None = None,
    system_prompt: str | None = None,
    max_turns: int = 10,
    max_tokens: int | None = None,
    plan_and_execute: bool = True,
    workspace: Path | None = None,
) -> AgentRunner:
    gateway = model if model is not None else GeminiModel()
    token_limit = (
        max_tokens if max_tokens is not None else settings.model_config.max_tokens
    )

    def child_query(**kwargs: Any) -> AsyncGenerator[LoopEvent]:
        return query(
            model=gateway, max_tokens=token_limit, binding_factory=bindings, **kwargs
        )

    bindings = ToolBindingFactory(child_query)
    registry = tool_registry
    if registry is None:
        registry = create_tool_registry(WorkspacePathPolicy(workspace or Path.cwd()))
    return AgentRunner(
        model=gateway,
        binding_factory=bindings,
        tool_registry=registry,
        tools=tools,
        allowed_tool_names=allowed_tool_names,
        system_prompt=system_prompt
        if system_prompt is not None
        else settings.system_prompt,
        max_turns=max_turns,
        max_tokens=token_limit,
        plan_and_execute=plan_and_execute,
    )


def create_app(runner: AgentRunner, reviewer: TextReviewer | None = None) -> App:
    return App(RunCoordinator(runner, reviewer, settings.safety))
