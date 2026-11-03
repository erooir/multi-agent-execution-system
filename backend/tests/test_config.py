import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from backend.app import config, storage
from backend.app.storage import Store


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "store", Store(tmp_path / "settings.db"))
    monkeypatch.setattr(config, "get_api_key", lambda: "test-only-key")


def test_output_default_8000_and_saved_lower_limit_preserved(isolated_settings):
    assert config.get_settings()["max_output_tokens"] == 8000
    config.save_settings({"max_output_tokens": 2048})
    assert config.get_settings()["max_output_tokens"] == 2048
    assert config.save_settings({"default_mode": "rehearsal"})["max_output_tokens"] == 2048


@pytest.mark.parametrize("output_cap", [256, 8000])
def test_output_settings_accept_valid_boundaries(isolated_settings, output_cap):
    assert config.save_settings({"max_output_tokens": output_cap})["max_output_tokens"] == output_cap


@pytest.mark.parametrize("output_cap", [255, 8001])
def test_output_settings_reject_outside_boundaries(isolated_settings, output_cap):
    with pytest.raises(ValueError, match="256至8000"):
        config.save_settings({"max_output_tokens": output_cap})
    assert config.get_settings()["max_output_tokens"] == 8000


def install_registry(monkeypatch, query):
    registry = SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        OpenKey=lambda *args: nullcontext(),
        QueryValueEx=query,
    )
    monkeypatch.setattr(config.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "winreg", registry)


def test_api_key_uses_only_the_named_process_user_variable(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY_ZX", "  synthetic-current-key  ")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-legacy-key")
    install_registry(
        monkeypatch,
        lambda *args: pytest.fail("进程变量存在时不应读取注册表"),
    )
    assert config.get_api_key() == "synthetic-current-key"


def test_api_key_falls_back_to_the_same_windows_user_variable(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY_ZX", raising=False)
    install_registry(monkeypatch, lambda *args: ("  synthetic-registry-key  ", 1))
    assert config.get_api_key() == "synthetic-registry-key"


def test_legacy_api_key_variables_are_ignored(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY_ZX", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-legacy-key")
    monkeypatch.setenv("deepseek_api_key", "synthetic-legacy-lowercase-key")

    def missing(*args):
        raise FileNotFoundError

    install_registry(monkeypatch, missing)
    assert config.get_api_key() is None
