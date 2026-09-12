"""config 启动校验逻辑测试：SECRET_KEY 缺失/空白时拒绝启动。"""

import importlib
from pathlib import Path

import pytest
import yaml

from app import config


def _restore_env_and_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    importlib.reload(config)


def test_missing_secret_key_refuses_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRET_KEY", raising=False)
    reloaded = importlib.reload(config)

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        reloaded.ensure_ready()

    _restore_env_and_reload(monkeypatch)


def test_blank_secret_key_refuses_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "   ")
    reloaded = importlib.reload(config)

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        reloaded.ensure_ready()

    _restore_env_and_reload(monkeypatch)


def test_ensure_ready_creates_data_dirs() -> None:
    config.ensure_ready()

    assert config.DB_PATH.parent.is_dir()
    assert config.UPLOADS_DIR.is_dir()
    assert config.BACKUPS_DIR.is_dir()


def test_weak_default_secret_refuses_start_without_dev_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET_KEY", "dev-secret-change-me")
    monkeypatch.delenv("DEV_ALLOW_WEAK_SECRETS", raising=False)
    reloaded = importlib.reload(config)

    with pytest.raises(RuntimeError, match="DEV_ALLOW_WEAK_SECRETS"):
        reloaded.ensure_ready()

    _restore_env_and_reload(monkeypatch)


def test_weak_agent_secret_refuses_start_when_runtime_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET_KEY", "real-random-session-key")
    monkeypatch.setenv("AGENT_RUNTIME_ENABLED", "1")
    monkeypatch.setenv("AGENT_SERVICE_SECRET", "dev-agent-secret-change-me")
    monkeypatch.delenv("DEV_ALLOW_WEAK_SECRETS", raising=False)
    reloaded = importlib.reload(config)

    with pytest.raises(RuntimeError, match="DEV_ALLOW_WEAK_SECRETS"):
        reloaded.ensure_ready()

    _restore_env_and_reload(monkeypatch)


def test_dev_flag_allows_weak_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "dev-secret-change-me")
    monkeypatch.setenv("AGENT_RUNTIME_ENABLED", "1")
    monkeypatch.setenv("AGENT_SERVICE_SECRET", "dev-agent-secret-change-me")
    monkeypatch.setenv("DEV_ALLOW_WEAK_SECRETS", "1")
    reloaded = importlib.reload(config)

    # 显式开发标记：放行（仅本地试用）
    reloaded.ensure_ready()

    _restore_env_and_reload(monkeypatch)


# ---- 09-11 发布可观测性 R5/AC-5：配置链与 compose 模板透传 ----

_STEWARD_OBSERVABILITY_KEYS = (
    "STEWARD_ENABLED",
    "STEWARD_WORKER_ENABLED",
    "STEWARD_SCAN_INTERVAL_SECONDS",
    "STEWARD_SCAN_MAX_JOBS_PER_TICK",
    "STEWARD_RETRY_BACKOFF_FIRST_SECONDS",
    "STEWARD_RETRY_BACKOFF_SECOND_SECONDS",
    "STEWARD_RERUN_COOLDOWN_SECONDS",
    "STEWARD_ALERT_QUEUE_SECONDS",
)


def test_steward_alert_threshold_config_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量 → config 属性的配置链：显式阈值按配置生效，缺省 0=自动。"""
    monkeypatch.setenv("STEWARD_ALERT_QUEUE_SECONDS", "120")
    reloaded = importlib.reload(config)
    assert reloaded.STEWARD_ALERT_QUEUE_SECONDS == 120

    monkeypatch.delenv("STEWARD_ALERT_QUEUE_SECONDS", raising=False)
    reloaded = importlib.reload(config)
    assert reloaded.STEWARD_ALERT_QUEUE_SECONDS == 0
    _restore_env_and_reload(monkeypatch)


def test_steward_alert_threshold_bounds_refuse_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """阈值越界（负数/超上界）在启动校验拒启（fail-closed 配置防线）。"""
    monkeypatch.setenv("STEWARD_ALERT_QUEUE_SECONDS", "-1")
    reloaded = importlib.reload(config)
    with pytest.raises(RuntimeError, match="STEWARD_ALERT_QUEUE_SECONDS"):
        reloaded._validate_steward_scheduling()

    monkeypatch.setenv("STEWARD_ALERT_QUEUE_SECONDS", "86401")
    reloaded = importlib.reload(config)
    with pytest.raises(RuntimeError, match="STEWARD_ALERT_QUEUE_SECONDS"):
        reloaded._validate_steward_scheduling()
    # 恢复重载发生在 monkeypatch 卸载之前：必须先删变量再重载，
    # 否则 86401 会残留在 config 模块属性里污染后续测试。
    monkeypatch.delenv("STEWARD_ALERT_QUEUE_SECONDS", raising=False)
    _restore_env_and_reload(monkeypatch)


def test_compose_template_passes_steward_observability_keys() -> None:
    """compose 模板把可观测性新键透传到 api 服务 environment（.env 可覆盖）。"""
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    with compose_path.open(encoding="utf-8") as fh:
        compose = yaml.safe_load(fh)
    environment = compose["services"]["api"]["environment"]
    for key in _STEWARD_OBSERVABILITY_KEYS:
        assert key in environment, f"compose 缺少 {key} 透传"
        assert str(environment[key]).startswith("${"), f"{key} 必须是可覆盖的模板变量"
