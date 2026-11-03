import asyncio
from dataclasses import dataclass, field

from google.genai import types


@dataclass
class RunControl:
    """同一次运行及其子任务共用的终止门闩和完整历史检查点。"""

    parent: RunControl | None = None
    stopped: bool = False
    safety_stopped: bool = False
    history: list[types.Content] = field(default_factory=list)
    new_contents: list[types.Content] = field(default_factory=list)
    turns: int = 0
    usage: dict[str, int] = field(default_factory=dict)

    def ensure_running(self) -> None:
        if self.parent is not None:
            self.parent.ensure_running()
        if self.stopped:
            raise asyncio.CancelledError

    def capture(
        self,
        history: list[types.Content],
        new_contents: list[types.Content],
        turns: int,
        usage: dict[str, int],
    ) -> None:
        """只在消息及工具响应结构完整时保存，避免后续修改污染检查点。"""

        self.history = [content.model_copy(deep=True) for content in history]
        self.new_contents = [content.model_copy(deep=True) for content in new_contents]
        self.turns = turns
        self.usage = dict(usage)
