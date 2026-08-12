"""WriteFile 工具冒烟测试

测试场景:
  1. 正常写入新文件并验证内容
  2. 空路径或空内容应返回错误
  3. 自动创建不存在的父目录
  4. 覆盖已有文件
  5. 工具元数据验证
"""

from pathlib import Path
import sys
import asyncio
import shutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_service.tools.write_file import WriteFileTool

# 测试用临时目录
TEMP_DIR = Path(__file__).resolve().parent / "_tmp_write_test"


def cleanup():
    """清理临时文件"""
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)


async def test_write_new_file():
    """测试: 写入新文件，应成功且文件内容正确"""
    tool = WriteFileTool()
    target = str(TEMP_DIR / "hello.txt")
    result = await tool.run({"path": target, "content": "hello_write_test"})

    assert not result.is_error, f"写入不应报错, 但得到: {result.content}"
    actual = Path(target).read_text(encoding="utf-8")
    assert actual == "hello_write_test", f"文件内容应为 'hello_write_test', 实际: {actual}"
    print("[PASS] test_write_new_file")


async def test_empty_path():
    """测试: 传入空路径，应返回 is_error=True"""
    tool = WriteFileTool()
    result = await tool.run({"path": "", "content": "some content"})

    assert result.is_error, "空路径应返回 is_error=True"
    print("[PASS] test_empty_path")


async def test_empty_content():
    """测试: 传入空内容，应返回 is_error=True"""
    tool = WriteFileTool()
    result = await tool.run({"path": str(TEMP_DIR / "empty.txt"), "content": ""})

    assert result.is_error, "空内容应返回 is_error=True"
    print("[PASS] test_empty_content")


async def test_nested_dir_creation():
    """测试: 写入到多层不存在的目录，应自动创建父目录"""
    tool = WriteFileTool()
    target = str(TEMP_DIR / "a" / "b" / "c" / "deep.txt")
    result = await tool.run({"path": target, "content": "deep_content"})

    assert not result.is_error, f"嵌套写入不应报错, 但得到: {result.content}"
    assert Path(target).exists(), "文件应被创建"
    actual = Path(target).read_text(encoding="utf-8")
    assert actual == "deep_content", f"文件内容应为 'deep_content', 实际: {actual}"
    print("[PASS] test_nested_dir_creation")


async def test_overwrite_file():
    """测试: 覆盖写入已有文件，内容应更新"""
    tool = WriteFileTool()
    target = str(TEMP_DIR / "overwrite.txt")

    await tool.run({"path": target, "content": "first"})
    await tool.run({"path": target, "content": "second"})

    actual = Path(target).read_text(encoding="utf-8")
    assert actual == "second", f"覆盖后内容应为 'second', 实际: {actual}"
    print("[PASS] test_overwrite_file")


async def test_tool_metadata():
    """测试: 工具元数据应正确配置"""
    tool = WriteFileTool()

    assert tool.name == "write_file", f"name 应为 'write_file', 实际: {tool.name}"
    assert tool.read_only is False, "WriteFile 不应是只读工具"
    assert "path" in tool.input_schema["required"], "path 应为必填参数"
    assert "content" in tool.input_schema["required"], "content 应为必填参数"

    declaration = tool.to_function_declaration()
    assert declaration.name == "write_file"
    print("[PASS] test_tool_metadata")


async def main():
    print("=" * 40)
    print("WriteFile 工具冒烟测试")
    print("=" * 40)

    cleanup()

    tests = [
        test_write_new_file,
        test_empty_path,
        test_empty_content,
        test_nested_dir_creation,
        test_overwrite_file,
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

    cleanup()

    print("=" * 40)
    print(f"结果: {passed} 通过, {failed} 失败, 共 {len(tests)} 项")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
