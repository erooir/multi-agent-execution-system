import json
import os
import shutil
import subprocess
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


@pytest.mark.parametrize("mode", ["file", "bom", "process_override", "missing", "disabled"])
def test_dotenv_loaded_before_model_configuration(tmp_path, mode):
    from pathlib import Path

    # Run the real package initialization in a fresh checkout without touching
    # the developer's .env or importing a business database.
    project = tmp_path / "checkout"
    package = project / "backend/app"
    package.mkdir(parents=True)
    source = Path(config.__file__).parent
    for name in ("__init__.py", "config.py", "resource_limits.py"):
        shutil.copyfile(source / name, package / name)
    key = "synthetic-$HOME-${USER}#literal"
    if mode != "missing":
        (project / ".env").write_text(
            "HANGZHI_UPSTREAM_MODEL_URL=http://127.0.0.1:8001/v1\n"
            "HANGZHI_UPSTREAM_MODEL_ID=hangzhi-qwen38\n"
            f"HANGZHI_UPSTREAM_API_KEY='{key}'\n"
            "HANGZHI_UPSTREAM_BILLING=internal_zero\n"
            "HANGZHI_CONTEXT_TOKENS=32768\n"
            "HANGZHI_OFFLINE=false\n",
            encoding="utf-8-sig" if mode == "bom" else "utf-8",
        )
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("HANGZHI_", "DEEPSEEK_")) and k != "PYTHON_DOTENV_DISABLED"
    }
    expected = key
    if mode == "process_override":
        env.update(HANGZHI_UPSTREAM_API_KEY="operator-process-key", HANGZHI_OFFLINE="true")
        expected = "operator-process-key"
    if mode == "disabled":
        env["PYTHON_DOTENV_DISABLED"] = "1"
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "import json, os; from backend.app.config import get_api_key, intranet_provider; "
        "provider=intranet_provider(); "
        f"print(json.dumps({{'model': provider.model if provider else None, 'key_matches': get_api_key()=={expected!r}, "
        "'offline': os.environ.get('HANGZHI_OFFLINE')}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(project)],
        cwd=tmp_path,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    assert key not in result.stdout and "operator-process-key" not in result.stdout
    value = json.loads(result.stdout)
    if mode in {"missing", "disabled"}:
        assert value == {"model": None, "key_matches": False, "offline": None}
    else:
        assert value == {
            "model": "hangzhi-qwen38",
            "key_matches": True,
            "offline": "true" if mode == "process_override" else "false",
        }
