# Agent Service

纯 Python 手写的 Gemini CLI 编程助手，使用 `google-genai`，不依赖 Agent 框架。
按 [架构划分](docs/framewokr.md) 拆成五个内核模块与外部适配器，当前只保留本地 CLI。

**原 Web 服务链路已删除**：不再提供 HTTP 接口、服务端审批、数据库持久化、迁移和容器部署。
`web_search` 与 `fetch_web_page` 是 Agent 的联网工具，不属于被删除的服务端链路，仍保留。

## 启动

需要 Python 3.14、uv 和交互式终端。以下命令使用 PowerShell：

```powershell
uv sync --frozen --all-groups
$env:GEMINI_API_KEY = "替换为有效密钥"
& .venv\Scripts\python.exe -m agent_service.main
```

Key 优先来自进程环境变量 `GEMINI_API_KEY`，回退到
`src/agent_service/config/config.yaml` 中的 `model.key`。
不自动读取 `.env`；不要把真实密钥提交到仓库。

模型名称、输出 token 上限、审核参数来自 `config.yaml`；
系统提示词与审核提示词也在同一配置目录。配置导入时加载，修改后需重启进程。

CLI 以启动时的当前目录作为文件工具工作区。文件参数使用相对路径，例如
`src/agent_service/main.py`，不是绝对路径。需要改用其他工作区时，可通过
Python 组装接口 `create_runner(workspace=Path(...))` 明确指定。

## 代码结构

```text
src/agent_service/
├── kernel/
│   ├── runtime/        # 单次运行生命周期、循环、事件、停止检查点
│   ├── context/        # 历史、实时消息、运行通知、压缩
│   ├── model/          # 模型端口、流式结果、主模型用量
│   ├── dispatch/       # 工具协议、声明过滤、审批、并发执行与结果
│   └── strategy/       # 规划/执行/反思、验证与完成提醒
├── adapters/
│   ├── gemini/         # SDK 客户端与流转换
│   ├── tools/          # 文件、命令、联网、计划与协作工具实现
│   ├── collaboration/ # 子代理角色、消息、会话、运行期工具绑定
│   ├── security/      # 工作区策略、输出审核与停止协调
│   └── cli/           # PromptSession / Rich 终端交互
├── config/            # 配置与提示词
├── bootstrap.py       # 显式组装与依赖注入
└── main.py            # CLI 入口
```

内核不能导入适配器、配置或组装入口。具体实现由 `bootstrap.create_runner()`
注入；`bootstrap.create_app()` 在 Runner 外组装审核协调器与 CLI。
完整职责、调用时序与扩展方法见 [架构说明](docs/architecture.md)。

## 已有能力与边界

- 流式文字、工具调用、规划/反思、基于消息数的上下文压缩。
- 单次运行最多 **10 轮**；子代理也受此上限限制，支持角色权限衰减和实时消息。
- 声明工具表、运行允许表与阶段限制共同约束执行；未声明工具不能靠模型臆造调用获得权限。
- 写入、编辑、命令和质量检查必须逐次审批；审批绑定调用 ID 和参数指纹，默认拒绝。
- 文件工具拒绝工作区外路径、敏感文件、符号链接与 Windows reparse point。
- 异步输出审核可以停止后续生成与工具启动，但文字可能在审核前短暂展示；
  不能撤销已发生的副作用。详见 [审核边界](docs/safety.md)。
- `run_command` 是本地高权限 shell，**不是沙箱**；批准后可产生工作区外副作用。
  同步线程中的进程也不保证随协程取消而终止。
- CLI 历史保存在内存中；没有跨进程恢复、持久化审计或任务恢复功能。
- “回读验证”和反思只是策略状态，不等同于相关测试全部通过。

## 验证

```powershell
& .venv\Scripts\python.exe -m ruff check .
& .venv\Scripts\python.exe -m pyright
& .venv\Scripts\python.exe -m pytest -q
```

默认只执行单元及内部集成测试，不调用真实模型、网络或 shell。
外部验证需显式运行，可能产生费用：

```powershell
& .venv\Scripts\python.exe -m pytest --run-external tests\external -q
```

CI 仅保留 Ruff、Pyright 和 Windows 内部测试，不启动服务或数据库。
更多说明见 [测试结构](tests/README.md)。
[CLI 产品需求池](docs/observability-requirements.md) 描述未来目标，不代表已实现。
