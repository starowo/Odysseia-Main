"""线程管理模块 SQLite 数据库层。

替换原有的多 JSON 文件持久化方案，解决大数据量下 JSON 读写失败导致数据丢失的问题。
启动时自动从旧 JSON 文件全量迁移，迁移完成后保留旧文件作为备份。
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import Optional

import aiosqlite

_DB_PATH = pathlib.Path("data") / "thread_manage.db"
_MIGRATED_MARKER = pathlib.Path("data") / ".migrated_to_db"

_db: Optional[aiosqlite.Connection] = None
_logger: Optional[logging.Logger] = None

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS thread_cache_meta (
    thread_id INTEGER PRIMARY KEY,
    last_message_id INTEGER
);

CREATE TABLE IF NOT EXISTS thread_cache_stats (
    thread_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    message_count INTEGER DEFAULT 0,
    last_active TEXT,
    PRIMARY KEY (thread_id, user_id)
);

CREATE TABLE IF NOT EXISTS auto_clear_disabled (
    thread_id INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS thread_delegates (
    guild_id INTEGER NOT NULL,
    thread_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, thread_id, user_id)
);

CREATE TABLE IF NOT EXISTS thread_mutes (
    guild_id INTEGER NOT NULL,
    thread_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    muted_until INTEGER,
    violations INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, thread_id, user_id)
);

CREATE TABLE IF NOT EXISTS forum_optout (
    user_id INTEGER PRIMARY KEY
);
"""


def set_logger(logger: Optional[logging.Logger]) -> None:
    global _logger
    _logger = logger


def _log(msg: str, level: str = "info") -> None:
    if _logger:
        getattr(_logger, level)(f"[DB] {msg}")


# ── 初始化 ──────────────────────────────────────────────────

async def init_db() -> None:
    """初始化数据库连接并建表。幂等，可多次调用。"""
    global _db
    if _db is not None:
        return

    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(str(_DB_PATH))
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA synchronous=NORMAL")
    await _db.executescript(_CREATE_TABLES_SQL)
    await _db.commit()
    _log("数据库表已就绪")


async def close_db() -> None:
    """关闭数据库连接。"""
    global _db
    if _db:
        await _db.close()
        _db = None


# ── 迁移 ────────────────────────────────────────────────────

async def _migrate_thread_cache() -> int:
    cache_dir = pathlib.Path("data/thread_cache")
    if not cache_dir.is_dir():
        return 0
    total = 0
    for p in cache_dir.glob("*.json"):
        try:
            thread_id = int(p.stem)
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            last_id = data.get("last_id")
            if last_id is not None:
                await _db.execute(
                    "INSERT OR REPLACE INTO thread_cache_meta (thread_id, last_message_id) VALUES (?, ?)",
                    (thread_id, last_id),
                )
            for uid_str, count in data.get("message_counts", {}).items():
                uid = int(uid_str)
                last_active = data.get("last_active", {}).get(uid_str)
                await _db.execute(
                    "INSERT OR REPLACE INTO thread_cache_stats "
                    "(thread_id, user_id, message_count, last_active) VALUES (?, ?, ?, ?)",
                    (thread_id, uid, count, last_active),
                )
                total += 1
        except Exception as e:
            _log(f"迁移线程缓存 {p.name} 失败: {e}", "warning")
    return total


async def _migrate_auto_clear_disabled() -> int:
    path = pathlib.Path("data/auto_clear_disabled.json")
    if not path.is_file():
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for tid in data.get("disabled_threads", []):
            await _db.execute("INSERT OR IGNORE INTO auto_clear_disabled (thread_id) VALUES (?)", (tid,))
        return len(data.get("disabled_threads", []))
    except Exception as e:
        _log(f"迁移自动清理禁用列表失败: {e}", "warning")
        return 0


async def _migrate_thread_delegates() -> int:
    delegates_dir = pathlib.Path("data/thread_delegates")
    if not delegates_dir.is_dir():
        return 0
    total = 0
    for guild_dir in delegates_dir.iterdir():
        if not guild_dir.is_dir():
            continue
        try:
            guild_id = int(guild_dir.name)
        except ValueError:
            continue
        for p in guild_dir.glob("*.json"):
            try:
                thread_id = int(p.stem)
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for uid in data.get("delegates", []):
                    await _db.execute(
                        "INSERT OR IGNORE INTO thread_delegates (guild_id, thread_id, user_id) VALUES (?, ?, ?)",
                        (guild_id, thread_id, int(uid)),
                    )
                    total += 1
            except Exception as e:
                _log(f"迁移协管 {p} 失败: {e}", "warning")
    return total


async def _migrate_thread_mutes() -> int:
    mute_dir = pathlib.Path("data/thread_mute")
    if not mute_dir.is_dir():
        return 0
    total = 0
    for guild_dir in mute_dir.iterdir():
        if not guild_dir.is_dir():
            continue
        try:
            guild_id = int(guild_dir.name)
        except ValueError:
            continue
        for thread_dir in guild_dir.iterdir():
            if not thread_dir.is_dir():
                continue
            try:
                thread_id = int(thread_dir.name)
            except ValueError:
                continue
            for p in thread_dir.glob("*.json"):
                try:
                    user_id = int(p.stem)
                    with open(p, "r", encoding="utf-8") as f:
                        record = json.load(f)
                    muted_until = record.get("muted_until")
                    violations = record.get("violations", 0)
                    await _db.execute(
                        "INSERT OR REPLACE INTO thread_mutes "
                        "(guild_id, thread_id, user_id, muted_until, violations) VALUES (?, ?, ?, ?, ?)",
                        (guild_id, thread_id, user_id, muted_until, violations),
                    )
                    total += 1
                except Exception as e:
                    _log(f"迁移禁言记录 {p} 失败: {e}", "warning")
    return total


async def _migrate_forum_optout() -> int:
    optout_dir = pathlib.Path("data/forum_selfmanage_welcome_optout")
    if not optout_dir.is_dir():
        return 0

    users: set[int] = set()
    global_path = optout_dir / "opted_out_users.json"
    marker = optout_dir / ".legacy_guild_optout_merged"

    if global_path.is_file():
        try:
            with open(global_path, "r", encoding="utf-8") as f:
                users = {int(x) for x in json.load(f).get("users", [])}
        except Exception:
            pass

    if not marker.exists():
        for p in optout_dir.iterdir():
            if not p.is_file() or p.suffix != ".json":
                continue
            if p.name == "opted_out_users.json" or not p.stem.isdigit():
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    users |= {int(x) for x in json.load(f).get("users", [])}
            except Exception:
                pass
        try:
            marker.touch()
        except Exception:
            pass

    for uid in users:
        await _db.execute("INSERT OR IGNORE INTO forum_optout (user_id) VALUES (?)", (uid,))
    return len(users)


async def migrate_from_json() -> bool:
    """从旧 JSON 文件全量迁移到 SQLite。

    仅在标记文件 data/.migrated_to_db 不存在时执行。
    迁移完成后创建标记文件，旧 JSON 保留不删。
    返回 True 表示执行了迁移，False 表示已迁移过。
    """
    if _MIGRATED_MARKER.exists():
        _log("已迁移过，跳过")
        return False

    _log("开始从 JSON 迁移数据...")

    counts = {
        "thread_cache": await _migrate_thread_cache(),
        "auto_clear_disabled": await _migrate_auto_clear_disabled(),
        "thread_delegates": await _migrate_thread_delegates(),
        "thread_mutes": await _migrate_thread_mutes(),
        "forum_optout": await _migrate_forum_optout(),
    }
    await _db.commit()

    try:
        _MIGRATED_MARKER.touch()
    except Exception as e:
        _log(f"创建迁移标记文件失败: {e}", "error")
        return True

    _log(
        f"迁移完成 — 缓存用户: {counts['thread_cache']}, "
        f"禁用线程: {counts['auto_clear_disabled']}, "
        f"协管: {counts['thread_delegates']}, "
        f"禁言: {counts['thread_mutes']}, "
        f"Opt-out: {counts['forum_optout']}"
    )
    return True


# ── 线程缓存操作 ────────────────────────────────────────────

async def load_thread_cache(thread_id: int) -> tuple[Optional[int], dict[int, int], dict[int, str]]:
    """读取线程消息缓存。返回 (last_id, message_counts, last_active)。"""
    rows = await _db.execute_fetchall(
        "SELECT last_message_id FROM thread_cache_meta WHERE thread_id = ?",
        (thread_id,),
    )
    last_id = rows[0][0] if rows else None

    message_counts: dict[int, int] = {}
    last_active: dict[int, str] = {}
    rows = await _db.execute_fetchall(
        "SELECT user_id, message_count, last_active FROM thread_cache_stats WHERE thread_id = ?",
        (thread_id,),
    )
    for row in rows:
        message_counts[row[0]] = row[1]
        if row[2]:
            last_active[row[0]] = row[2]

    return last_id, message_counts, last_active


async def save_thread_cache(thread_id: int, last_id: Optional[int],
                            message_counts: dict[int, int], last_active: dict[int, str]) -> None:
    """保存线程消息缓存。使用批量写入以提高性能。"""
    if last_id is not None:
        await _db.execute(
            "INSERT OR REPLACE INTO thread_cache_meta (thread_id, last_message_id) VALUES (?, ?)",
            (thread_id, last_id),
        )
    rows = [(thread_id, uid, count, last_active.get(uid)) for uid, count in message_counts.items()]
    await _db.executemany(
        "INSERT OR REPLACE INTO thread_cache_stats (thread_id, user_id, message_count, last_active) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )
    await _db.commit()


# ── 自动清理禁用列表操作 ────────────────────────────────────

async def load_disabled_threads() -> set[int]:
    rows = await _db.execute_fetchall("SELECT thread_id FROM auto_clear_disabled")
    return {row[0] for row in rows}


async def save_disabled_threads(thread_ids: set[int]) -> None:
    await _db.execute("DELETE FROM auto_clear_disabled")
    await _db.executemany(
        "INSERT INTO auto_clear_disabled (thread_id) VALUES (?)",
        [(tid,) for tid in thread_ids],
    )
    await _db.commit()


# ── 子区协管操作 ────────────────────────────────────────────

async def load_thread_delegates(guild_id: int, thread_id: int) -> set[int]:
    rows = await _db.execute_fetchall(
        "SELECT user_id FROM thread_delegates WHERE guild_id = ? AND thread_id = ?",
        (guild_id, thread_id),
    )
    return {row[0] for row in rows}


async def save_thread_delegates(guild_id: int, thread_id: int, delegates: set[int]) -> None:
    await _db.execute(
        "DELETE FROM thread_delegates WHERE guild_id = ? AND thread_id = ?",
        (guild_id, thread_id),
    )
    if delegates:
        await _db.executemany(
            "INSERT INTO thread_delegates (guild_id, thread_id, user_id) VALUES (?, ?, ?)",
            [(guild_id, thread_id, uid) for uid in delegates],
        )
    await _db.commit()


# ── 子区禁言操作 ────────────────────────────────────────────

async def load_all_mutes() -> dict[tuple[int, int, int], dict]:
    """加载全部禁言记录。key = (guild_id, thread_id, user_id)。"""
    records: dict[tuple[int, int, int], dict] = {}
    rows = await _db.execute_fetchall("SELECT * FROM thread_mutes")
    for row in rows:
        key = (row["guild_id"], row["thread_id"], row["user_id"])
        records[key] = {"muted_until": row["muted_until"], "violations": row["violations"]}
    return records


async def save_mute_record(guild_id: int, thread_id: int, user_id: int,
                           record: Optional[dict]) -> None:
    """保存或删除禁言记录。record 为 None 时删除。"""
    if not record:
        await _db.execute(
            "DELETE FROM thread_mutes WHERE guild_id = ? AND thread_id = ? AND user_id = ?",
            (guild_id, thread_id, user_id),
        )
    else:
        await _db.execute(
            "INSERT OR REPLACE INTO thread_mutes "
            "(guild_id, thread_id, user_id, muted_until, violations) VALUES (?, ?, ?, ?, ?)",
            (guild_id, thread_id, user_id, record.get("muted_until"), record.get("violations", 0)),
        )
    await _db.commit()


# ── 论坛 Opt-out 操作 ───────────────────────────────────────

async def load_forum_optout() -> set[int]:
    rows = await _db.execute_fetchall("SELECT user_id FROM forum_optout")
    return {row[0] for row in rows}


async def save_forum_optout(users: set[int]) -> None:
    await _db.execute("DELETE FROM forum_optout")
    await _db.executemany(
        "INSERT INTO forum_optout (user_id) VALUES (?)",
        [(uid,) for uid in users],
    )
    await _db.commit()
