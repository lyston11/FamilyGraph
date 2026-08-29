"""config 启动校验逻辑测试：SECRET_KEY 缺失/空白时拒绝启动。"""

import importlib

import pytest

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
