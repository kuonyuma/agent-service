"""pytest 全局配置。

测试目录按 unit、integration、external 三层划分。
external 测试默认只收集不执行，避免普通测试误触发 shell、网络请求或 API 费用。
需要显式执行时使用：pytest --run-external tests/external -q
"""

from pathlib import Path

import pytest

_CATEGORIES = {
    "unit": pytest.mark.unit,
    "integration": pytest.mark.integration,
    "external": pytest.mark.external,
}


def pytest_addoption(parser):
    parser.addoption(
        "--run-external",
        action="store_true",
        default=False,
        help="执行 tests/external 中需要真实外部环境的测试",
    )


def pytest_collection_modifyitems(config, items):
    """根据目录自动添加层级标记，并默认跳过 external 测试。"""

    run_external = config.getoption("--run-external")
    for item in items:
        path_parts = set(Path(str(item.fspath)).parts)
        category = next(
            (name for name in _CATEGORIES if name in path_parts),
            None,
        )
        if category is None:
            continue

        item.add_marker(_CATEGORIES[category])
        if category == "external" and not run_external:
            item.add_marker(
                pytest.mark.skip(
                    reason="external 测试默认关闭，请使用 --run-external 显式执行"
                )
            )
