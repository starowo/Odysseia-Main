from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

DEFAULT_INTERVAL_SECONDS = 6 * 60 * 60
DATA_DIR = Path("data/thread_pin_watch")
DATA_DIR.mkdir(parents=True, exist_ok=True)

SUMMARY_KEYS = (
    "total",
    "already_pinned",
    "restored",
    "missing",
    "invalid",
    "failed",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_thread_link(guild_id: Optional[int], parent_id: Optional[int], thread_id: int) -> str:
    if guild_id and parent_id:
        return f"https://discord.com/channels/{guild_id}/{parent_id}/{thread_id}"
    if guild_id:
        return f"https://discord.com/channels/{guild_id}/{thread_id}"
    return str(thread_id)


class ThreadPinWatchStore:
    def __init__(self, data_dir: str | Path = DATA_DIR):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, guild_id: int) -> Path:
        return self.data_dir / f"{int(guild_id)}.json"

    def _default_config(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "interval_seconds": DEFAULT_INTERVAL_SECONDS,
            "report_channel_id": None,
            "last_check_at": None,
            "last_summary": {key: 0 for key in SUMMARY_KEYS},
            "threads": [],
        }

    def list_guild_ids(self) -> list[int]:
        guild_ids: list[int] = []
        for file in self.data_dir.glob("*.json"):
            try:
                guild_ids.append(int(file.stem))
            except ValueError:
                continue
        return sorted(guild_ids)

    def load(self, guild_id: int) -> dict[str, Any]:
        path = self._path(guild_id)
        data = self._default_config()
        if not path.exists():
            return data

        try:
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError):
            return data

        if not isinstance(raw, dict):
            return data

        data.update({
            "enabled": bool(raw.get("enabled", True)),
            "interval_seconds": int(raw.get("interval_seconds", DEFAULT_INTERVAL_SECONDS) or DEFAULT_INTERVAL_SECONDS),
            "report_channel_id": raw.get("report_channel_id"),
            "last_check_at": raw.get("last_check_at"),
            "last_summary": self._normalize_summary(raw.get("last_summary")),
        })

        threads = raw.get("threads", [])
        if isinstance(threads, list):
            data["threads"] = [
                self._normalize_thread_record(record)
                for record in threads
                if isinstance(record, dict) and record.get("thread_id") is not None
            ]
        return data

    def save(self, guild_id: int, data: dict[str, Any]) -> None:
        normalized = self._default_config()
        normalized.update({
            "enabled": bool(data.get("enabled", True)),
            "interval_seconds": int(data.get("interval_seconds", DEFAULT_INTERVAL_SECONDS) or DEFAULT_INTERVAL_SECONDS),
            "report_channel_id": data.get("report_channel_id"),
            "last_check_at": data.get("last_check_at"),
            "last_summary": self._normalize_summary(data.get("last_summary")),
            "threads": [
                self._normalize_thread_record(record)
                for record in data.get("threads", [])
                if isinstance(record, dict) and record.get("thread_id") is not None
            ],
        })
        normalized["threads"] = self._sort_records(normalized["threads"])

        path = self._path(guild_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2)

    def get_threads(self, guild_id: int, forum_id: Optional[int] = None) -> list[dict[str, Any]]:
        threads = self.load(guild_id).get("threads", [])
        if forum_id is None:
            return deepcopy(threads)
        return deepcopy([record for record in threads if record.get("parent_id") == int(forum_id)])

    def is_watched(self, guild_id: int, thread_id: int) -> bool:
        return any(record.get("thread_id") == int(thread_id) for record in self.load(guild_id).get("threads", []))

    def add_thread_records(self, guild_id: int, records: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        data = self.load(guild_id)
        existing_map = {int(record["thread_id"]): record for record in data["threads"]}
        added: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        for record in records:
            normalized = self._normalize_thread_record(record)
            thread_id = int(normalized["thread_id"])
            existing = existing_map.get(thread_id)
            if existing:
                self._merge_thread_record(existing, normalized)
                skipped.append(deepcopy(existing))
                continue

            data["threads"].append(normalized)
            existing_map[thread_id] = data["threads"][-1]
            added.append(deepcopy(data["threads"][-1]))

        self.save(guild_id, data)
        return {"added": added, "skipped": skipped}

    def remove_thread(self, guild_id: int, thread_id: int) -> Optional[dict[str, Any]]:
        data = self.load(guild_id)
        thread_id = int(thread_id)
        for index, record in enumerate(data["threads"]):
            if int(record.get("thread_id", 0)) == thread_id:
                removed = deepcopy(record)
                data["threads"].pop(index)
                self.save(guild_id, data)
                return removed
        return None

    def set_report_channel(self, guild_id: int, channel_id: int) -> dict[str, Any]:
        data = self.load(guild_id)
        data["report_channel_id"] = int(channel_id)
        self.save(guild_id, data)
        return data

    def clear_report_channel(self, guild_id: int) -> dict[str, Any]:
        data = self.load(guild_id)
        data["report_channel_id"] = None
        self.save(guild_id, data)
        return data

    def set_enabled(self, guild_id: int, enabled: bool) -> dict[str, Any]:
        data = self.load(guild_id)
        data["enabled"] = bool(enabled)
        self.save(guild_id, data)
        return data

    def update_check_results(
        self,
        guild_id: int,
        results: Iterable[dict[str, Any]],
        *,
        checked_at: Optional[str] = None,
        summary: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        data = self.load(guild_id)
        checked_at = checked_at or utc_now_iso()
        record_map = {int(record["thread_id"]): record for record in data["threads"]}

        for result in results:
            if not isinstance(result, dict) or result.get("thread_id") is None:
                continue
            thread_id = int(result["thread_id"])
            record = record_map.get(thread_id)
            if record is None:
                continue

            if result.get("title_snapshot"):
                record["title_snapshot"] = str(result["title_snapshot"])
            if result.get("thread_link_snapshot"):
                record["thread_link_snapshot"] = str(result["thread_link_snapshot"])
            if result.get("parent_id") is not None:
                record["parent_id"] = int(result["parent_id"])
            if result.get("parent_name_snapshot"):
                record["parent_name_snapshot"] = str(result["parent_name_snapshot"])

            record["last_status"] = str(result.get("status") or "pending")
            record["last_checked_at"] = result.get("checked_at") or checked_at
            error = result.get("error")
            record["last_error"] = None if error in (None, "") else str(error)[:1000]

        if summary is not None:
            data["last_check_at"] = checked_at
            data["last_summary"] = self._normalize_summary(summary)

        self.save(guild_id, data)
        return data

    def _normalize_summary(self, summary: Any) -> dict[str, int]:
        result = {key: 0 for key in SUMMARY_KEYS}
        if isinstance(summary, dict):
            for key in SUMMARY_KEYS:
                try:
                    result[key] = int(summary.get(key, 0) or 0)
                except (TypeError, ValueError):
                    result[key] = 0
        return result

    def _normalize_thread_record(self, record: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            "thread_id": int(record["thread_id"]),
            "title_snapshot": str(record.get("title_snapshot") or f"帖子 {record['thread_id']}"),
            "thread_link_snapshot": record.get("thread_link_snapshot"),
            "parent_id": int(record["parent_id"]) if record.get("parent_id") is not None else None,
            "parent_name_snapshot": str(record.get("parent_name_snapshot") or "未知论坛"),
            "added_by": int(record["added_by"]) if record.get("added_by") is not None else None,
            "added_at": record.get("added_at") or utc_now_iso(),
            "source": str(record.get("source") or "manual"),
            "last_status": str(record.get("last_status") or "pending"),
            "last_checked_at": record.get("last_checked_at"),
            "last_error": None if record.get("last_error") in (None, "") else str(record.get("last_error"))[:1000],
        }
        if not normalized["thread_link_snapshot"]:
            normalized["thread_link_snapshot"] = build_thread_link(None, normalized["parent_id"], normalized["thread_id"])
        return normalized

    def _merge_thread_record(self, target: dict[str, Any], incoming: dict[str, Any]) -> None:
        for key in ("title_snapshot", "thread_link_snapshot", "parent_id", "parent_name_snapshot"):
            value = incoming.get(key)
            if value not in (None, ""):
                target[key] = value

        if target.get("added_by") is None and incoming.get("added_by") is not None:
            target["added_by"] = incoming["added_by"]
        if not target.get("added_at"):
            target["added_at"] = incoming.get("added_at") or utc_now_iso()
        if not target.get("source"):
            target["source"] = incoming.get("source") or "manual"

    def _sort_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            records,
            key=lambda item: (
                str(item.get("parent_name_snapshot") or "").lower(),
                str(item.get("title_snapshot") or "").lower(),
                int(item.get("thread_id") or 0),
            ),
        )
