"""config：.env.local 加载、必填校验、LLM_PROXY 透传。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import ConfigError, LLMSettings, load_llm_settings


def _clear_env(monkeypatch) -> None:
    for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_PROXY"):
        monkeypatch.delenv(name, raising=False)


def test_loads_required_env_without_proxy(monkeypatch, tmp_path: Path) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_MODEL", "deepseek-flash")

    settings = load_llm_settings(tmp_path / "missing.env")
    assert settings == LLMSettings(
        api_key="sk-test",
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        proxy=None,
    )


def test_proxy_is_optional_and_trimmed(monkeypatch, tmp_path: Path) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("LLM_MODEL", "deepseek-flash")
    monkeypatch.setenv("LLM_PROXY", "  http://127.0.0.1:7897  ")

    settings = load_llm_settings(tmp_path / "missing.env")
    assert settings.proxy == "http://127.0.0.1:7897"


def test_missing_required_var_raises_without_values(monkeypatch, tmp_path: Path) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    with pytest.raises(ConfigError) as error_info:
        load_llm_settings(tmp_path / "missing.env")
    message = str(error_info.value)
    assert "LLM_MODEL" in message
    assert "sk-test" not in message


def test_reads_env_file(monkeypatch, tmp_path: Path) -> None:
    _clear_env(monkeypatch)
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "LLM_API_KEY=sk-from-file\n"
        "LLM_BASE_URL=https://api.deepseek.com/v1\n"
        "LLM_MODEL=deepseek-flash\n"
        "LLM_PROXY=\n",
        encoding="utf-8",
    )
    settings = load_llm_settings(env_file)
    assert settings.api_key == "sk-from-file"
    assert settings.proxy is None
