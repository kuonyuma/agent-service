from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class APISettings(BaseSettings):
    """HTTP 服务和数据库配置，均可通过环境变量覆盖。"""

    model_config = SettingsConfigDict(
        env_prefix="AGENT_SERVICE_",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = (
        "mysql+asyncmy://agent_service:agent_service_dev@127.0.0.1:3307/agent_service"
    )
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    run_timeout_seconds: float = Field(default=120.0, gt=0)
    approval_timeout_seconds: float = Field(default=60.0, gt=0)
    approval_poll_interval_seconds: float = Field(default=0.25, gt=0, le=5)
    stream_event_buffer_size: int = Field(default=256, ge=8, le=4096)
    workspace_root: Path = Path("web-workspaces")
    web_mutating_tools_enabled: bool = False
    web_quality_tool_enabled: bool = False
    sql_echo: bool = False

    @model_validator(mode="after")
    def validate_timeouts(self) -> APISettings:
        approvals_enabled = (
            self.web_mutating_tools_enabled or self.web_quality_tool_enabled
        )
        if approvals_enabled and (
            self.approval_timeout_seconds >= self.run_timeout_seconds
        ):
            raise ValueError("审批超时必须小于 run 总超时。")
        return self


api_settings = APISettings()
