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
