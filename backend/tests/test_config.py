import pytest

from backend.app import config
from backend.app.storage import Store


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "store", Store(tmp_path / "settings.db"))
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
