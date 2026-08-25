from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_service.config.api_settings import APISettings


def test_api_settings_exposes_secure_web_defaults(tmp_path):
    settings = APISettings(
        workspace_root=tmp_path,
    )

    assert settings.workspace_root == Path(tmp_path)
    assert not settings.web_mutating_tools_enabled
    assert not settings.web_quality_tool_enabled
    assert settings.host == "127.0.0.1"


def test_approval_timeout_must_be_lower_than_run_timeout(tmp_path):
    with pytest.raises(ValidationError, match="审批超时必须小于 run 总超时"):
        APISettings(
            workspace_root=tmp_path,
            run_timeout_seconds=10,
            approval_timeout_seconds=10,
            web_mutating_tools_enabled=True,
        )
