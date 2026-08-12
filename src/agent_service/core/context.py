from google.genai import types
from agent_service.client.stream_message import stream_message, StreamResult


async def compress_context(
    content: list[types.Content],
    max_tokens: int = 4096,
    max_history_len: int = 10,
) -> list[types.Content]:
    if len(content) < max_history_len:
        return content

    first_content = content[0]
    recent_content = content[-2:]
    old_contents = content[1:-2]

    if not old_contents:
        return content

    result: StreamResult | None = None

    summary_prompt = (
        "请将上述对话历史总结为一份极简的【前情提要】，包含以下信息：\n"
        "1. 已尝试或执行过的操作/工具\n"
        "2. 读取或修改过的文件及关键行\n"
        "3. 当前遇到的核心报错或最新状态\n"
        "字数控制在 150 字以内，保持精炼。"
    )

    query = old_contents + [
        types.Content(role="user", parts=[types.Part.from_text(text=summary_prompt)])
    ]

    async for event in stream_message(
        contents=query,
        system_prompt="你是一位上下文压缩助手",
        max_tokens=max_tokens,
    ):
        if event.type == "message_done" and event.result:
            result = event.result

    if result and result.contents:
        summary_text = (
            "[系统上下文注入 - 以下是之前对话的压缩摘要，"
            "请将其作为背景知识参考，不要在回复中复述或提及此摘要]\n\n"
            f"{result.contents}"
        )
        # 将摘要合并到 first_content 中，避免两个连续的 user 角色
        merged_content = types.Content(
            role="user",
            parts=(first_content.parts or [])
            + [types.Part.from_text(text=summary_text)],
        )
        # 紧跟一条 model 回复，维持角色交替
        ack_content = types.Content(
            role="model",
            parts=[types.Part.from_text(text="好的，我已了解之前的对话背景。")],
        )
        return [merged_content, ack_content] + recent_content

    return content
