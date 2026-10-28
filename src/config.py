"""运行配置：真实模式读取 .env.local，构造 LLM 设置。

密钥只在本模块与 DeepSeek 客户端之间传递；不打印、不写入 trace/output。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REQUIRED_ENV_VARS: tuple[str, ...] = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")
OPTIONAL_ENV_VARS: tuple[str, ...] = ("LLM_PROXY",)


class ConfigError(RuntimeError):
    """缺少或非法的运行配置（真实模式）；消息中不包含密钥值。"""


@dataclass(frozen=True, slots=True)
class LLMSettings:
    api_key: str
    base_url: str
    model: str
    proxy: str | None = None


def load_llm_settings(env_file: str | Path = ".env.local") -> LLMSettings:
    """读取 .env.local（不覆盖已存在的进程环境变量），缺变量时明确报错。

    `LLM_PROXY` 可选：为空则直连；客户端 `trust_env=False`，不读系统代理。
    """
    path = Path(env_file)
    if path.is_file():
        load_dotenv(path, override=False, encoding="utf-8")
    values = {name: os.environ.get(name, "").strip() for name in REQUIRED_ENV_VARS}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigError(f"真实模式缺少环境变量：{', '.join(missing)}（在项目根 .env.local 配置）")
    proxy = os.environ.get("LLM_PROXY", "").strip()
    return LLMSettings(
        api_key=values["LLM_API_KEY"],
        base_url=values["LLM_BASE_URL"],
        model=values["LLM_MODEL"],
        proxy=proxy or None,
    )
