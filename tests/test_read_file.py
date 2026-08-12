"""ReadFile 工具冒烟测试

测试场景:
  1. 正常读取已知文件
  2. 不存在的路径应返回错误
  3. 不传 path 参数的边界情况
  4. 工具元数据验证
"""

from pathlib import Path
import sys
import asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_service.tools.read_file import ReadFileTool


async def test_read_existing_file():
    """测试: 读取 main.py，应返回非空内容且不报错"""
    tool = ReadFileTool()
    main_path = str(
        Path(__file__).resolve().parents[1] / "src" / "agent_service" / "main.py"
    )
    result = await tool.run({"path": main_path})

    assert not result.is_error, f"读取已有文件不应报错, 但得到: {result.content}"
    assert len(result.content) > 0, "文件内容不应为空"
    print("[PASS] test_read_existing_file")


async def test_read_nonexistent_file():
    """测试: 读取不存在的文件，应返回 is_error=True"""
    tool = ReadFileTool()
    result = await tool.run({"path": "/no/such/file_abc123.txt"})

    assert result.is_error, "不存在的文件应返回 is_error=True"
    print("[PASS] test_read_nonexistent_file")


async def test_read_self():
    """测试: 读取本测试文件自身，内容应包含此函数名"""
    tool = ReadFileTool()
    result = await tool.run({"path": str(Path(__file__).resolve())})

    assert not result.is_error, f"读取自身不应报错, 但得到: {result.content}"
    assert "test_read_self" in result.content, "内容应包含本函数名"
    print("[PASS] test_read_self")


async def test_tool_metadata():
    """测试: 工具元数据应正确配置"""
    tool = ReadFileTool()

    assert tool.name == "read_file", f"name 应为 'read_file', 实际: {tool.name}"
    assert tool.read_only is True, "ReadFile 应是只读工具"
    assert "path" in tool.input_schema["required"], "path 应为必填参数"

    declaration = tool.to_function_declaration()
    assert declaration.name == "read_file"
    print("[PASS] test_tool_metadata")


async def main():
    print("=" * 40)
    print("ReadFile 工具冒烟测试")
    print("=" * 40)

    tests = [
        test_read_existing_file,
        test_read_nonexistent_file,
        test_read_self,
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
