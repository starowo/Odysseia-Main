"""
答题验证 SQLite 数据库层
替代原有的 JSON 文件存储方案，使用 aiosqlite 实现异步操作
"""

import json
import aiosqlite
import pathlib
import datetime
from typing import List, Dict, Optional

DB_PATH = pathlib.Path("data/verify/verify.db")

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS verify_users (
    guild_id  INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    last_success        TEXT,
    timeout_until       TEXT,
    quiz_cooldown_until TEXT,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS verify_attempts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id  INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    timestamp TEXT    NOT NULL,
    success   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attempts_lookup
    ON verify_attempts(guild_id, user_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS verify_question_cache (
    guild_id  INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    questions TEXT    NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS verify_guild_settings (
    guild_id             INTEGER PRIMARY KEY,
    auto_upgrade_enabled INTEGER NOT NULL DEFAULT 0
);
"""


class VerifyDatabase:
    def __init__(self, db_path: pathlib.Path = DB_PATH):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def init(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.executescript(_CREATE_TABLES_SQL)
        await self._db.commit()

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None

    # ── user data ──────────────────────────────────────────────

    async def get_user_data(self, guild_id: int, user_id: int) -> Dict:
        row = await self._db.execute_fetchall(
            "SELECT last_success, timeout_until, quiz_cooldown_until "
            "FROM verify_users WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        if row:
            r = row[0]
            last_success = r[0]
            timeout_until = r[1]
            quiz_cooldown_until = r[2]
        else:
            last_success = None
            timeout_until = None
            quiz_cooldown_until = None

        attempts = await self._fetch_attempts(guild_id, user_id)
        return {
            "attempts": attempts,
            "last_success": last_success,
            "timeout_until": timeout_until,
            "quiz_cooldown_until": quiz_cooldown_until,
        }

    async def _fetch_attempts(self, guild_id: int, user_id: int) -> List[Dict]:
        rows = await self._db.execute_fetchall(
            "SELECT timestamp, success FROM verify_attempts "
            "WHERE guild_id=? AND user_id=? ORDER BY timestamp ASC",
            (guild_id, user_id),
        )
        return [{"timestamp": r[0], "success": bool(r[1])} for r in rows]

    async def _ensure_user(self, guild_id: int, user_id: int):
        await self._db.execute(
            "INSERT OR IGNORE INTO verify_users(guild_id, user_id) VALUES(?,?)",
            (guild_id, user_id),
        )

    async def save_user_attempt(self, guild_id: int, user_id: int, success: bool) -> Dict:
        await self._ensure_user(guild_id, user_id)
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO verify_attempts(guild_id, user_id, timestamp, success) VALUES(?,?,?,?)",
            (guild_id, user_id, ts, int(success)),
        )
        if success:
            await self._db.execute(
                "UPDATE verify_users SET last_success=? WHERE guild_id=? AND user_id=?",
                (ts, guild_id, user_id),
            )
        await self._db.commit()
        return await self.get_user_data(guild_id, user_id)

    # ── timeout / cooldown ─────────────────────────────────────

    async def set_user_timeout(self, guild_id: int, user_id: int, minutes: int):
        await self._ensure_user(guild_id, user_id)
        until = (datetime.datetime.now(datetime.timezone.utc)
                 + datetime.timedelta(minutes=minutes)).isoformat()
        await self._db.execute(
            "UPDATE verify_users SET timeout_until=? WHERE guild_id=? AND user_id=?",
            (until, guild_id, user_id),
        )
        await self._db.commit()

    async def set_user_quiz_cooldown(self, guild_id: int, user_id: int, minutes: int):
        await self._ensure_user(guild_id, user_id)
        until = (datetime.datetime.now(datetime.timezone.utc)
                 + datetime.timedelta(minutes=minutes)).isoformat()
        await self._db.execute(
            "UPDATE verify_users SET quiz_cooldown_until=? WHERE guild_id=? AND user_id=?",
            (until, guild_id, user_id),
        )
        await self._db.commit()

    async def is_user_in_timeout(self, guild_id: int, user_id: int) -> bool:
        rows = await self._db.execute_fetchall(
            "SELECT timeout_until FROM verify_users WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        if not rows or not rows[0][0]:
            return False
        return datetime.datetime.now(datetime.timezone.utc) < datetime.datetime.fromisoformat(rows[0][0])

    async def is_user_in_quiz_cooldown(self, guild_id: int, user_id: int) -> bool:
        rows = await self._db.execute_fetchall(
            "SELECT quiz_cooldown_until FROM verify_users WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        if not rows or not rows[0][0]:
            return False
        return datetime.datetime.now(datetime.timezone.utc) < datetime.datetime.fromisoformat(rows[0][0])

    async def get_quiz_cooldown_remaining(self, guild_id: int, user_id: int) -> Optional[int]:
        rows = await self._db.execute_fetchall(
            "SELECT quiz_cooldown_until FROM verify_users WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        if not rows or not rows[0][0]:
            return None
        cooldown_time = datetime.datetime.fromisoformat(rows[0][0])
        now = datetime.datetime.now(datetime.timezone.utc)
        if now < cooldown_time:
            return int((cooldown_time - now).total_seconds() / 60) + 1
        return None

    async def get_recent_failed_attempts(
        self, guild_id: int, user_id: int, reset_hours: int = 24
    ) -> int:
        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(hours=reset_hours)).isoformat()
        rows = await self._db.execute_fetchall(
            "SELECT success FROM verify_attempts "
            "WHERE guild_id=? AND user_id=? AND timestamp>=? "
            "ORDER BY timestamp DESC",
            (guild_id, user_id, cutoff),
        )
        count = 0
        for r in rows:
            if r[0]:
                break
            count += 1
        return count

    # ── question cache ─────────────────────────────────────────

    async def get_user_questions(self, guild_id: int, user_id: int) -> Optional[List[Dict]]:
        rows = await self._db.execute_fetchall(
            "SELECT questions FROM verify_question_cache WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        if rows:
            return json.loads(rows[0][0])
        return None

    async def save_user_questions(self, guild_id: int, user_id: int, questions: List[Dict]):
        blob = json.dumps(questions, ensure_ascii=False)
        await self._db.execute(
            "INSERT OR REPLACE INTO verify_question_cache(guild_id, user_id, questions) VALUES(?,?,?)",
            (guild_id, user_id, blob),
        )
        await self._db.commit()

    async def clear_user_questions(self, guild_id: int, user_id: int):
        await self._db.execute(
            "DELETE FROM verify_question_cache WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        await self._db.commit()

    # ── guild settings ──────────────────────────────────────────

    async def get_auto_upgrade_enabled(self, guild_id: int) -> bool:
        rows = await self._db.execute_fetchall(
            "SELECT auto_upgrade_enabled FROM verify_guild_settings WHERE guild_id=?",
            (guild_id,),
        )
        if rows:
            return bool(rows[0][0])
        return True

    async def set_auto_upgrade_enabled(self, guild_id: int, enabled: bool):
        await self._db.execute(
            "INSERT OR REPLACE INTO verify_guild_settings(guild_id, auto_upgrade_enabled) VALUES(?,?)",
            (guild_id, int(enabled)),
        )
        await self._db.commit()

    async def get_all_auto_upgrade_settings(self) -> Dict[int, bool]:
        rows = await self._db.execute_fetchall(
            "SELECT guild_id, auto_upgrade_enabled FROM verify_guild_settings",
        )
        return {r[0]: bool(r[1]) for r in rows}

    # ── bulk import (供迁移脚本使用) ────────────────────────────

    async def bulk_import_user(
        self,
        guild_id: int,
        user_id: int,
        last_success: Optional[str],
        timeout_until: Optional[str],
        quiz_cooldown_until: Optional[str],
        attempts: List[Dict],
    ):
        """批量导入单个用户数据（在事务中调用，不单独 commit）"""
        await self._db.execute(
            "INSERT OR REPLACE INTO verify_users"
            "(guild_id, user_id, last_success, timeout_until, quiz_cooldown_until) "
            "VALUES(?,?,?,?,?)",
            (guild_id, user_id, last_success, timeout_until, quiz_cooldown_until),
        )
        if attempts:
            await self._db.executemany(
                "INSERT INTO verify_attempts(guild_id, user_id, timestamp, success) "
                "VALUES(?,?,?,?)",
                [(guild_id, user_id, a["timestamp"], int(a["success"])) for a in attempts],
            )

    async def bulk_commit(self):
        await self._db.commit()
