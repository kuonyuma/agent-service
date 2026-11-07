import asyncio

from google.genai import types


def runtime_notice(body: str) -> str:
    """给系统提示语套上 `<runtime-notice>` 标签。

    套上这个标签是为了告诉大模型：“这段话是程序自动发给你的状态提醒，不是用户说的话。”

    参数:
        body: 要提醒大模型的文字内容。

    返回:
        去掉首尾多余空格、并包好 `<runtime-notice>...</runtime-notice>` 标签的字符串。
    """

    return f"<runtime-notice>\n{body.strip()}\n</runtime-notice>"


def append_notice(
    history: list[types.Content],
    notice_text: str,
) -> None:
    """把一条系统状态提醒塞到聊天记录的最后面。

    大模型的聊天记录要求“用户一句、模型一句”交替出现。如果最后一条本来就是用户消息，
    就直接把提醒拼进这条消息里；如果不是，就在末尾新建一条用户消息放进去。
    注意：这个函数会直接修改传入的 history 列表。

    参数:
        history: 当前的聊天记录列表。
        notice_text: 已经包好标签的提醒文字。

    返回:
        None（直接改在 history 里，不额外返回值）。
    """

    notice_part = types.Part.from_text(text=notice_text)
    if history and history[-1].role == "user":
        existing_parts = list(history[-1].parts or [])
        history[-1] = types.Content(
            role="user",
            parts=[*existing_parts, notice_part],
        )
    else:
        history.append(types.Content(role="user", parts=[notice_part]))


def has_prior_plan_state(
    prior_contents: list[types.Content],
) -> bool:
    """看一眼之前的聊天记录里，有没有留下“上一个任务做过计划或反思”的痕迹。

    比如用户聊了第二个新问题，但历史记录里还留着第一个问题的计划。
    这个函数只负责检查历史、不会改动任何内容。如果查出来有旧痕迹，
    外面的代码就会提醒大模型：“旧计划已经翻篇了，新任务请重新开始。”

    参数:
        prior_contents: 新问题之前的旧聊天记录列表。

    返回:
        True 表示找到了旧计划、旧反思、系统提醒或历史压缩摘要的痕迹；
        False 表示干干净净，没有这些旧痕迹。
    """

    for item in prior_contents:
        for part in item.parts or []:
            if part.function_call is not None and part.function_call.name in {
                "update_plan",
                "reflect",
            }:
                return True
            if part.function_response is not None and part.function_response.name in {
                "update_plan",
                "reflect",
            }:
                return True
            if part.text and (
                "<runtime-notice>" in part.text
                or "[系统上下文注入 - 以下是之前对话的压缩摘要" in part.text
            ):
                return True
    return False


class RunContext:
    """持有本次运行的独立历史；仅在完整工具往返之后建立安全检查点。"""

    def __init__(self, contents: list[types.Content]) -> None:
        self.history = [item.model_copy(deep=True) for item in contents]
        self.initial_length = len(self.history)

    @property
    def new_contents(self) -> list[types.Content]:
        return self.history[self.initial_length :]

    def append_model(self, parts: list[types.Part]) -> None:
        self.history.append(types.Content(role="model", parts=parts))

    def append_tools(self, parts: list[types.Part], notice: str = "") -> None:
        if notice:
            parts = [*parts, types.Part.from_text(text=notice)]
        self.history.append(types.Content(role="user", parts=parts))

    def notice(self, text: str) -> None:
        if text:
            append_notice(self.history, text)

    def receive(self, inbox: asyncio.Queue[str] | None) -> None:
        if inbox is None:
            return
        messages: list[str] = []
        while True:
            try:
                message = inbox.get_nowait()
            except asyncio.QueueEmpty:
                break
            if isinstance(message, str) and message.strip():
                messages.append(f"[实时通信指令] {message.strip()}")
        if messages:
            self.notice("\n\n".join(messages))
