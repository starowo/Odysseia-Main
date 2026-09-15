"""Isolated rename tests: temporary SQLite and mocked Discord, never a live API."""

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import Intents as RealIntents
from discord.ext.commands import Bot as RealBot

from src.channel_rename.core import (
    ChannelRenameStore,
    build_channel_name,
    is_single_unicode_emoji,
)
from src.channel_rename.cog import ChannelRenameCommands


@pytest.mark.parametrize(
    "emoji",
    [
        "😭",
        "🌙",
        "❤️",
        "👍🏽",
        "🇨🇳",
        "🇺🇸",
        "👩‍💻",
        "👨‍👩‍👧‍👦",
        "🏳️‍🌈",
        "👩🏽‍🚀",
        "❤️‍🔥",
        "1️⃣",
        "©️",
        "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",
    ],
)
def test_single_emoji(emoji):
    assert is_single_unicode_emoji(emoji)


@pytest.mark.parametrize(
    "emoji",
    [
        "",
        "ABC",
        "聊天",
        "😭🔥",
        "❤️🌙",
        ":xxx:",
        "<:xxx:123456789>",
        "<a:xxx:123456789>",
        "+",
        "☆",
        "©",
        "™",
        "1",
        "#",
        "🏽",
        "🇨",
        "🇦🇦",
        "😭‍🔥",
        "😀🏽",
        "😭文字",
        " 😭",
        "😭 ",
        "\ufe0f",
        "❤︎",
    ],
)
def test_invalid_emoji(emoji):
    assert not is_single_unicode_emoji(emoji)


@pytest.mark.parametrize(
    "name,emoji,expected",
    [
        ("聊天区", None, "聊天区"),
        ("聊天区", "🌙", "🌙丨聊天区"),
        ("噢耶，改改的名", "😭", "😭丨噢耶，改改的名"),
        ("丨聊天区", "🌙", "🌙丨聊天区"),
        ("｜聊天区", "🌙", "🌙｜聊天区"),
        ("・聊天区", "🌙", "🌙・聊天区"),
        ("｜噢耶，改改的名", "😭", "😭｜噢耶，改改的名"),
        ("AET", None, "AET"),
        ("AET", "🌙", "🌙丨AET"),
        ("AeT", "🌙", "🌙丨AeT"),
        ("  聊天区  ", None, "  聊天区  "),
        ("x" * 98, "😭", "😭丨" + "x" * 98),
        ("x" * 97, "❤️", "❤️丨" + "x" * 97),
        ("丨" + "x" * 98, "😭", "😭丨" + "x" * 98),
    ],
)
def test_name_is_preserved(name, emoji, expected):
    assert build_channel_name(name, emoji) == expected


@pytest.mark.parametrize(
    "name,emoji",
    [
        ("", None),
        (" \t\n", "😭"),
        ("x" * 101, None),
        ("x" * 100, "😭"),
        ("x" * 99, "😭"),
        ("x" * 98, "❤️"),
        ("x" * 99, "❤️"),
        ("聊天", "ABC"),
        ("聊天", ""),
        ("聊\x00天", None),
    ],
)
def test_invalid_names(name, emoji):
    with pytest.raises(ValueError):
        build_channel_name(name, emoji)


@pytest.mark.asyncio
async def test_discord_normalization_is_explained(cog, store):
    channel = make_channel()
    channel.edit.side_effect = None
    channel.edit.return_value = SimpleNamespace(id=10, name="🌙丨chat-room")
    interaction = make_interaction(channel)
    await invoke(cog, interaction, "chat room", "🌙")
    assert channel.edit.call_args.kwargs["name"] == "🌙丨chat room"
    assert (await store.get_window(1, 10)).count == 1
    assert "Discord" in reply_text(interaction)
    assert "规范化" in reply_text(interaction)
    assert "🌙丨chat-room" in reply_text(interaction)
    assert_private(interaction)
    fields = {
        field.name: field.value
        for field in channel.send.call_args.kwargs["embed"].fields
    }
    assert fields["✏️ 新名称"] == "🌙丨chat-room"


@pytest.mark.asyncio
async def test_request_normalized_to_current_name_does_not_consume(cog, store):
    channel = make_channel(name="🌙丨chat-room")
    channel.edit.side_effect = None
    channel.edit.return_value = SimpleNamespace(id=10, name=channel.name)
    interaction = make_interaction(channel)
    await invoke(cog, interaction, "chat room", "🌙")
    assert channel.edit.call_args.kwargs["name"] == "🌙丨chat room"
    assert (await store.get_window(1, 10)).count == 0
    assert "Discord" in reply_text(interaction)
    assert "规范化" in reply_text(interaction)
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["AET", "AeT", "测试A区", "École"])
async def test_text_channel_converts_uppercase_before_api(cog, store, name):
    channel = make_channel()
    interaction = make_interaction(channel)
    await invoke(cog, interaction, name, "🌙")
    expected = "🌙丨" + name.lower()
    assert channel.edit.call_args.kwargs["name"] == expected
    assert (await store.get_window(1, 10)).count == 1
    fields = {
        field.name: field.value
        for field in channel.send.call_args.kwargs["embed"].fields
    }
    assert fields["✏️ 新名称"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("emoji,current_name", [(None, "aet"), ("🌙", "🌙丨aet")])
async def test_uppercase_matching_existing_lowercase_does_not_call_api(
    cog, store, emoji, current_name
):
    channel = make_channel(name=current_name)
    interaction = make_interaction(channel)
    await invoke(cog, interaction, "AET", emoji)
    channel.edit.assert_not_awaited()
    interaction.guild.fetch_channel.assert_not_awaited()
    assert (await store.get_window(1, 10)).count == 0
    assert "完全相同" in reply_text(interaction)
    assert_private(interaction)


@pytest.mark.parametrize(
    "name,emoji,expected",
    [
        ("AET", None, "aet"),
        ("AeT", "🌙", "🌙丨aet"),
        ("AET", "Ⓜ️", "Ⓜ️丨aet"),
        ("İ" * 50, None, "i\u0307" * 50),
    ],
)
def test_name_lowercasing_preserves_emoji_and_checks_final_length(
    name, emoji, expected
):
    assert build_channel_name(name, emoji, lowercase=True) == expected


def test_lowercasing_length_expansion_is_rejected():
    with pytest.raises(ValueError, match="100"):
        build_channel_name("İ" + "a" * 99, lowercase=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel_class", [discord.VoiceChannel, discord.Thread])
@pytest.mark.parametrize("emoji,expected", [(None, "AeT"), ("🌙", "🌙丨AeT")])
async def test_case_supporting_channels_submit_original_characters(
    cog, store, channel_class, emoji, expected
):
    channel = make_channel(channel_class=channel_class)
    interaction = make_interaction(channel)
    await invoke(cog, interaction, "AeT", emoji)
    assert channel.edit.call_args.kwargs["name"] == expected
    assert (await store.get_window(1, 10)).count == 1
    fields = {
        field.name: field.value
        for field in channel.send.call_args.kwargs["embed"].fields
    }
    assert fields["✏️ 新名称"] == expected


@pytest.mark.asyncio
async def test_same_automatically_prefixed_name_does_not_call_api(cog, store):
    channel = make_channel(name="🌙丨聊天区")
    interaction = make_interaction(channel)
    await invoke(cog, interaction, "聊天区", "🌙")
    channel.edit.assert_not_awaited()
    interaction.guild.fetch_channel.assert_not_awaited()
    assert (await store.get_window(1, 10)).count == 0


@pytest.fixture
def clock():
    return SimpleNamespace(now=1000.0)


@pytest.fixture
def store(tmp_path, clock):
    return ChannelRenameStore(tmp_path / "channel_rename.db", clock=lambda: clock.now)


@pytest.mark.asyncio
async def test_rolling_window_and_channel_isolation(store, clock):
    assert (await store.get_window(1, 10)).count == 0
    await store.record_success(1, 10, clock.now)
    assert (await store.get_window(1, 10)).count == 1
    clock.now = 1300
    await store.record_success(1, 10, clock.now)
    window = await store.get_window(1, 10)
    assert window.count == 2
    assert window.retry_after == 300
    assert (await store.get_window(1, 11)).count == 0
    assert (await store.get_window(2, 10)).count == 0
    clock.now = 1599.2
    assert (await store.get_window(1, 10)).retry_after == 1
    clock.now = 1600
    assert (await store.get_window(1, 10)).count == 1
    assert (await store.get_window(1, 10)).retry_after == 0
    clock.now = 1900
    assert (await store.get_window(1, 10)).count == 0


@pytest.mark.asyncio
async def test_restart_and_expired_rows(store, clock):
    await store.record_success(1, 10, 400)
    await store.record_success(1, 10, 900)
    await store.record_success(1, 10, 950)
    restarted = ChannelRenameStore(store.db_path, clock=lambda: clock.now)
    window = await restarted.get_window(1, 10)
    assert window.count == 2
    assert window.retry_after == 500
    with sqlite3.connect(store.db_path) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM channel_rename_events").fetchone()[0] == 2
        )
        indexes = db.execute("PRAGMA index_list(channel_rename_events)").fetchall()
        assert any(
            [row[2] for row in db.execute(f'PRAGMA index_info("{index[1]}")')]
            == ["channel_id", "renamed_at"]
            for index in indexes
        )


@pytest.mark.asyncio
async def test_equal_timestamps_are_distinct_but_retries_are_idempotent(store):
    await store.record_success(1, 10, 1000, event_id="first")
    await store.record_success(1, 10, 1000, event_id="first")
    await store.record_success(1, 10, 1000, event_id="second")
    assert (await store.get_window(1, 10)).count == 2


@pytest.fixture
def bot():
    return SimpleNamespace(logger=MagicMock())


@pytest.fixture
def cog(bot, store):
    return ChannelRenameCommands(bot, store=store)


def make_channel(channel_id=10, name="旧名称", *, channel_class=discord.TextChannel):
    channel = MagicMock(spec=channel_class)
    channel.id = channel_id
    channel.name = name
    channel.send = AsyncMock()

    async def edit(*, name, reason):
        await asyncio.sleep(0)
        # discord.py returns a new object; it does not update the old cache object.
        return SimpleNamespace(id=channel.id, name=name)

    channel.edit = AsyncMock(side_effect=edit)
    return channel


def make_interaction(channel, user_id=20):
    guild = SimpleNamespace(id=1, fetch_channel=AsyncMock(return_value=channel))
    return SimpleNamespace(
        guild=guild,
        channel=channel,
        user=SimpleNamespace(id=user_id, mention=f"<@{user_id}>"),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


async def invoke(cog, interaction, name="新名称", emoji=None):
    await cog.rename_channel.callback(cog, interaction, name, emoji)


def replies(interaction):
    return (
        interaction.response.send_message.call_args_list
        + interaction.followup.send.call_args_list
    )


def reply_text(interaction):
    return "\n".join(str(call.args[0]) for call in replies(interaction))


def assert_private(interaction):
    assert replies(interaction)
    assert all(call.kwargs.get("ephemeral") is True for call in replies(interaction))


@pytest.mark.asyncio
async def test_channel_shares_two_successes_across_members(cog, store, clock):
    channel = make_channel()
    for user_id in (20, 21):
        interaction = make_interaction(channel, user_id)
        await invoke(cog, interaction, f"频道{user_id}")
        assert (await store.get_window(1, 10)).count == user_id - 19
    clock.now = 1323
    blocked = make_interaction(channel, 22)
    await invoke(cog, blocked, "第三次")
    assert channel.edit.await_count == 2
    assert "4分37秒" in reply_text(blocked)
    assert "2 次" in reply_text(blocked)
    assert_private(blocked)
    other = make_channel(11)
    await invoke(cog, make_interaction(other))
    assert other.edit.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,emoji", [("聊天", "😭🔥"), ("", None), ("x" * 100, "😭"), ("旧名称", None)]
)
async def test_invalid_or_same_name_does_not_call_api(cog, store, name, emoji):
    channel = make_channel()
    interaction = make_interaction(channel)
    await invoke(cog, interaction, name, emoji)
    channel.edit.assert_not_awaited()
    interaction.guild.fetch_channel.assert_not_awaited()
    assert (await store.get_window(1, 10)).count == 0
    assert_private(interaction)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type,expected",
    [
        (discord.Forbidden, "管理频道"),
        (discord.HTTPException, "Discord API"),
        (RuntimeError, "失败"),
    ],
)
async def test_api_failure_does_not_consume(cog, store, error_type, expected):
    channel = make_channel()
    response = SimpleNamespace(status=403, reason="Forbidden", headers={})
    error = (
        RuntimeError("unexpected")
        if error_type is RuntimeError
        else error_type(response, "failure")
    )
    channel.edit.side_effect = error
    interaction = make_interaction(channel)
    await invoke(cog, interaction)
    assert (await store.get_window(1, 10)).count == 0
    assert expected in reply_text(interaction)
    assert_private(interaction)
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, SimpleNamespace(id=10, name="旧名称")])
async def test_no_confirmed_change_does_not_consume(cog, store, result):
    channel = make_channel()
    channel.edit.side_effect = None
    channel.edit.return_value = result
    await invoke(cog, make_interaction(channel))
    assert (await store.get_window(1, 10)).count == 0
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("emoji", [None, "😭"])
async def test_public_success_embed_uses_actual_api_name(cog, store, clock, emoji):
    channel = make_channel()

    async def edit(**kwargs):
        assert (await store.get_window(1, 10)).count == 0
        clock.now += 20
        return SimpleNamespace(id=10, name="实际名称")

    async def send(**kwargs):
        assert (await store.get_window(1, 10)).count == 1

    channel.edit.side_effect = edit
    channel.send.side_effect = send
    interaction = make_interaction(channel)
    await invoke(cog, interaction, "｜聊天区", emoji)
    assert channel.edit.call_args.kwargs["name"] == (emoji or "") + "｜聊天区"
    embed = channel.send.call_args.kwargs["embed"]
    assert embed.title == "✅ 频道名称已修改"
    assert embed.colour == discord.Colour.green()
    fields = {field.name: field.value for field in embed.fields}
    assert fields["📋 原名称"] == "旧名称"
    assert fields["✏️ 新名称"] == "实际名称"
    assert fields["👤 操作者"] == "<@20>"
    assert fields["😀 自定义 Emoji"] == (emoji or "未设置")
    assert "Discord API" in fields["⚠️ 注意事项"]
    assert "频道修改时间" in embed.footer.text
    assert embed.timestamp.timestamp() == 1020
    with sqlite3.connect(store.db_path) as db:
        assert (
            db.execute("SELECT renamed_at FROM channel_rename_events").fetchone()[0]
            == 1020
        )


@pytest.mark.asyncio
async def test_panel_failure_is_still_success(cog, store):
    channel = make_channel()
    channel.send.side_effect = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "no send"
    )
    interaction = make_interaction(channel)
    await invoke(cog, interaction)
    assert (await store.get_window(1, 10)).count == 1
    assert "频道名称已修改，但机器人没能发送公开的改名面板" in reply_text(interaction)
    assert_private(interaction)


@pytest.mark.asyncio
async def test_concurrent_calls_cannot_take_third_slot(cog, store):
    await store.record_success(1, 10, 1000)
    channel = make_channel()
    interactions = [make_interaction(channel, n) for n in range(30)]
    await asyncio.gather(
        *(
            invoke(cog, interaction, f"新名字{n}")
            for n, interaction in enumerate(interactions)
        )
    )
    assert channel.edit.await_count == 1
    assert (await store.get_window(1, 10)).count == 2


@pytest.mark.asyncio
async def test_slow_channel_does_not_block_another(cog):
    entered, release = asyncio.Event(), asyncio.Event()
    slow, fast = make_channel(10), make_channel(11)

    async def slow_edit(**kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(id=10, name=kwargs["name"])

    slow.edit.side_effect = slow_edit
    task = asyncio.create_task(invoke(cog, make_interaction(slow)))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await asyncio.wait_for(invoke(cog, make_interaction(fast)), 3)
        assert fast.edit.await_count == 1
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_database_read_failure_prevents_edit_and_logs(cog, store, monkeypatch):
    monkeypatch.setattr(
        store,
        "get_window",
        AsyncMock(side_effect=sqlite3.OperationalError("unavailable")),
    )
    channel = make_channel()
    interaction = make_interaction(channel)
    await invoke(cog, interaction)
    channel.edit.assert_not_awaited()
    assert "数据库" in reply_text(interaction)
    assert cog.logger.exception.called
    assert_private(interaction)


@pytest.mark.asyncio
async def test_database_write_failure_retries_success_before_next_rename(
    cog, store, monkeypatch
):
    original = store.record_success
    monkeypatch.setattr(
        store,
        "record_success",
        AsyncMock(side_effect=sqlite3.OperationalError("unavailable")),
    )
    channel = make_channel()
    interaction = make_interaction(channel)
    await invoke(cog, interaction)
    assert "已修改" in reply_text(interaction)
    assert "数据库" in reply_text(interaction)
    assert cog.logger.exception.called
    await invoke(cog, make_interaction(channel), "另一个名字")
    assert channel.edit.await_count == 1
    monkeypatch.setattr(store, "record_success", original)
    await invoke(cog, make_interaction(channel), "再试一次")
    assert (await store.get_window(1, 10)).count == 2


@pytest.mark.asyncio
async def test_reinstantiated_cog_still_limited(bot, store, clock):
    await store.record_success(1, 10, 900)
    await store.record_success(1, 10, 950)
    restarted = ChannelRenameCommands(
        bot, store=ChannelRenameStore(store.db_path, clock=lambda: clock.now)
    )
    channel = make_channel()
    await invoke(restarted, make_interaction(channel))
    channel.edit.assert_not_awaited()


def test_slash_command_has_no_member_permission_gate():
    command = ChannelRenameCommands.rename_channel
    assert command.name == "改改的名"
    assert command.default_permissions is None
    assert command.checks == []
    assert command.guild_only
    assert [(param.display_name, param.required) for param in command.parameters] == [
        ("新名称", True),
        ("emoji", False),
    ]


def test_example_config_enables_new_cog():
    config = json.loads(
        (Path(__file__).parents[1] / "config.example.json").read_text(encoding="utf-8")
    )
    assert config["cogs"]["channel_rename"] == {
        "enabled": True,
        "description": "频道改名功能",
    }


@pytest.mark.asyncio
async def test_fresh_channel_name_avoids_duplicate_edit(cog, store):
    stale = make_channel(name="过期缓存名称")
    current = make_channel(name="新名称")
    interaction = make_interaction(stale)
    interaction.guild.fetch_channel.return_value = current
    await invoke(cog, interaction)
    stale.edit.assert_not_awaited()
    current.edit.assert_not_awaited()
    assert (await store.get_window(1, 10)).count == 0
    assert "完全相同" in reply_text(interaction)


@pytest.mark.asyncio
async def test_cancelled_interaction_keeps_lock_through_success_commit(cog, store):
    entered, release = asyncio.Event(), asyncio.Event()
    channel = make_channel()
    await store.record_success(1, 10, 999)

    async def edit(**kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(id=10, name=kwargs["name"])

    channel.edit.side_effect = edit
    first = asyncio.create_task(invoke(cog, make_interaction(channel), "取消后的改名"))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(
            invoke(cog, make_interaction(channel), "第三次改名")
        )
        release.set()
        await asyncio.wait_for(second, 3)
        assert channel.edit.await_count == 1
        assert (await store.get_window(1, 10)).count == 2
    finally:
        release.set()
        await asyncio.gather(*cog._state["tasks"])


@pytest.mark.asyncio
async def test_reload_shares_inflight_channel_lock(cog, store, bot):
    reloaded = ChannelRenameCommands(bot, store=store)
    await store.record_success(1, 10, 999)
    channel = make_channel()
    await asyncio.gather(
        invoke(cog, make_interaction(channel), "来自旧模块"),
        invoke(reloaded, make_interaction(channel), "来自新模块"),
    )
    assert channel.edit.await_count == 1
    assert (await store.get_window(1, 10)).count == 2


@pytest.mark.asyncio
async def test_discord_registration_and_unload_without_login(store):
    bot = RealBot(command_prefix="!", intents=RealIntents.none())
    try:
        cog = ChannelRenameCommands(bot, store=store)
        await bot.add_cog(cog)
        command = bot.tree.get_command("改改的名")
        assert command is not None
        payload = command.to_dict(bot.tree)
        assert payload["default_member_permissions"] is None
        assert [option["name"] for option in payload["options"]] == ["新名称", "emoji"]
        await bot.remove_cog("ChannelRenameCommands")
        assert bot.tree.get_command("改改的名") is None
    finally:
        await bot.close()


def test_cog_manager_integration_is_additive(bot, monkeypatch):
    from src.bot_manage import cogUtils

    # Keep unrelated Cogs' constructors (DBs, loops, config reads) out of this
    # integration check while exercising the real manager's new mapping.
    for module_name, class_name in [
        ("thread_manage", "ThreadSelfManage"),
        ("bot_manage", "BotManageCommands"),
        ("admin", "AdminCommands"),
        ("verify", "VerifyCommands"),
        ("misc", "MiscCommands"),
        ("event", "EventCommands"),
        ("anonymous_feedback", "AnonymousFeedbackCog"),
        ("sync", "ServerSyncCommands"),
        ("license_auto", "LicenseCog"),
        ("banner", "BannerCommands"),
        ("post_filter", "PostFilterCog"),
    ]:
        monkeypatch.setattr(getattr(cogUtils, module_name), class_name, MagicMock())
    manager = cogUtils.CogManager(bot, {"cogs": {}})
    assert manager.cog_module_paths["channel_rename"] == "src.channel_rename.cog"
    assert manager.cog_class_names["channel_rename"] == "ChannelRenameCommands"
    assert isinstance(manager.cog_map["channel_rename"], ChannelRenameCommands)
