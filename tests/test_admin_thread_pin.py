import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest


project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.admin.cog import AdminCommands


@pytest.fixture
def admin_cog(mock_bot):
    cog = AdminCommands(mock_bot)
    cog.get_guild_config = MagicMock(return_value=0)
    return cog


def make_thread(*, pinned: bool, forum_post: bool = True):
    thread = MagicMock(spec=discord.Thread)
    thread.flags = SimpleNamespace(pinned=pinned)
    thread.edit = AsyncMock()
    thread.mention = "<#1234567890>"
    if forum_post:
        thread.parent = MagicMock(spec=discord.ForumChannel)
    else:
        thread.parent = MagicMock(spec=discord.TextChannel)
    return thread


@pytest.mark.asyncio
async def test_pin_forum_thread_uses_thread_edit(admin_cog, mock_interaction):
    thread = make_thread(pinned=False, forum_post=True)

    await AdminCommands.pin_in_thread_admin.callback(admin_cog, mock_interaction, thread)

    thread.edit.assert_awaited_once()
    assert thread.edit.await_args.kwargs["pinned"] is True
    assert "管理员置顶 by" in thread.edit.await_args.kwargs["reason"]
    mock_interaction.followup.send.assert_awaited_once_with("✅ 已将帖子设为置顶帖", ephemeral=True)


@pytest.mark.asyncio
async def test_pin_forum_thread_rejects_non_forum_thread(admin_cog, mock_interaction):
    thread = make_thread(pinned=False, forum_post=False)

    await AdminCommands.pin_in_thread_admin.callback(admin_cog, mock_interaction, thread)

    thread.edit.assert_not_awaited()
    mock_interaction.followup.send.assert_awaited_once_with("❌ 仅论坛频道中的帖子支持设为置顶帖", ephemeral=True)


@pytest.mark.asyncio
async def test_unpin_forum_thread_uses_thread_edit(admin_cog, mock_interaction):
    thread = make_thread(pinned=True, forum_post=True)

    await AdminCommands.unpin_in_thread_admin.callback(admin_cog, mock_interaction, thread)

    thread.edit.assert_awaited_once()
    assert thread.edit.await_args.kwargs["pinned"] is False
    assert "管理员取消置顶 by" in thread.edit.await_args.kwargs["reason"]
    mock_interaction.followup.send.assert_awaited_once_with("✅ 已取消帖子置顶", ephemeral=True)
