"""上下文压缩单元测试

使用 mock 的 stream_message 替换真实 LLM 调用，保证离线可运行。
"""

from pathlib import Path
import sys
import asyncio
from google.genai import types

# 确保能正确导入项目目录中的模块
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_service.client.stream_message import StreamEvent, StreamResult
import agent_service.core.context
from agent_service.core.context import compress_context


async def _fake_stream_message(contents, system_prompt, max_tokens):
    """模拟 LLM 流式返回压缩摘要。"""
    yield StreamEvent(
        type="message_done",
        result=StreamResult(
            contents="用户要求编写一个 Web 服务器，已讨论框架选择，最新问题是 HTTPS 支持。",
            stop_reason="STOP",
            usage={},
        ),
    )


async def test_compress_below_threshold():
    """测试1：消息数量低于阈值时，不触发压缩，直接返回原列表"""
    print("\n--- 测试 1: 未达到阈值 (max_history_len=10, 消息数=5) ---")
    mock_contents = [
        types.Content(role="user", parts=[types.Part.from_text(text=f"消息 {i}")])
        for i in range(5)
    ]
    compressed = await compress_context(mock_contents, max_history_len=10)
    assert len(compressed) == 5, f"预期长度为 5，实际长度为 {len(compressed)}"
    print("[OK] 验证通过：低于阈值未触发压缩，保留原始 5 条消息。")


async def test_compress_exceed_threshold():
    """测试 2：消息数量达到/超过阈值时，触发压缩（mock LLM）"""
    print("\n--- 测试 2: 超过阈值 (max_history_len=10, 消息数=12) ---")

    # 构造 12 条对话历史：首条 + 9 条中间对话 + 最近 2 条对话
    first_msg = types.Content(
        role="user", parts=[types.Part.from_text(text="初始任务：编写一个 Web 服务器")]
    )
    middle_msgs = [
        types.Content(
            role="model" if i % 2 == 1 else "user",
            parts=[types.Part.from_text(text=f"中间步骤 {i}: 讨论框架与架构")],
        )
        for i in range(1, 10)
    ]
    recent_msgs = [
        types.Content(role="user", parts=[types.Part.from_text(text="最新问题：支持 HTTPS 吗？")]),
        types.Content(role="model", parts=[types.Part.from_text(text="支持，可以通过 ssl_context 配置。")]),
    ]

    mock_contents = [first_msg] + middle_msgs + recent_msgs
    print(f"压缩前消息总条数: {len(mock_contents)}")

    # 用假 stream_message 替换真实的 LLM 调用
    original_stream_message = agent_service.core.context.stream_message
    agent_service.core.context.stream_message = _fake_stream_message
    try:
        compressed = await compress_context(mock_contents, max_history_len=10)
    finally:
        agent_service.core.context.stream_message = original_stream_message

    print(f"压缩后消息总条数: {len(compressed)}")

    # 验证结构
    # 期待结果: [merged(user), ack(model), recent_msg_1, recent_msg_2] -> 共 4 条
    assert len(compressed) == 4, f"预期压缩后为 4 条消息，实际为 {len(compressed)}"

    # 验证第一条为合并后的 user 消息（初始任务 + 摘要，共 2 个 part）
    assert compressed[0].role == "user"
    assert compressed[0].parts[0].text == "初始任务：编写一个 Web 服务器"
    assert len(compressed[0].parts) == 2, "合并消息应包含 2 个 part（原始任务 + 摘要）"

    # 验证 ack 确认为 role="model"，维持角色交替
    assert compressed[1].role == "model"
    assert compressed[1].parts[0].text == "好的，我已了解之前的对话背景。"

    # 验证最后两条保留了最近对话
    assert compressed[-2].parts[0].text == "最新问题：支持 HTTPS 吗？"
    assert compressed[-1].parts[0].text == "支持，可以通过 ssl_context 配置。"

    # 打印总结内容（摘要在第一条消息的第 2 个 part 中）
    summary_text = compressed[0].parts[1].text
    assert "HTTPS" in summary_text, "摘要应包含来自 mock 的摘要文本"
    print(f"\n[生成的摘要注入内容]:\n{summary_text}\n")
    print("[OK] 验证通过：摘要合并到首条 user 消息，角色交替正确，不会被模型回吐。")


async def main():
    await test_compress_below_threshold()
    await test_compress_exceed_threshold()


if __name__ == "__main__":
    asyncio.run(main())
    print("\n所有测试完成！")
