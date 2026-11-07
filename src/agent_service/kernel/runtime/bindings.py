from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from agent_service.kernel.dispatch.approval import PermissionCheck
from agent_service.kernel.dispatch.base import Tool
from agent_service.kernel.runtime.control import RunControl
from agent_service.kernel.runtime.events import LoopEvent
from agent_service.kernel.strategy.plan import ExecutionPlan

ToolRegistry = Mapping[str, Tool]
EventForwarder = Callable[[LoopEvent], Awaitable[None]]


@dataclass
class RunBindings:
    """外核按次绑定有状态工具；内核只管理接口及其生命周期。"""

    registry: ToolRegistry
    usage: dict[str, int] = field(default_factory=dict)
    cleanup: Callable[[], Awaitable[None]] | None = None

    async def close(self) -> None:
        if self.cleanup is not None:
            await self.cleanup()


class BindingFactory(Protocol):
    def __call__(
        self,
        *,
        registry: ToolRegistry,
        allowed_tool_names: frozenset[str],
        system_prompt: str,
        plan: ExecutionPlan,
        permission_check: PermissionCheck | None,
        event_forwarder: EventForwarder,
        run_control: RunControl | None,
    ) -> RunBindings: ...
