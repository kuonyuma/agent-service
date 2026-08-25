# Agent Service

这是一个用 Python 手写的异步 Gemini Agent Runtime。项目不依赖 LangChain，
自行管理模型流式调用、Function Calling、工具执行、会话持久化和运行状态。

目前有两个复用同一 `AgentRunner` 的入口：

- CLI：保留人工审批，可使用完整工具集。
- FastAPI：通过 SSE 推送运行事件，默认只注册工作区内的读取/列目录工具；
  可在本机显式开启写入/修改工具的一次性审批。

数据库使用 SQLAlchemy 2.0 异步 API、`asyncmy` 和 MySQL 8.4；Schema 变更由
Alembic 管理。架构、事务时序和 ER 图见
[docs/architecture.md](docs/architecture.md)。

![Compose 与 API 本地验收演示](docs/demo.gif)

该 GIF 由 `scripts/generate_demo_gif.ps1` 根据真实 Compose/MySQL/API 响应生成；
为了不消耗外部额度，录制过程不调用 Gemini。

## 环境要求

- Windows 与 PowerShell 7
- Python 3.14
- [uv](https://docs.astral.sh/uv/)
- Docker Desktop（使用 Compose 启动 MySQL 或完整服务时需要）

同步锁定的开发环境：

```powershell
uv sync --frozen --group dev
```

Gemini Key 只从当前进程环境变量读取，未设置时才回退到本地配置文件：

```powershell
$env:GEMINI_API_KEY = "your_api_key_here"
```

不要把真实 Key、数据库 URL 或密码提交到仓库。

## 运行 CLI

```powershell
& .venv\Scripts\python.exe -m agent_service.main
```

CLI 和 HTTP API 使用同一个 `AgentRunner`。调用方传入的历史不会被隐式修改，
最终的 `LoopResult` 会分别给出完整运行历史和本轮 `new_contents`。

## 一键启动 API 与 MySQL

下面的命令会构建镜像、启动 MySQL 8.4、执行 `alembic upgrade head`，再启动 API：

```powershell
docker compose up -d --build
docker compose ps
```

宿主机端口只绑定到本机：

- API：`127.0.0.1:8000`
- MySQL：`127.0.0.1:3307`

检查状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

OpenAPI 页面位于 <http://127.0.0.1:8000/docs>。

查看日志或停止服务：

```powershell
docker compose logs -f api
docker compose down
```

`docker compose down` 不删除 MySQL volume；只有显式加 `-v` 才会删除数据。

## 在 Windows 主机直接运行 API

先只启动 MySQL：

```powershell
docker compose up -d mysql
```

然后设置主机访问的数据库 URL。以下密码对应 Compose 的本地开发默认值：

```powershell
$env:AGENT_SERVICE_DATABASE_URL = "mysql+asyncmy://agent_service:agent_service_dev@127.0.0.1:3307/agent_service"
```

迁移会修改上述数据库的 Schema，请确认 URL 后再执行：

```powershell
& .venv\Scripts\python.exe -m alembic upgrade head
```

最后只监听本机地址：

```powershell
& .venv\Scripts\python.exe -m uvicorn agent_service.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

可用环境变量还包括：

- `AGENT_SERVICE_RUN_TIMEOUT_SECONDS`：Runner 生产事件的累计超时，默认 120 秒；
  客户端读取 SSE 的背压时间不计入该预算。
- `AGENT_SERVICE_SQL_ECHO`：是否输出 SQL，默认 `false`。
- `AGENT_SERVICE_WORKSPACE_ROOT`：Web 文件工具可访问的共享本地工作区，默认
  `web-workspaces`。
- `AGENT_SERVICE_WEB_MUTATING_TOOLS_ENABLED`：是否向 Web 声明需要逐次审批的
  `write_file`、`edit_file`，默认 `false`。
- `AGENT_SERVICE_WEB_QUALITY_TOOL_ENABLED`：是否声明固定动作的
  `run_quality_check`，默认 `false`；只支持 `ruff`、`pyright`，不接受命令字符串。
- `AGENT_SERVICE_APPROVAL_TIMEOUT_SECONDS`：审批自动拒绝时间，默认 60 秒，
  开启审批工具时必须小于 run 总超时。
- `AGENT_SERVICE_STREAM_EVENT_BUFFER_SIZE`：单个 SSE run 的有界事件缓冲区，
  默认 256。

## 最小 API 示例

创建会话：

```powershell
$conversation = Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/conversations" `
  -ContentType "application/json" `
  -Body (@{ title = "项目分析" } | ConvertTo-Json)

$conversation.id
```

创建 run 并读取 SSE。浏览器原生 `EventSource` 不能发送 POST，Web 客户端应使用
`fetch()` 读取响应流；PowerShell 可直接使用 `curl.exe`：

```powershell
curl.exe -N -X POST `
  "http://127.0.0.1:8000/api/v1/conversations/$($conversation.id)/runs" `
  -H "Content-Type: application/json" `
  --data-raw '{"message":"请介绍这个项目"}'
```

响应中的 `X-Run-ID` 可用于查询权威状态：

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/runs/<run_id>"
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/conversations/$($conversation.id)/messages"
```

最小接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/conversations` | 创建会话 |
| `GET` | `/api/v1/conversations/{id}` | 查询会话 |
| `GET` | `/api/v1/conversations/{id}/messages` | 查询有序消息 |
| `POST` | `/api/v1/conversations/{id}/runs` | 创建 run 并返回 SSE |
| `GET` | `/api/v1/runs/{run_id}` | 查询 run 状态 |
| `GET` | `/api/v1/runs/{run_id}/tool-calls` | 查询工具与审批审计 |
| `POST` | `/api/v1/runs/{run_id}/tool-calls/{call_id}/approval` | 批准或拒绝一次工具调用 |
| `GET` | `/health/live` | 进程存活检查，不访问数据库 |
| `GET` | `/health/ready` | 使用独立短连接检查数据库 |

SSE 事件依次使用以下具名类型：

- `text.delta`
- `tool.requested`
- `tool.approval_required`，包含 `tool_call_id` 与审批过期时间
- `tool.completed`，保留 `is_error`
- `run.completed`
- `run.failed`

每条事件都有递增的 SSE `id`，JSON `data` 中包含 `run_id`。最终事件会在 run
终态和消息已经提交后发送；流开始后的错误通过 `run.failed` 表达，不能再修改 HTTP
状态码。

## 事务与并发设计

`POST /runs` 不使用一个贯穿流式响应的依赖注入 Session：

1. 短事务原子占用 `conversations.active_run_id`、创建 run、保存用户消息并提交。
2. 事务外调用 Gemini、工作区工具并等待可能的人工审批。
3. 工具事件分别使用短事务记录。
4. 短事务保存模型消息、run 终态，并用 run ID 条件释放占用。

同一会话的两个请求竞争时，只有一条
`UPDATE ... WHERE active_run_id IS NULL` 能成功，另一个请求返回 `409 Conflict`。
Gemini 生成、工具执行和审批等待期间都不持有数据库锁。客户端取消流时，
服务会用新的 Session 将仍在运行的 run 标记为 `cancelled`。若进程硬退出，下一次
竞争请求会把超过 `AGENT_SERVICE_RUN_TIMEOUT_SECONDS` 的旧 run 标记为
`RunExpired` 后原子回收占用；这是一版单节点超时租约，不等同于多副本 heartbeat。

## 工具审批流程

默认 Web 配置仍是只读。需要练习审批状态机时，在 `.env` 中只对本机开启：

```dotenv
AGENT_SERVICE_WEB_MUTATING_TOOLS_ENABLED=true
```

重启 Compose 后，保持 `POST /runs` 的 SSE 连接打开。模型请求写工具时会依次收到
`tool.requested` 和 `tool.approval_required`；在另一个 PowerShell 窗口查询并决定：

```powershell
$calls = Invoke-RestMethod `
  "http://127.0.0.1:8000/api/v1/runs/<run_id>/tool-calls"

$body = @{ decision = "approve"; reason = "允许本次本地修改" } |
  ConvertTo-Json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/runs/<run_id>/tool-calls/$($calls[0].call_id)/approval" `
  -ContentType "application/json" `
  -Body $body
```

审批使用 `approval_status='pending'` 的条件更新；重复决定或批准已超时调用返回 409。
服务先用短事务记录 `waiting_approval`，随后关闭 Session，在事务外等待通知并短轮询
数据库。审批只解锁原生产任务，不会由审批接口直接执行工具。未接入认证前，审计主体
固定记录为 `local_operator`，不能把它理解成可信用户身份。

## 测试与质量检查

默认测试不会访问网络、Gemini 或数据库：

```powershell
& .venv\Scripts\python.exe -m pytest -q --basetemp .pytest_temp\local
& .venv\Scripts\python.exe -m ruff check .
& .venv\Scripts\python.exe -m pyright
```

真实 MySQL 测试只接受显式的 `AGENT_SERVICE_TEST_DATABASE_URL`，并会清空该测试库中
本项目四张业务表的数据。不要把开发库或生产库 URL 传给它。先对同一个专用测试库
执行迁移，再运行：

```powershell
$env:AGENT_SERVICE_TEST_DATABASE_URL = "mysql+asyncmy://<user>:<password>@127.0.0.1:3307/agent_service_test"
$env:AGENT_SERVICE_DATABASE_URL = $env:AGENT_SERVICE_TEST_DATABASE_URL

& .venv\Scripts\python.exe -m alembic upgrade head
& .venv\Scripts\python.exe -m pytest --run-external -q `
  tests\external\test_mysql_migrations.py `
  tests\external\test_mysql_run_service.py
```

真实 Gemini 测试仍需显式执行：

```powershell
& .venv\Scripts\python.exe -m pytest --run-external tests\external\test_gemini_client.py tests\external\test_gemini_stream.py -q
```

CI 会另外启动真实 MySQL 8.4，验证迁移 upgrade/check/downgrade/upgrade、并发竞争和
取消状态；SQLite 不参与 MySQL 行为验证。

也可以用 PowerShell 脚本在专用 `_test` 数据库上一次完成迁移升降级和全部 MySQL
测试。脚本会执行 `downgrade base`，不要传入开发库或生产库：

```powershell
$env:AGENT_SERVICE_TEST_DATABASE_URL = "mysql+asyncmy://<user>:<password>@127.0.0.1:3307/agent_service_test"
& .\scripts\verify_mysql.ps1
```

Compose 已运行时可重新生成演示 GIF：

```powershell
& .\scripts\generate_demo_gif.ps1
```

## 当前安全边界

- FastAPI 默认显式开放 `list_files`、`read_file`；文件工具只接受工作区内相对
  路径，拒绝 `..`、绝对/UNC/设备路径、NTFS ADS、敏感目录、符号链接和 Windows
  reparse point，并限制文件与输出大小。
- 写入/修改默认关闭；开启后每个调用都绑定 `call_id + 工具名 + 参数指纹` 单独
  审批，并记录批准、拒绝、过期或取消的时间、主体和原因。
- 任意字符串的 `run_command` 不向 Web 暴露。可选质量检查由服务端构造固定 argv，
  使用 `shell=False` 和净化后的环境；它是命令白名单，不是操作系统级沙箱。
- 当前工作区由本机所有会话共享；运行中的审批依赖原 SSE 生产任务，进程重启后不能
  恢复到函数调用中间状态。文件路径检查也不能消除恶意本机进程并发替换路径造成的
  TOCTOU 风险。
- JWT、用户/租户隔离、限流、独立低权限工具 worker 和可恢复事件 outbox 尚未实现。
  因此 API 仍只应绑定 `127.0.0.1`，不得转发到局域网或互联网。

配置文件和系统提示词在进程启动时加载；修改后需要重启进程。
