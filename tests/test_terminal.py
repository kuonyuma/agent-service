"""Terminal 工具冒烟测试

测试场景:
  1. 正常命令执行 (echo)
  2. 空命令错误处理
  3. 命令失败时 stderr 捕获
"""

from pathlib import Path
import sys
import asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_service.tools.run_command import RunCommandTool


async def test_echo():
    """测试: 正常执行 echo 命令，输出应包含预期文本"""
    terminal = RunCommandTool()
    result = await terminal.run({"command": "echo hello_terminal"})

    assert not result.is_error, f"echo 不应报错, 但得到: {result.content}"
    assert "hello_terminal" in result.content, (
        f"输出中应包含 'hello_terminal', 实际: {result.content}"
    )
    assert "returncode0" in result.content, (
        f"返回码应为 0, 实际: {result.content}"
    )
    print("[PASS] test_echo")


async def test_empty_command():
    """测试: 传入空命令，应返回 is_error=True"""
    terminal = RunCommandTool()
    result = await terminal.run({"command": ""})

    assert result.is_error, "空命令应返回 is_error=True"
    print("[PASS] test_empty_command")


async def test_missing_command_key():
    """测试: 不传 command 键，应返回 is_error=True"""
    terminal = RunCommandTool()
    result = await terminal.run({})

    assert result.is_error, "缺少 command 键应返回 is_error=True"
    print("[PASS] test_missing_command_key")


async def test_failing_command():
    """测试: 执行一个必然失败的命令，returncode 应非零"""
    terminal = RunCommandTool()
    # 在 Windows 和 Unix 上都会失败的命令
    result = await terminal.run({"command": "exit 1"})

    assert not result.is_error, "工具本身不应报错，错误体现在 returncode"
    assert "returncode1" in result.content, (
        f"返回码应为 1, 实际: {result.content}"
    )
    print("[PASS] test_failing_command")


async def test_tool_metadata():
    """测试: 工具元数据应正确配置"""
    terminal = RunCommandTool()

    assert terminal.name == "run_command", f"name 应为 'run_command', 实际: {terminal.name}"
    assert terminal.read_only is False, "Terminal 不应是只读工具"
    assert "command" in terminal.input_schema["required"], "command 应为必填参数"

    declaration = terminal.to_function_declaration()
    assert declaration.name == "run_command"
    print("[PASS] test_tool_metadata")


async def main():
    print("=" * 40)
    print("Terminal 工具冒烟测试")
    print("=" * 40)

    tests = [
        test_echo,
        test_empty_command,
        test_missing_command_key,
        test_failing_command,
        test_tool_metadata,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            await test()
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"[ERROR] {test.__name__}: {type(e).__name__}: {e}")
            failed += 1

    print("=" * 40)
    print(f"结果: {passed} 通过, {failed} 失败, 共 {len(tests)} 项")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
