"""EditFile 工具冒烟测试

测试场景:
  1. 正常替换唯一匹配的字符串
  2. 不存在的文件应返回错误
  3. 未找到匹配字符串应返回错误
  4. 多次匹配时应返回错误 (拒绝歧义修改)
  5. 工具元数据验证
"""

from pathlib import Path
import sys
import asyncio
import shutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_service.tools.edit_file import EditFileTool

TEMP_DIR = Path(__file__).resolve().parent / "_tmp_edit_test"


def cleanup():
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)


def create_test_file(name: str, content: str) -> str:
    """创建临时测试文件并返回路径"""
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    path = TEMP_DIR / name
    path.write_text(content, encoding="utf-8")
    return str(path)


async def test_normal_replace():
    """测试: 正常替换唯一字符串"""
    tool = EditFileTool()
    path = create_test_file("normal.txt", "hello world")
    result = await tool.run({
        "path": path,
        "old_string": "hello",
        "new_string": "goodbye",
    })

    assert not result.is_error, f"正常替换不应报错, 但得到: {result.content}"
    actual = Path(path).read_text(encoding="utf-8")
    assert actual == "goodbye world", f"内容应为 'goodbye world', 实际: {actual}"
    print("[PASS] test_normal_replace")


async def test_nonexistent_file():
    """测试: 编辑不存在的文件，应返回 is_error=True"""
    tool = EditFileTool()
    result = await tool.run({
        "path": "/no/such/file.txt",
        "old_string": "a",
        "new_string": "b",
    })

    assert result.is_error, "不存在的文件应返回 is_error=True"
    print("[PASS] test_nonexistent_file")


async def test_no_match():
    """测试: 未找到匹配字符串，应返回 is_error=True"""
    tool = EditFileTool()
    path = create_test_file("nomatch.txt", "hello world")
    result = await tool.run({
        "path": path,
        "old_string": "xyz_not_exist",
        "new_string": "replaced",
    })

    assert result.is_error, "未找到匹配应返回 is_error=True"
    print("[PASS] test_no_match")


async def test_multiple_matches():
    """测试: 多次匹配时应拒绝修改"""
    tool = EditFileTool()
    path = create_test_file("multi.txt", "aaa bbb aaa")
    result = await tool.run({
        "path": path,
        "old_string": "aaa",
        "new_string": "ccc",
    })

    assert result.is_error, "多次匹配应返回 is_error=True"
    # 原文件不应被修改
    actual = Path(path).read_text(encoding="utf-8")
    assert actual == "aaa bbb aaa", "多次匹配时原文件不应被修改"
    print("[PASS] test_multiple_matches")


async def test_tool_metadata():
    """测试: 工具元数据应正确配置"""
    tool = EditFileTool()

    assert tool.name == "edit_file", f"name 应为 'edit_file', 实际: {tool.name}"
    assert tool.read_only is False, "EditFile 不应是只读工具"
    assert "path" in tool.input_schema["required"], "path 应为必填参数"
    assert "old_string" in tool.input_schema["required"], "old_string 应为必填参数"
    assert "new_string" in tool.input_schema["required"], "new_string 应为必填参数"

    declaration = tool.to_function_declaration()
    assert declaration.name == "edit_file"
    print("[PASS] test_tool_metadata")


async def main():
    print("=" * 40)
    print("EditFile 工具冒烟测试")
    print("=" * 40)

    cleanup()

    tests = [
        test_normal_replace,
        test_nonexistent_file,
        test_no_match,
        test_multiple_matches,
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
