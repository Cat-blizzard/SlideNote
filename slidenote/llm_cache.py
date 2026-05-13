from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LLM_CACHE_SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_cache_key(data: Any) -> str:
    return sha256_text(stable_json(data))


class LLMCache:
    def __init__(self, cache_dir: Path, mode: str = "on") -> None:
        if mode not in {"on", "off", "refresh"}:
            raise ValueError("cache mode must be one of: on, off, refresh")
        self.cache_dir = cache_dir
        self.mode = mode

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    def path_for(self, cache_key: str) -> Path:
        digest = cache_key.split(":", 1)[-1]
        return self.cache_dir / digest[:2] / digest[2:4] / f"{digest}.json"

    def read(self, cache_key: str) -> dict[str, Any] | None:
        if self.mode != "on":
            return None
        path = self.path_for(cache_key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if data.get("schema_version") != LLM_CACHE_SCHEMA_VERSION:
            return None
        if data.get("cache_key") != cache_key:
            return None
        if not isinstance(data.get("output_text"), str):
            return None
        return data

    def write(self, cache_key: str, entry: dict[str, Any]) -> Path | None:
        if not self.enabled:
            return None
        path = self.path_for(cache_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": LLM_CACHE_SCHEMA_VERSION,
            "cache_key": cache_key,
            "created_at": utc_now_iso(),
            **entry,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

