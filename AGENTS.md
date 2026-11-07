# AGENTS.md

纯 Python 手写的 Gemini CLI Agent。实现以 `docs/framewokr.md` 的五个内核模块与外核划分为依据；
当前实现说明见 `docs/architecture.md`。Web 服务、持久化与部署链路已删除。

## 结构与边界

- `kernel/runtime/`：运行生命周期、事件、停止控制；`loop.query()` 只编排。
- `kernel/context/`：历史、inbox、通知与压缩。
- `kernel/model/`：模型端口与结果；保留 SDK DTO，但不得导入具体客户端。
- `kernel/dispatch/`：工具契约、审批、并发执行。
- `kernel/strategy/`：计划、反思、验证与完成策略。
- `adapters/`：gemini、tools、collaboration、security、cli。
- `bootstrap.py`：唯一生产组装入口，给内核注入模型、工具表、配置与绑定工厂。
- 内核禁止依赖 adapters、config、bootstrap；不得重新引入默认全局工具实例。
- CLI 入口为 `agent_service.main`。不要恢复已删除的 Web、数据库或迁移目录。

## 环境与命令

- Windows + PowerShell，Python 3.14，依赖由 `pyproject.toml` / `uv.lock` 管理。
- 同步：`uv sync --frozen --all-groups`。
- CLI：`& .venv\Scripts\python.exe -m agent_service.main`（交互式终端，需要有效 key）。
- 测试：`& .venv\Scripts\python.exe -m pytest -q`。
- 检查：`& .venv\Scripts\python.exe -m ruff check .` 和 `& .venv\Scripts\python.exe -m pyright`。
- `tests/unit/` 验证单模块；`tests/integration/` 验证内部协作；
  `tests/external/` 需显式 `--run-external`，可能启动 shell、联网或产生模型费用。

## 配置

- key 优先取环境变量 `GEMINI_API_KEY`，回退 `src/agent_service/config/config.yaml` 的 `model.key`。
- 模型名只在 config.yaml 维护；外部测试从配置读取，不重复硬编码。
- 配置、系统提示词和审核提示词在 settings 导入时加载，改动后需重启。
- 不提交真实凭据，不假定存在自动读取 .env 的功能。

## 易错点

- 包位于 `src/agent_service/`；测试通过 pytest 的 pythonpath 引入。
- 工具继承 `kernel/dispatch/base.py:Tool`，并加入
  `adapters/tools/index.py:create_tool_registry()`；不是添加文件就自动开放。
- `read_only=False` 必须逐次审批，默认拒绝；ID、工具名、参数指纹必须绑定。
- 声明表、注册表、允许表、阶段限制必须一致；未声明工具不能执行。
- 单次 query 硬上限 10 轮，不能改为无限循环。
- 计划/反思/协作工具每次运行独立绑定；不得跨运行共享计划或子会话。
- 文件工具使用 WorkspacePathPolicy，只接受工作区相对路径并拒绝敏感路径及链接。
  shell 经审批仍是高权限本地操作，不是沙箱。
- 取消必须关闭模型流、等待工具异步任务、清理子代理；不要吞掉 CancelledError。
- 历史检查点只保存完整调用/响应对；保留 Gemini 原始 Part 与思考签名。
- 注释、用户文案、报错信息使用中文。
- 当前仓库根为本项目目录；只修改本任务文件，不碰无关的 `jobs/`。
