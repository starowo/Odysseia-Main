"""反商业化 aff 处罚与补录记录的 SQLite 存储层。"""
from __future__ import annotations

import asyncio
import contextlib
import datetime
import pathlib
import sqlite3
import threading
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS aff_punishments (
    punish_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    guild_id INTEGER NOT NULL,
    offense_no INTEGER NOT NULL,
    record_type TEXT NOT NULL DEFAULT 'punish',
    mute_seconds INTEGER NOT NULL DEFAULT 0,
    warn_days INTEGER NOT NULL DEFAULT 0,
    moderator_id INTEGER NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    revoked_at TEXT,
    revoked_by INTEGER,
    revoke_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_aff_punishments_user
    ON aff_punishments (user_id, status);
"""


class AffPunishDatabase:
    """线程安全的异步 SQLite 包装器，支持跨服累计查询、补录与撤销。"""

    def __init__(self, db_path: str = "data/anticommercial/aff_records.db"):
        self.db_path = pathlib.Path(db_path)
        self._initialized = False
        self._init_lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.db_path.exists()
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 5000")
            if not self._initialized or not existed:
                with self._init_lock:
                    if not self._initialized or not existed:
                        conn.execute("PRAGMA journal_mode = WAL")
                        conn.executescript(SCHEMA)
                        conn.commit()
                        self._initialized = True
            return conn
        except Exception:
            conn.close()
            raise

    @staticmethod
    def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        return dict(row) if row is not None else None

    async def get_active_records(
        self,
        user_id: int,
        guild_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """获取指定用户在统计范围内的所有未撤销记录（按时间正序）。"""

        def _query() -> List[Dict[str, Any]]:
            with contextlib.closing(self._connect()) as conn:
                if guild_ids is not None:
                    if not guild_ids:
                        return []
                    placeholders = ",".join("?" for _ in guild_ids)
                    cursor = conn.execute(
                        f"SELECT * FROM aff_punishments "
                        f"WHERE user_id = ? AND status = 'active' "
                        f"AND guild_id IN ({placeholders}) "
                        f"ORDER BY created_at ASC, rowid ASC",
                        (int(user_id), *[int(gid) for gid in guild_ids]),
                    )
                else:
                    cursor = conn.execute(
                        "SELECT * FROM aff_punishments "
                        "WHERE user_id = ? AND status = 'active' "
                        "ORDER BY created_at ASC, rowid ASC",
                        (int(user_id),),
                    )
                return [dict(r) for r in cursor.fetchall()]

        return await asyncio.to_thread(_query)

    async def get_all_records(
        self,
        user_id: int,
        guild_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """获取指定用户在统计范围内的全部记录（含已撤销，按时间倒序）。"""

        def _query() -> List[Dict[str, Any]]:
            with contextlib.closing(self._connect()) as conn:
                if guild_ids is not None:
                    if not guild_ids:
                        return []
                    placeholders = ",".join("?" for _ in guild_ids)
                    cursor = conn.execute(
                        f"SELECT * FROM aff_punishments "
                        f"WHERE user_id = ? AND guild_id IN ({placeholders}) "
                        f"ORDER BY created_at DESC, rowid DESC",
                        (int(user_id), *[int(gid) for gid in guild_ids]),
                    )
                else:
                    cursor = conn.execute(
                        "SELECT * FROM aff_punishments "
                        "WHERE user_id = ? "
                        "ORDER BY created_at DESC, rowid DESC",
                        (int(user_id),),
                    )
                return [dict(r) for r in cursor.fetchall()]

        return await asyncio.to_thread(_query)

    async def get_record(self, punish_id: str) -> Optional[Dict[str, Any]]:
        """按 punish_id 查询单条记录。"""

        def _query() -> Optional[Dict[str, Any]]:
            with contextlib.closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT * FROM aff_punishments WHERE punish_id = ?",
                    (str(punish_id).strip(),),
                ).fetchone()
                return self._row_to_dict(row)

        return await asyncio.to_thread(_query)

    async def add_record(
        self,
        *,
        punish_id: str,
        user_id: int,
        guild_id: int,
        offense_no: int,
        record_type: str,
        mute_seconds: int,
        warn_days: int,
        moderator_id: int,
        reason: Optional[str],
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """新增一条有效记录（punish 或 backfill）。"""
        ts = created_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
        pid = str(punish_id).strip()

        def _insert() -> Dict[str, Any]:
            with contextlib.closing(self._connect()) as conn:
                with conn:
                    conn.execute(
                        "INSERT INTO aff_punishments ("
                        "punish_id, user_id, guild_id, offense_no, record_type, "
                        "mute_seconds, warn_days, moderator_id, reason, created_at, status"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')",
                        (
                            pid,
                            int(user_id),
                            int(guild_id),
                            int(offense_no),
                            str(record_type),
                            int(mute_seconds),
                            int(warn_days),
                            int(moderator_id),
                            reason,
                            ts,
                        ),
                    )
                row = conn.execute(
                    "SELECT * FROM aff_punishments WHERE punish_id = ?",
                    (pid,),
                ).fetchone()
                return dict(row)

        return await asyncio.to_thread(_insert)

    async def revoke_record(
        self,
        punish_id: str,
        revoked_by: int,
        revoke_reason: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """将未撤销的记录标记为已撤销，返回更新后的记录；不存在或已撤销则返回 None。"""
        revoked_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        pid = str(punish_id).strip()

        def _update() -> Optional[Dict[str, Any]]:
            with contextlib.closing(self._connect()) as conn:
                with conn:
                    cursor = conn.execute(
                        "UPDATE aff_punishments "
                        "SET status = 'revoked', revoked_at = ?, revoked_by = ?, revoke_reason = ? "
                        "WHERE punish_id = ? AND status = 'active'",
                        (revoked_at, int(revoked_by), revoke_reason, pid),
                    )
                if cursor.rowcount <= 0:
                    return None
                row = conn.execute(
                    "SELECT * FROM aff_punishments WHERE punish_id = ?",
                    (pid,),
                ).fetchone()
                return self._row_to_dict(row)

        return await asyncio.to_thread(_update)
