# Agent Service 项目总结

本项目是一个使用 Python 自研的异步 Gemini Agent Runtime，不依赖 LangChain，自行实现并管理流式模型交互、Function Calling、工具执行调度、会话持久化和运行状态机。

---

## 一、技术栈总结

### 1. 语言与运行时
- **Python**: `>= 3.14`
- **包管理与构建工具**: [uv](https://docs.astral.sh/uv/)、Hatchling (`hatchling.build`)

### 2. AI / 大语言模型
- **Google GenAI SDK**: `google-genai`（接入 Gemini 异步流式模型与函数调用能力）

### 3. Web 与网络服务
- **FastAPI**: `>= 0.141.1`，提供异步 HTTP API 与基于 SSE（Server-Sent Events）的实时事件流
- **Uvicorn**: ASGI 服务器，负责 API 进程部署

### 4. 数据存储与持久化
- **数据库**: MySQL 8.4
- **ORM / 查询库**: SQLAlchemy 2.0 异步模式 (`sqlalchemy[asyncio]>=2.0.52`)
- **异步驱动**: `asyncmy`
- **数据迁移**: Alembic (`alembic>=1.19.1`)

### 5. 终端交互与 UI
- **CLI 界面**: `prompt-toolkit`、`rich`

### 6. 配置与校验
- **Pydantic Settings**: `pydantic-settings>=2.15`
- **PyYAML**: `pyyaml`

### 7. 测试与代码质量
- **测试框架**: `pytest`、`pytest-asyncio`（分为 unit、integration 和 external 测试）
- **类型检查**: `pyright>=1.1.411`
- **代码规范 / Linter**: `ruff>=0.16.3`

### 8. 容器化
- **Docker / Docker Compose**: 用于一键编排并启动 API 与 MySQL 8.4 容器服务

---

## 二、启动与运行方式

### 0. 准备工作
- **同步开发依赖**:
  ```powershell
  uv sync --frozen --group dev
  ```
- **配置环境变量（Gemini API Key）**:
  ```powershell
  $env:GEMINI_API_KEY = "your_api_key_here"
  ```

---

### 1. 运行 CLI 交互终端
CLI 提供带人工审批能力的完整工具集：
```powershell
& .venv\Scripts\python.exe -m agent_service.main
```

---

### 2. 一键启动 API 与 MySQL（Docker Compose，推荐）
自动构建镜像、启动 MySQL 8.4、执行 Alembic 数据库迁移，并启动 FastAPI 服务：
```powershell
# 后台构建并启动
docker compose up -d --build

# 查看运行状态
docker compose ps

# 查看 API 日志
docker compose logs -f api

# 停止容器服务（数据卷保留）
docker compose down
```
- **服务端口**:
  - API: `http://127.0.0.1:8000`（OpenAPI 文档位于 `http://127.0.0.1:8000/docs`）
  - MySQL: `127.0.0.1:3307`
- **健康检查**:
  - 进程存活：`Invoke-RestMethod http://127.0.0.1:8000/health/live`
  - 数据库就绪：`Invoke-RestMethod http://127.0.0.1:8000/health/ready`

---

### 3. 在主机直接运行 API（配合 Docker 中的 MySQL）
1. **仅启动 MySQL 容器**:
   ```powershell
   docker compose up -d mysql
   ```
2. **配置数据库连接环境变量**:
   ```powershell
   $env:AGENT_SERVICE_DATABASE_URL = "mysql+asyncmy://agent_service:agent_service_dev@127.0.0.1:3307/agent_service"
   ```
3. **执行数据库迁移**:
   ```powershell
   & .venv\Scripts\python.exe -m alembic upgrade head
   ```
4. **启动 FastAPI 服务**:
   ```powershell
   & .venv\Scripts\python.exe -m uvicorn agent_service.api.app:create_app --factory --host 127.0.0.1 --port 8000
   ```
