import asyncio
from dataclasses import dataclass, field

from google.genai import types


@dataclass
class RunControl:
    """同一次运行及其子任务共用的终止门闩和完整历史检查点。"""

    parent: RunControl | None = None
    stopped: bool = False # 当前运行是否被要求停止
    safety_stopped: bool = False # 是否因安全审核相关原因被停止
    history: list[types.Content] = field(default_factory=list) # 检查点保存的对话历史
    new_contents: list[types.Content] = field(default_factory=list) # 检查点中属于本次运行新增的内容
    turns: int = 0
    usage: dict[str, int] = field(default_factory=dict)

    def ensure_running(self) -> None:
        "“确保仍在运行"
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
        """把当前运行状态复制一份，保存到 RunControl 对象里，作为一个检查点。"""

        self.history = [content.model_copy(deep=True) for content in history]
        self.new_contents = [content.model_copy(deep=True) for content in new_contents]
        self.turns = turns
        self.usage = dict(usage)
