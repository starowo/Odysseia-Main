"""频道名校验和成功记录；不依赖 Discord 的业务逻辑。"""

import math
import time
import unicodedata
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

import aiosqlite

WINDOW_SECONDS = 600
MAX_RENAMES = 2
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "channel_rename.db"


@lru_cache(maxsize=1)
def _emoji_sequences() -> frozenset[str]:
    # Unicode 17.0 fully/minimally-qualified sequences only. Text-presentation
    # symbols (e.g. bare ©), lone components and invented ZWJ chains are excluded.
    path = Path(__file__).with_name("emoji_sequences.txt")
    return frozenset(
        "".join(chr(int(point, 16)) for point in line.split())
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )


def is_single_unicode_emoji(value: str) -> bool:
    """精确匹配一个 Unicode Emoji 序列，不修剪或转换用户输入。"""
    return value in _emoji_sequences()


def build_channel_name(
    new_name: str, emoji: Optional[str] = None, *, lowercase: bool = False
) -> str:
    """添加前缀及默认分隔符，可按频道规则转小写，再检查最终长度。"""
    if not new_name or not new_name.strip():
        raise ValueError("❌ 新名称不能为空。")
    if any(unicodedata.category(char) in {"Cc", "Cs"} for char in new_name):
        raise ValueError("❌ 新名称不能包含控制字符或无效 Unicode 字符。")
    if emoji is not None and not is_single_unicode_emoji(emoji):
        raise ValueError(
            "❌ emoji 只能填写 1 个真正的 Unicode Emoji，不能使用文字、普通符号或 Discord 自定义 Emoji。"
        )
    if lowercase:
        # Only normalize the name, preserving the independently validated Emoji.
        new_name = new_name.lower()
    # Keep existing separators from earlier command usage, without doubling
    # them. New inputs need only the name; the default separator is U+4E28.
    separator = "丨" if emoji and not new_name.startswith(("丨", "｜", "・")) else ""
    final_name = (emoji or "") + separator + new_name
    if len(final_name) > 100:
        raise ValueError("❌ 最终频道名称（包含 Emoji 和分隔符）不能超过 100 个字符。")
    return final_name


@dataclass(frozen=True)
class RenameWindow:
    count: int
    retry_after: int


class ChannelRenameStore:
    """短连接 SQLite 存储；频道锁由调用者持有，不跨 Discord 请求占用 DB 锁。"""

    def __init__(
        self, db_path: Path = DEFAULT_DB_PATH, *, clock: Callable[[], float] = time.time
    ):
        self.db_path = Path(db_path)
        self.clock = clock

    @asynccontextmanager
    async def _connect(self):
        # Lazy initialization: constructing an eagerly mapped but disabled Cog
        # must not touch disk or cause other Cogs to fail at startup.
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS channel_rename_events ("
                "id TEXT PRIMARY KEY, "
                "guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, "
                "renamed_at REAL NOT NULL)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_channel_rename_time "
                "ON channel_rename_events(channel_id, renamed_at)"
            )
            await db.commit()
            yield db

    async def get_window(self, guild_id: int, channel_id: int) -> RenameWindow:
        async with self._connect() as db:
            now = self.clock()
            cutoff = now - WINDOW_SECONDS
            await db.execute(
                "DELETE FROM channel_rename_events "
                "WHERE guild_id = ? AND channel_id = ? AND renamed_at <= ?",
                (guild_id, channel_id, cutoff),
            )
            async with db.execute(
                "SELECT COUNT(*), MIN(renamed_at) FROM channel_rename_events "
                "WHERE guild_id = ? AND channel_id = ? AND renamed_at > ?",
                (guild_id, channel_id, cutoff),
            ) as cursor:
                count, oldest = await cursor.fetchone()
            await db.commit()
        retry_after = (
            max(0, math.ceil(oldest + WINDOW_SECONDS - now))
            if count >= MAX_RENAMES
            else 0
        )
        return RenameWindow(count=count, retry_after=retry_after)

    async def record_success(
        self,
        guild_id: int,
        channel_id: int,
        renamed_at: float,
        *,
        event_id: Optional[str] = None,
    ):
        """仅在 API 确认改变名称后调用；重试同一成功记录不会重复插入。"""
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO channel_rename_events(id, guild_id, channel_id, renamed_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
                (
                    event_id if event_id is not None else uuid.uuid4().hex,
                    guild_id,
                    channel_id,
                    renamed_at,
                ),
            )
            await db.commit()
