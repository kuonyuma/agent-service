# AGENTS.md

纯 Python 手写的 Gemini Agent 服务（google-genai SDK）。Runtime 主链为
`client/` → `core/agentic_loop.py` → `core/runner.py` → `tools/`；CLI 位于
`ui/`。HTTP 链路为 `api/` → `services/` → Runtime，并通过 `db/` 的
SQLAlchemy 2.0 异步 Session Factory 访问 MySQL。CLI 入口为 `main.py`，API 工厂为
`agent_service.api.app:create_app`。

## 环境与命令

- 依赖由 `pyproject.toml` 和 `uv.lock` 管理，开发依赖包含 `pytest` 与 `pytest-asyncio`；运行环境使用 `.venv`（Python 3.14）。新增依赖优先更新项目配置并重新同步环境。
- 运行 CLI：`& .venv\Scripts\python.exe -m agent_service.main`（交互式终端，需要 API key）。
- 完整本地服务：`docker compose up -d --build`；API/MySQL 宿主端口只绑定
  `127.0.0.1:8000` 与 `127.0.0.1:3307`。
- 主机运行 API：先设置 `AGENT_SERVICE_DATABASE_URL` 并执行迁移，再运行
  `& .venv\Scripts\python.exe -m uvicorn agent_service.api.app:create_app --factory --host 127.0.0.1 --port 8000`。
- 测试使用 pytest：默认运行 `& .venv\Scripts\python.exe -m pytest -q`，执行 unit 与 integration；需要真实外部环境时显式运行 `& .venv\Scripts\python.exe -m pytest --run-external tests\external -q`。
- `tests/unit/` 验证单个模块，`tests/integration/` 验证项目内部协作，`tests/external/` 验证真实 shell、网络和 Gemini API；external 测试默认跳过，Gemini 测试需要有效 key。

## API Key 与配置

- key 优先读环境变量 `GEMINI_API_KEY`，回退到 `src/agent_service/config/config.yaml` 的 `model.key`；值以 `your` 开头或为空时 `get_client()` 抛出 `ConfigurationError`。
- 配置与系统提示词（`src/agent_service/config/system_prompt.md`）在 `src/agent_service/config/settings.py` 导入时一次性加载为模块级单例 `settings`，改配置必须重启进程。
- HTTP/数据库配置使用 `APISettings`，环境变量前缀为 `AGENT_SERVICE_`；迁移只从进程环境和该配置读取 URL，不在源码中写真实凭据。

## 易错点

- 项目包位于 `src/agent_service/`，代码和测试使用 `from agent_service...` 导入；pytest 通过 `pyproject.toml` 中的 `pythonpath = ["src"]` 定位源码。
- 新增工具必须两步：继承 `src/agent_service/tools/base.py` 的 `Tool`（定义 `name`/`description`/`input_schema`/`read_only`），**并加入 `src/agent_service/tools/index.py` 的 `ALL_TOOLS`**，否则 LLM 看不到该工具。
- `read_only = False` 的工具（写文件、执行命令）在 `agentic_loop.py` 中必须经 `permission_check` 回调由用户确认；拒绝时以模拟 tool 错误回复告知 LLM。
- `agentic_loop.query()` 硬性上限 10 轮，超出返回 `max_turns`，不要改成无限循环。
- `POST /runs` 必须在返回 `StreamingResponse` 前提交用户消息和 run；Gemini/工具期间不得持有数据库 Session 或事务。工具事件与终态各自创建短 Session。
- 同一会话使用 `active_run_id IS NULL` 的原子条件更新竞争；释放必须同时匹配当前 run ID。冲突映射为 409。
- Web Runner 默认只允许显式表中的 `list_files`、`read_file`；新增
  `read_only=True` 工具不会自动开放。设置
  `AGENT_SERVICE_WEB_MUTATING_TOOLS_ENABLED=true` 后才声明 `write_file`、
  `edit_file`，并要求绑定 call ID 与参数指纹的一次性审批。声明层和执行层必须使用同一 allow-list。
- Web 文件工具都使用 `WorkspacePathPolicy`，只接受配置工作区内的相对路径，
  并拒绝敏感路径、符号链接和 Windows reparse point。任意字符串
  `run_command` 永不向 Web 暴露；可选质量检查只接受固定 `ruff`/`pyright`
  动作。即便如此，在认证与独立工具沙箱完成前，API 仍只能监听本机地址。
- 审批等待必须先提交 `ToolCall` 与 `AgentRun.waiting_approval`，再在事务外
  等待；批准、拒绝、超时使用 `approval_status='pending'` 条件更新竞争，
  审批接口不得直接执行工具。
- 真实 MySQL 测试只接受名称以 `_test` 结尾的 `AGENT_SERVICE_TEST_DATABASE_URL`；不得使用 SQLite 声称验证 MySQL 行为。
- 模型名以 `src/agent_service/config/config.yaml` 为准，外部测试从配置读取模型名，修改模型时不应在测试中重复硬编码。
- 代码注释、用户可见文案、报错信息一律用中文，保持现状。
- git 仓库根在上层 `A:\Root_Code\Github_Workspace`（整个工作区是同一个仓库），提交时注意只暂存本项目文件。
