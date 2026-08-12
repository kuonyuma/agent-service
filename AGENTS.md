# AGENTS.md

纯 Python 手写的 Gemini Agent 框架（google-genai SDK），四层结构：`client/`(API 流式调用) → `core/agentic_loop.py`(代理循环) → `tools/`(工具注册与执行) → `ui/app.py`(终端 UI)。入口 `main.py`。

## 环境与命令

- 依赖只存在于 `.venv`（uv 创建，Python 3.14），**没有** requirements.txt / pyproject.toml。新增依赖需 `& .venv\Scripts\python.exe -m pip install <pkg>`。
- 运行应用：`& .venv\Scripts\python.exe main.py`（交互式终端，需要 API key）。
- **测试不是 pytest**（未安装 pytest）。`tests/test_*.py` 各自是独立冒烟脚本，逐个运行：`& .venv\Scripts\python.exe tests\test_xxx.py`，输出 PASS/FAIL。
- `tests/test_client.py`、`tests/stream_test.py` 会真实调用 Gemini API，需要有效 key；其余测试可离线运行。
- `tests/heap.c` 是供读取类工具手动测试的样例文件，不是测试用例。

## API Key 与配置

- key 优先读环境变量 `GEMINI_API_KEY`，回退到 `config/config.yaml` 的 `model.key`；值以 `your` 开头或为空时 `get_client()` 直接 `sys.exit(1)`（见 `client/client.py:19`）。
- 配置与系统提示词（`config/system_prompt.md`）在 `config/settings.py` 导入时一次性加载为模块级单例 `settings`，改配置必须重启进程。

## 易错点

- 所有导入以项目根为基准（如 `from tools.base import Tool`）；测试脚本靠 `sys.path.insert(0, parents[1])` 自助定位项目根，新增测试需保留该写法。
- 新增工具必须两步：继承 `tools/base.py` 的 `Tool`（定义 `name`/`description`/`input_schema`/`read_only`），**并加入 `tools/index.py` 的 `ALL_TOOLS`**，否则 LLM 看不到该工具。
- `read_only = False` 的工具（写文件、执行命令）在 `agentic_loop.py` 中必须经 `permission_check` 回调由用户确认；拒绝时以模拟 tool 错误回复告知 LLM。
- `agentic_loop.query()` 硬性上限 10 轮，超出返回 `max_turns`，不要改成无限循环。
- 模型名 `gemini-3.6-flash` 在 `config/config.yaml` 与个别测试中各自硬编码，改模型名需两处同步。
- 代码注释、用户可见文案、报错信息一律用中文，保持现状。
- git 仓库根在上层 `A:\Root_Code\Github_Workspace`（整个工作区是同一个仓库），提交时注意只暂存本项目文件。
