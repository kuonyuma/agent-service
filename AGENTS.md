# AGENTS.md

纯 Python 手写的 Gemini Agent 框架（google-genai SDK），四层结构：`client/`(API 流式调用) → `core/agentic_loop.py`(代理循环) → `tools/`(工具注册与执行) → `ui/app.py`(终端 UI)。入口 `main.py`。

## 环境与命令

- 依赖由 `pyproject.toml` 和 `uv.lock` 管理，开发依赖包含 `pytest` 与 `pytest-asyncio`；运行环境使用 `.venv`（Python 3.14）。新增依赖优先更新项目配置并重新同步环境。
- 运行应用：`& .venv\Scripts\python.exe main.py`（交互式终端，需要 API key）。
- 测试使用 pytest：默认运行 `& .venv\Scripts\python.exe -m pytest -q`，执行 unit 与 integration；需要真实外部环境时显式运行 `& .venv\Scripts\python.exe -m pytest --run-external tests\external -q`。
- `tests/unit/` 验证单个模块，`tests/integration/` 验证项目内部协作，`tests/external/` 验证真实 shell、网络和 Gemini API；external 测试默认跳过，Gemini 测试需要有效 key。

## API Key 与配置

- key 优先读环境变量 `GEMINI_API_KEY`，回退到 `src/agent_service/config/config.yaml` 的 `model.key`；值以 `your` 开头或为空时 `get_client()` 直接 `sys.exit(1)`（见 `src/agent_service/client/client.py`）。
- 配置与系统提示词（`src/agent_service/config/system_prompt.md`）在 `src/agent_service/config/settings.py` 导入时一次性加载为模块级单例 `settings`，改配置必须重启进程。

## 易错点

- 项目包位于 `src/agent_service/`，代码和测试使用 `from agent_service...` 导入；pytest 通过 `pyproject.toml` 中的 `pythonpath = ["src"]` 定位源码。
- 新增工具必须两步：继承 `src/agent_service/tools/base.py` 的 `Tool`（定义 `name`/`description`/`input_schema`/`read_only`），**并加入 `src/agent_service/tools/index.py` 的 `ALL_TOOLS`**，否则 LLM 看不到该工具。
- `read_only = False` 的工具（写文件、执行命令）在 `agentic_loop.py` 中必须经 `permission_check` 回调由用户确认；拒绝时以模拟 tool 错误回复告知 LLM。
- `agentic_loop.query()` 硬性上限 10 轮，超出返回 `max_turns`，不要改成无限循环。
- 模型名以 `src/agent_service/config/config.yaml` 为准，外部测试从配置读取模型名，修改模型时不应在测试中重复硬编码。
- 代码注释、用户可见文案、报错信息一律用中文，保持现状。
- git 仓库根在上层 `A:\Root_Code\Github_Workspace`（整个工作区是同一个仓库），提交时注意只暂存本项目文件。
