from google.genai import types

from agent_service.client.stream_message import StreamResult, stream_message


def _has_tool_parts(item: types.Content) -> bool:
    for part in item.parts or []:
        if part.function_call is not None or part.function_response is not None:
            return True
    return False


def _format_contents_for_summary(contents: list[types.Content]) -> str:
    """将包含工具调用与响应的历史转换为纯文本记录，避免压缩请求违反函数调用配对约束。"""

    lines: list[str] = []
    for item in contents:
        role_label = "用户" if item.role == "user" else "助手"
        for part in item.parts or []:
            if part.text:
                lines.append(f"[{role_label}]: {part.text}")
            if part.function_call is not None:
                fc = part.function_call
                lines.append(f"[助手调用工具]: {fc.name}({fc.args or {}})")
            if part.function_response is not None:
                fr = part.function_response
                response_preview = str(fr.response or {})
                if len(response_preview) > 500:
                    response_preview = response_preview[:500] + "..."
                lines.append(f"[工具返回结果 {fr.name}]: {response_preview}")
    return "\n".join(lines)


async def compress_context(
    content: list[types.Content],
    max_tokens: int = 4096,
    max_history_len: int = 10,
) -> list[types.Content]:
    if len(content) < max_history_len:
        return content

    first_content = content[0]
    tail_two = content[-2:]
    can_keep_two = (
        len(tail_two) == 2
        and not _has_tool_parts(tail_two[0])
        and not _has_tool_parts(tail_two[1])
        and tail_two[0].role != tail_two[1].role
    )

    if can_keep_two:
        recent_content = tail_two
        old_contents = content[1:-2]
    else:
        recent_content = content[-1:]
        old_contents = content[1:-1]

    if not old_contents:
        return content

    result: StreamResult | None = None

    summary_prompt = (
        "请将上述对话历史总结为一份极简的【前情提要】，包含以下信息：\n"
        "1. 已尝试或执行过的操作/工具\n"
        "2. 读取或修改过的文件及关键行\n"
        "3. 当前遇到的核心报错或最新状态\n"
        "4. 当前执行计划（Plan）的目标及各步骤完成进度（如有）\n"
        "字数控制在 200 字以内，保持精炼。"
    )

    transcript = _format_contents_for_summary(old_contents)
    query = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text=(
                        "以下是之前对话与工具执行的完整记录：\n\n"
                        f"{transcript}\n\n"
                        f"{summary_prompt}"
                    )
                )
            ],
        )
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
        first_text_parts = [
            part
            for part in (first_content.parts or [])
            if part.function_call is None and part.function_response is None
        ]
        merged_content = types.Content(
            role="user",
            parts=first_text_parts + [types.Part.from_text(text=summary_text)],
        )
        # 若 recent_content 首条已是 model 角色（如上一轮完整回复 + 当前新用户提问），
        # 直接衔接即可维持 user -> model -> user 严格交替；否则插入一条 model 确认消息。
        if recent_content and recent_content[0].role == "model":
            return [merged_content] + recent_content

        ack_content = types.Content(
            role="model",
            parts=[types.Part.from_text(text="好的，我已了解之前的对话背景。")],
        )
        return [merged_content, ack_content] + recent_content

    return content
