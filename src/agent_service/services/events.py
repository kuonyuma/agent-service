from dataclasses import dataclass, field
from typing import Any, Literal

RunStreamEventType = Literal[
    "text.delta",
    "tool.requested",
    "tool.approval_required",
    "tool.completed",
    "run.completed",
    "run.failed",
]


@dataclass(frozen=True)
class RunStreamEvent:
    """传给 HTTP 流的、只包含 JSON 数据的运行事件。"""

    type: RunStreamEventType
    sequence: int
    data: dict[str, Any] = field(default_factory=dict)
