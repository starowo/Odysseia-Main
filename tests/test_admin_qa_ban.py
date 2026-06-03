from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from src.admin.cog import AdminCommands, QA_PERMABAN_REASON


def make_role(role_id: int):
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    return role


def make_member(member_id: int, guild, roles=None, bot=False, name="User"):
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.guild = guild
    member.roles = roles or []
    member.bot = bot
    member.mention = f"<@{member_id}>"
    member.display_name = f"{name} Display"
    member.name = name
    member.display_avatar.url = f"https://example.com/{member_id}.png"
    member.guild_permissions.administrator = False
    member.__str__.return_value = f"{name}#{member_id}"
    return member


def make_interaction(guild, user):
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.guild = guild
    interaction.user = user
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    return interaction


@pytest.fixture
def qa_ban_context():
    bot = MagicMock()
    bot.logger = MagicMock()
    bot.get_cog.return_value = None

    guild = MagicMock(spec=discord.Guild)
    guild.id = 123
    guild.name = "测试服务器"
    guild.ban = AsyncMock()

    qa_role = make_role(10)
    guild.get_role.return_value = qa_role
    guild.get_channel.return_value = None
    guild.get_channel_or_thread.return_value = None

    operator = make_member(1, guild, roles=[qa_role], name="Operator")
    target = make_member(2, guild, roles=[], name="Target")
    interaction = make_interaction(guild, operator)

    cog = AdminCommands(bot)
    cog.get_guild_config = MagicMock(
        side_effect=lambda key, guild_id, default=None: {
            "qa_role_id": qa_role.id,
            "punish_announce_channel_id": 0,
            "quiz_punish_log_channel_id": 0,
        }.get(key, default)
    )
    cog._save_punish_record = MagicMock(return_value="abc12345")

    return cog, interaction, guild, qa_role, operator, target


async def call_qa_ban(cog, interaction, member):
    await AdminCommands.qa_ban.callback(cog, interaction, member)


@pytest.mark.asyncio
async def test_qa_ban_rejects_missing_qa_role_config(qa_ban_context):
    cog, interaction, guild, _, _, target = qa_ban_context
    cog.get_guild_config = MagicMock(return_value=0)

    await call_qa_ban(cog, interaction, target)

    interaction.response.send_message.assert_called_once_with(
        "❌ 当前服务器未设置答疑组", ephemeral=True
    )
    guild.ban.assert_not_called()


@pytest.mark.asyncio
async def test_qa_ban_rejects_missing_qa_role(qa_ban_context):
    cog, interaction, guild, _, _, target = qa_ban_context
    guild.get_role.return_value = None

    await call_qa_ban(cog, interaction, target)

    interaction.response.send_message.assert_called_once_with(
        "❌ 答疑组角色不存在", ephemeral=True
    )
    guild.ban.assert_not_called()


@pytest.mark.asyncio
async def test_qa_ban_rejects_non_qa_operator(qa_ban_context):
    cog, interaction, guild, _, operator, target = qa_ban_context
    operator.roles = []

    await call_qa_ban(cog, interaction, target)

    interaction.response.send_message.assert_called_once_with(
        "❌ 您不是答疑组成员", ephemeral=True
    )
    guild.ban.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_mutator", "expected_message"),
    [
        (lambda ctx: ctx["operator"], "❌ 不能封禁自己"),
        (lambda ctx: make_member(3, ctx["guild"], bot=True), "❌ 不能封禁机器人"),
        (
            lambda ctx: make_member(4, ctx["guild"], roles=[ctx["qa_role"]]),
            "❌ 不能封禁答疑组成员",
        ),
    ],
)
async def test_qa_ban_rejects_protected_targets(
    qa_ban_context,
    target_mutator,
    expected_message,
):
    cog, interaction, guild, qa_role, operator, _ = qa_ban_context
    target = target_mutator({"guild": guild, "qa_role": qa_role, "operator": operator})

    await call_qa_ban(cog, interaction, target)

    interaction.response.send_message.assert_called_once_with(
        expected_message, ephemeral=True
    )
    guild.ban.assert_not_called()


@pytest.mark.asyncio
async def test_qa_ban_rejects_senior_admin_target(qa_ban_context):
    cog, interaction, guild, _, _, target = qa_ban_context

    with patch("src.admin.cog.is_senior_admin_member", return_value=True):
        await call_qa_ban(cog, interaction, target)

    interaction.response.send_message.assert_called_once_with(
        "❌ 不能封禁高级管理组成员", ephemeral=True
    )
    guild.ban.assert_not_called()


@pytest.mark.asyncio
async def test_qa_ban_rejects_admin_target(qa_ban_context):
    cog, interaction, guild, _, _, target = qa_ban_context

    with patch("src.admin.cog.is_senior_admin_member", return_value=False), patch(
        "src.admin.cog.is_admin_member",
        return_value=True,
    ):
        await call_qa_ban(cog, interaction, target)

    interaction.response.send_message.assert_called_once_with(
        "❌ 不能封禁管理组成员", ephemeral=True
    )
    guild.ban.assert_not_called()


@pytest.mark.asyncio
async def test_qa_ban_cancel_confirmation_does_not_ban(qa_ban_context):
    cog, interaction, guild, _, _, target = qa_ban_context

    with patch("src.admin.cog.confirm_view_embed", new=AsyncMock(return_value=False)):
        await call_qa_ban(cog, interaction, target)

    interaction.response.defer.assert_called_once_with(ephemeral=True)
    guild.ban.assert_not_called()
    cog._save_punish_record.assert_not_called()


@pytest.mark.asyncio
async def test_qa_ban_success_saves_syncs_and_announces(qa_ban_context):
    cog, interaction, guild, _, operator, target = qa_ban_context
    announce_channel = AsyncMock()
    quiz_log_channel = AsyncMock()
    guild.get_channel.return_value = announce_channel
    guild.get_channel_or_thread.return_value = quiz_log_channel
    cog.get_guild_config = MagicMock(
        side_effect=lambda key, guild_id, default=None: {
            "qa_role_id": 10,
            "punish_announce_channel_id": 20,
            "quiz_punish_log_channel_id": 30,
        }.get(key, default)
    )
    sync_cog = MagicMock()
    sync_cog.sync_punishment = AsyncMock()
    cog.bot.get_cog.return_value = sync_cog

    with patch(
        "src.admin.cog.confirm_view_embed", new=AsyncMock(return_value=True)
    ) as confirm_mock, patch(
        "src.admin.cog.dm.send_dm",
        new=AsyncMock(),
    ):
        await call_qa_ban(cog, interaction, target)

    confirm_mock.assert_awaited_once()
    guild.ban.assert_awaited_once_with(
        target, reason=QA_PERMABAN_REASON, delete_message_days=0
    )
    cog._save_punish_record.assert_called_once_with(
        guild.id,
        {
            "type": "ban",
            "user_id": target.id,
            "moderator_id": operator.id,
            "reason": QA_PERMABAN_REASON,
        },
    )
    sync_cog.sync_punishment.assert_awaited_once_with(
        guild=guild,
        punishment_type="ban",
        member=target,
        moderator=operator,
        reason=QA_PERMABAN_REASON,
        punishment_id="abc12345",
    )
    assert interaction.followup.send.await_count == 2

    announce_embed = announce_channel.send.await_args.kwargs["embed"]
    assert announce_embed.title == "⛔ 永久封禁"
    assert announce_embed.fields[0].name == "成员"
    assert announce_embed.fields[1].name == "答疑组成员"
    assert announce_embed.fields[2].name == "原因"
    assert announce_embed.footer.text == "处罚ID: abc12345"
    quiz_log_channel.send.assert_awaited_once()
