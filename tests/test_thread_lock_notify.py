import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import discord


class MockGuild:
    def __init__(self, id=123456789, name="Test Guild"):
        self.id = id
        self.name = name
        self.icon = None

    def get_member(self, user_id):
        if user_id == 111:
            member = MagicMock()
            member.id = 111
            member.bot = False
            return member
        if user_id == 222:
            member = MagicMock()
            member.id = 222
            member.bot = True
            return member
        return None


def make_thread(locked, owner_id, parent_forum=True):
    thread = MagicMock(spec=discord.Thread)
    thread.locked = locked
    thread.owner_id = owner_id
    thread.name = "Test Thread"
    thread.id = 999888777
    thread.mention = "<#999888777>"
    thread.guild = MockGuild()
    thread.parent = MagicMock(spec=discord.ForumChannel) if parent_forum else MagicMock()
    thread.created_at = MagicMock()
    return thread


class TestThreadLockNotify:
    def setup_method(self):
        bot = MagicMock()
        bot.logger = MagicMock()
        self.cog = _make_cog(bot)

    def test_locked_notifies_owner(self):
        before = make_thread(locked=False, owner_id=111)
        after = make_thread(locked=True, owner_id=111)

        with patch("src.thread_manage.cog.dm.send_dm", new_callable=AsyncMock) as mock_send:
            import asyncio
            asyncio.run(self.cog.on_thread_update(before, after))
            mock_send.assert_called_once()

            call_args = mock_send.call_args
            assert call_args[0][0] == after.guild
            embed = call_args[1]["embed"]
            assert "已被锁定" in embed.title
            assert "Test Thread" in embed.description
            assert str(after.id) in embed.description

    def test_already_locked_skips(self):
        before = make_thread(locked=True, owner_id=111)
        after = make_thread(locked=True, owner_id=111)

        with patch("src.thread_manage.cog.dm.send_dm", new_callable=AsyncMock) as mock_send:
            import asyncio
            asyncio.run(self.cog.on_thread_update(before, after))
            mock_send.assert_not_called()

    def test_unlocked_skips(self):
        before = make_thread(locked=False, owner_id=111)
        after = make_thread(locked=False, owner_id=111)

        with patch("src.thread_manage.cog.dm.send_dm", new_callable=AsyncMock) as mock_send:
            import asyncio
            asyncio.run(self.cog.on_thread_update(before, after))
            mock_send.assert_not_called()

    def test_no_owner_skips(self):
        before = make_thread(locked=False, owner_id=None)
        after = make_thread(locked=True, owner_id=None)

        with patch("src.thread_manage.cog.dm.send_dm", new_callable=AsyncMock) as mock_send:
            import asyncio
            asyncio.run(self.cog.on_thread_update(before, after))
            mock_send.assert_not_called()

    def test_non_forum_skips(self):
        before = make_thread(locked=False, owner_id=111, parent_forum=False)
        after = make_thread(locked=True, owner_id=111, parent_forum=False)

        with patch("src.thread_manage.cog.dm.send_dm", new_callable=AsyncMock) as mock_send:
            import asyncio
            asyncio.run(self.cog.on_thread_update(before, after))
            mock_send.assert_not_called()

    def test_bot_owner_skips(self):
        before = make_thread(locked=False, owner_id=222)
        after = make_thread(locked=True, owner_id=222)

        with patch("src.thread_manage.cog.dm.send_dm", new_callable=AsyncMock) as mock_send:
            import asyncio
            asyncio.run(self.cog.on_thread_update(before, after))
            mock_send.assert_not_called()

    def test_owner_not_in_guild_skips(self):
        before = make_thread(locked=False, owner_id=999)
        after = make_thread(locked=True, owner_id=999)

        with patch("src.thread_manage.cog.dm.send_dm", new_callable=AsyncMock) as mock_send:
            import asyncio
            asyncio.run(self.cog.on_thread_update(before, after))
            mock_send.assert_not_called()

    def test_dm_failure_does_not_raise(self):
        before = make_thread(locked=False, owner_id=111)
        after = make_thread(locked=True, owner_id=111)

        with patch("src.thread_manage.cog.dm.send_dm", side_effect=Exception("DM failed")) as mock_send:
            import asyncio
            asyncio.run(self.cog.on_thread_update(before, after))
            mock_send.assert_called_once()


def _make_cog(bot):
    from src.thread_manage.cog import ThreadSelfManage
    cog = ThreadSelfManage.__new__(ThreadSelfManage)
    cog.bot = bot
    cog.logger = bot.logger
    cog._mute_cache = {}
    cog._config_cache = {}
    cog._config_cache_mtime = None
    cog.auto_clear_manager = MagicMock()
    return cog