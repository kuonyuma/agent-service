"""LoadYaml 工具冒烟测试

测试场景:
  1. 正常加载项目中的 config.yaml
  2. 不存在的路径应返回错误
  3. 空路径应返回错误
  4. 无效 YAML 内容应返回解析错误
  5. 空文件应返回错误
  6. 工具元数据验证
"""

from pathlib import Path
import sys
import asyncio
import shutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_service.tools.yaml_loader import LoadYamlTool

TEMP_DIR = Path(__file__).resolve().parent / "_tmp_yaml_test"


def cleanup():
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)


def create_test_file(name: str, content: str) -> str:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    path = TEMP_DIR / name
    path.write_text(content, encoding="utf-8")
    return str(path)


async def test_load_project_config():
    """测试: 加载项目 config/config.yaml，应成功"""
    tool = LoadYamlTool()
    config_path = str(
        Path(__file__).resolve().parents[1]
        / "src"
        / "agent_service"
        / "config"
        / "config.yaml"
    )
    result = await tool.run({"path": config_path})

    assert not result.is_error, f"加载配置不应报错, 但得到: {result.content}"
    assert len(result.content) > 0, "配置内容不应为空"
    print("[PASS] test_load_project_config")


async def test_load_custom_yaml():
    """测试: 加载自定义 YAML 文件，内容应被正确解析"""
    tool = LoadYamlTool()
    path = create_test_file("custom.yaml", "name: test\nversion: 1\n")
    result = await tool.run({"path": path})

    assert not result.is_error, f"加载自定义 YAML 不应报错, 但得到: {result.content}"
    assert "name" in result.content, "输出应包含 'name' 键"
    assert "test" in result.content, "输出应包含 'test' 值"
    print("[PASS] test_load_custom_yaml")


async def test_nonexistent_path():
    """测试: 不存在的路径，应返回 is_error=True"""
    tool = LoadYamlTool()
    result = await tool.run({"path": "/no/such/config.yaml"})

    assert result.is_error, "不存在的路径应返回 is_error=True"
    print("[PASS] test_nonexistent_path")


async def test_empty_path():
    """测试: 空路径，应返回 is_error=True"""
    tool = LoadYamlTool()
    result = await tool.run({"path": ""})

    assert result.is_error, "空路径应返回 is_error=True"
    print("[PASS] test_empty_path")


async def test_invalid_yaml():
    """测试: 无效 YAML 内容，应返回解析错误"""
    tool = LoadYamlTool()
    path = create_test_file("bad.yaml", ":\n  :\n    - ][invalid")
    result = await tool.run({"path": path})

    assert result.is_error, "无效 YAML 应返回 is_error=True"
    print("[PASS] test_invalid_yaml")


async def test_empty_yaml_file():
    """测试: 空 YAML 文件，应返回错误"""
    tool = LoadYamlTool()
    path = create_test_file("empty.yaml", "")
    result = await tool.run({"path": path})

    assert result.is_error, "空 YAML 文件应返回 is_error=True"
    print("[PASS] test_empty_yaml_file")


async def test_missing_path_key():
    """测试: 不传 path 参数，应返回 is_error=True"""
    tool = LoadYamlTool()
    result = await tool.run({})

    assert result.is_error, "缺少 path 参数应返回 is_error=True"
    print("[PASS] test_missing_path_key")


async def test_tool_metadata():
    """测试: 工具元数据应正确配置"""
    tool = LoadYamlTool()

    assert tool.name == "load_yaml", f"name 应为 'load_yaml', 实际: {tool.name}"
    assert tool.read_only is True, "LoadYaml 应是只读工具"

    declaration = tool.to_function_declaration()
    assert declaration.name == "load_yaml"
    print("[PASS] test_tool_metadata")


async def main():
    print("=" * 40)
    print("LoadYaml 工具冒烟测试")
    print("=" * 40)

    cleanup()

    tests = [
        test_load_project_config,
        test_load_custom_yaml,
        test_nonexistent_path,
        test_empty_path,
        test_invalid_yaml,
        test_empty_yaml_file,
        test_missing_path_key,
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
