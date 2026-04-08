"""每服务器 JSON 配置：监视论坛、关键词、管理频道等。"""

from __future__ import annotations

import copy
import json
import pathlib
from typing import Any

DATA_DIR = pathlib.Path("data/post_filter")

DEFAULT_GUILD_CONFIG: dict[str, Any] = {
    "enabled": False,
    "admin_channel_id": None,
    "forum_channel_ids": [],
    "keywords": [],
    "verify_window_days": 14,
    "scan_delay_seconds": 4,
    "op_message_limit": 5,
}


def _guild_path(guild_id: int) -> pathlib.Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"{guild_id}.json"


def load_guild_config(guild_id: int) -> dict[str, Any]:
    path = _guild_path(guild_id)
    cfg = copy.deepcopy(DEFAULT_GUILD_CONFIG)
    if not path.exists():
        return cfg
    try:
        with open(path, "r", encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            cfg.update(stored)
    except (json.JSONDecodeError, OSError):
        pass
    cfg.setdefault("forum_channel_ids", [])
    cfg.setdefault("keywords", [])
    return cfg


def save_guild_config(guild_id: int, cfg: dict[str, Any]) -> None:
    path = _guild_path(guild_id)
    merged = copy.deepcopy(DEFAULT_GUILD_CONFIG)
    merged.update(cfg)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
