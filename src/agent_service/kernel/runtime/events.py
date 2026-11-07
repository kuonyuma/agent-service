from dataclasses import dataclass, field
from typing import Any, Literal

from google.genai import types

from agent_service.errors import AgentServiceError
from agent_service.kernel.dispatch.base import ToolResult
from agent_service.kernel.strategy.plan import AgentPhase, ExecutionPlan

LoopEventType = Literal[
    "safety.blocked",
    "text.delta",
    "plan.updated",
    "reflection.updated",
    "tool.requested",
    "tool.approval_required",
    "tool.completed",
    "run.completed",
    "run.failed",
]


@dataclass(frozen=True)
class LoopResult:
    """一次 Agent 运行的最终结果和新历史。"""

    reason: Literal["completed", "max_turns", "error"]
    history: list[types.Content]
    new_contents: list[types.Content]
    turns: int
    usage: dict[str, int] = field(default_factory=dict)
    error: AgentServiceError | None = None
    plan: ExecutionPlan | None = None
    safety_usage: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class LoopEvent:
    """与具体交互入口无关的运行事件。"""

    type: LoopEventType
    text: str = ""
    turn: int = 0
    phase: AgentPhase = "planning"
    plan: ExecutionPlan | None = None
    tool_call_id: str = ""
    tool_name: str = ""
    tool_arguments: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False
    approval_request_hash: str = ""
    tool_result: ToolResult | None = None
    result: LoopResult | None = None
    safety_code: str = ""
    safety_category: str = ""
