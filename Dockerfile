FROM ghcr.io/astral-sh/uv:0.11.20 AS uv

FROM python:3.14-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/

# 先仅安装锁定的第三方依赖，以便源码变更时复用依赖层。
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations

# config.yaml 只保留非敏感模型配置，Gemini key 必须在运行时注入。
RUN sed -i 's/^[[:space:]]*key:.*/  key: ""/' src/agent_service/config/config.yaml \
    && uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 agent-service \
    && chown -R agent-service:agent-service /app

USER agent-service

EXPOSE 8000

CMD ["uvicorn", "agent_service.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
