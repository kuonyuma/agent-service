from pathlib import Path
import sys
import asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_service.client.stream_message import stream_message


async def main():

    async for event in stream_message(contents="你好，请做一个简单的自我介绍"):
        if event.type == "text":
            sys.stdout.write(event.text)
        if event.type == "tool_use_start":
            sys.stdout.write("模型将准备使用工具")
    print()


if __name__ == "__main__":
    asyncio.run(main())
    print("测试结束")
