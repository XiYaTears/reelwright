"""trace 写入：JSONL、密钥脱敏、禁止 reasoning_content/内部思维。

每个事件一行，写后 flush；运行失败也保留已写事件。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {"reasoning_content", "reasoning", "thinking", "api_key", "authorization", "llm_api_key"}
)
MASK = "***"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class TraceWriter:
    """一次运行一个实例；事件同时缓存在内存中便于测试。"""

    def __init__(
        self,
        path: str | Path,
        *,
        run_id: str,
        secrets: Sequence[str] = (),
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._secrets = tuple(secret for secret in secrets if secret)
        self._seq = 0
        self._events: list[dict[str, Any]] = []
        self._handle = self.path.open("w", encoding="utf-8", newline="\n")

    def event(self, event_type: str, *, step: int | None = None, **fields: Any) -> dict[str, Any]:
        self._seq += 1
        record: dict[str, Any] = {
            "seq": self._seq,
            "ts": _now_iso(),
            "run_id": self.run_id,
            "type": event_type,
        }
        if step is not None:
            record["step"] = step
        record.update(self._sanitize(fields))
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str)
        self._handle.write(line + "\n")
        self._handle.flush()
        self._events.append(record)
        return record

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    def __enter__(self) -> TraceWriter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._events)

    def _sanitize(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            cleaned: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if key_text.casefold() in FORBIDDEN_KEYS:
                    continue
                cleaned[key_text] = self._sanitize(item)
            return cleaned
        if isinstance(value, (list, tuple)):
            return [self._sanitize(item) for item in value]
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, MASK)
            return value
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)
