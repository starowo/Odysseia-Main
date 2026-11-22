"""反商业化（/aff）模块的单元测试：跨服计次、双服独立权限、补录、双通道撤销、同步过期与双图留痕。"""
from __future__ import annotations

import datetime
import json
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from src.admin.cog import AdminCommands
from src.anticommercial.cog import AntiCommercialCommands, sanitize_image_filename
from src.anticommercial.database import AffPunishDatabase
from src.sync.cog import ServerSyncCommands


GUILD_A_ID = 100000000000000001
GUILD_A_AC_ROLE = 200000000000000001
GUILD_A_ADMIN_ROLE = 200000000000000002

GUILD_B_ID = 100000000000000002
GUILD_B_AC_ROLE = 200000000000000003
GUILD_B_ADMIN_ROLE = 200000000000000004


def make_role(role_id: int, name: str = "Role", position: int = 1) -> MagicMock:
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.name = name
    role.position = position
    role.mention = f"<@&{role_id}>"
    return role


def make_member(
    member_id: int,
    guild: MagicMock,
    roles=None,
    bot: bool = False,
    name: str = "User",
    top_pos: int = 2,
) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.guild = guild
    member.roles = list(roles or [])
    member.bot = bot
    member.mention = f"<@{member_id}>"
    member.display_name = f"{name} Display"
    member.name = name
    member.display_avatar.url = f"https://example.com/{member_id}.png"
    member.guild_permissions.administrator = False
    member.top_role = make_role(99000 + member_id, position=top_pos)
    member.timeout = AsyncMock()
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    return member


def make_guild(
    guild_id: int,
    name: str,
    roles_map: dict,
    announce_ch=None,
    log_ch=None,
) -> MagicMock:
    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    guild.name = name
    guild.owner_id = 999999999
    bot_top_role = make_role(88888, "BotRole", position=50)
    guild.me = MagicMock()
    guild.me.top_role = bot_top_role
    guild.get_role.side_effect = lambda rid: roles_map.get(int(rid))
    guild.get_channel.side_effect = lambda cid: announce_ch if announce_ch and int(cid) == announce_ch.id else None
    guild.get_channel_or_thread.side_effect = lambda cid: log_ch if log_ch and int(cid) == log_ch.id else None
    guild.get_member.return_value = None
    guild.fetch_member = AsyncMock(return_value=None)
    return guild


def make_interaction(guild: MagicMock, user: MagicMock, channel=None) -> AsyncMock:
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.guild = guild
    interaction.guild_id = guild.id
    interaction.user = user
    interaction.channel = channel
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


@pytest.fixture
def dual_server_env(tmp_path):
    db_file = tmp_path / "anticommercial" / "aff_records.db"
    config_data = {
        "warned_role_id": 9001,
        "punish_announce_channel_id": 8001,
        "punish_archive_channel_id": 8002,
        "moderation_log_channel_id": 8003,
        "anticommercial": {
            "db_path": str(db_file),
            "shared_guild_ids": [GUILD_A_ID, GUILD_B_ID],
            "tiers": [
                {"mute": "0d", "warn_days": 3},
                {"mute": "3d", "warn_days": 30},
            ],
        },
        "server_override": {
            str(GUILD_A_ID): {
                "admins": [GUILD_A_ADMIN_ROLE],
                "senior_admins": [GUILD_A_ADMIN_ROLE],
                "warned_role_id": 9001,
                "punish_announce_channel_id": 8001,
                "punish_archive_channel_id": 8002,
                "moderation_log_channel_id": 8003,
                "anticommercial": {
                    "role_ids": [GUILD_A_AC_ROLE],
                },
            },
            str(GUILD_B_ID): {
                "admins": [GUILD_B_ADMIN_ROLE],
                "senior_admins": [GUILD_B_ADMIN_ROLE],
                "warned_role_id": 9002,
                "punish_announce_channel_id": 8101,
                "punish_archive_channel_id": 8002,
                "moderation_log_channel_id": 8103,
                "anticommercial": {
                    "role_ids": [GUILD_B_AC_ROLE],
                },
            },
        },
    }

    warn_role_a = make_role(9001, "已警告A", position=1)
    ac_role_a = make_role(GUILD_A_AC_ROLE, "反商A", position=5)
    admin_role_a = make_role(GUILD_A_ADMIN_ROLE, "管理A", position=10)

    warn_role_b = make_role(9002, "已警告B", position=1)
    ac_role_b = make_role(GUILD_B_AC_ROLE, "反商B", position=5)
    admin_role_b = make_role(GUILD_B_ADMIN_ROLE, "管理B", position=10)

    announce_a = AsyncMock(spec=discord.TextChannel)
    announce_a.id = 8001
    log_a = AsyncMock(spec=discord.TextChannel)
    log_a.id = 8003

    announce_b = AsyncMock(spec=discord.TextChannel)
    announce_b.id = 8101
    log_b = AsyncMock(spec=discord.TextChannel)
    log_b.id = 8103

    archive_ch = AsyncMock(spec=discord.TextChannel)
    archive_ch.id = 8002

    guild_a = make_guild(
        GUILD_A_ID,
        "主服务器A",
        {9001: warn_role_a, GUILD_A_AC_ROLE: ac_role_a, GUILD_A_ADMIN_ROLE: admin_role_a},
        announce_ch=announce_a,
        log_ch=log_a,
    )
    guild_b = make_guild(
        GUILD_B_ID,
        "副服务器B",
        {9002: warn_role_b, GUILD_B_AC_ROLE: ac_role_b, GUILD_B_ADMIN_ROLE: admin_role_b},
        announce_ch=announce_b,
        log_ch=log_b,
    )

    sync_cog = MagicMock()
    sync_cog.sync_punishment = AsyncMock()
    sync_cog.sync_revoke_punishment = AsyncMock()

    bot = MagicMock()
    bot.logger = MagicMock()
    bot.get_guild.side_effect = lambda gid: {GUILD_A_ID: guild_a, GUILD_B_ID: guild_b}.get(int(gid))
    bot.get_channel.side_effect = lambda cid: archive_ch if int(cid) == 8002 else None
    bot.fetch_channel = AsyncMock(return_value=archive_ch)
    bot.fetch_user = AsyncMock(side_effect=lambda uid: MagicMock(id=int(uid), bot=False))

    cog = AntiCommercialCommands(bot, data_dir=tmp_path / "data")
    bot.get_cog.side_effect = lambda name: {
        "ServerSyncCommands": sync_cog,
        "AntiCommercialCommands": cog,
    }.get(name)

    return {
        "tmp_path": tmp_path,
        "config": config_data,
        "bot": bot,
        "cog": cog,
        "sync_cog": sync_cog,
        "guild_a": guild_a,
        "guild_b": guild_b,
        "roles_a": {"warn": warn_role_a, "ac": ac_role_a, "admin": admin_role_a},
        "roles_b": {"warn": warn_role_b, "ac": ac_role_b, "admin": admin_role_b},
        "channels": {
            "announce_a": announce_a,
            "log_a": log_a,
            "announce_b": announce_b,
            "log_b": log_b,
            "archive": archive_ch,
        },
    }


def _patch_cfg(cfg_dict):
    return (
        patch("src.utils.config_helper._load_config", return_value=cfg_dict),
        patch("src.anticommercial.config._load_config", return_value=cfg_dict),
    )


@pytest.mark.asyncio
async def test_cross_server_first_second_third_offense_flow(dual_server_env):
    env = dual_server_env
    cog = env["cog"]
    guild_a = env["guild_a"]
    guild_b = env["guild_b"]

    op_a = make_member(101, guild_a, roles=[env["roles_a"]["ac"]], name="ModA")
    op_b = make_member(102, guild_b, roles=[env["roles_b"]["ac"]], name="ModB")
    offender_a = make_member(555, guild_a, roles=[], name="Offender")
    offender_b = make_member(555, guild_b, roles=[], name="Offender")

    p1, p2 = _patch_cfg(env["config"])
    with p1, p2, \
         patch("src.anticommercial.cog.confirm_view_embed", new=AsyncMock(return_value=True)), \
         patch("src.anticommercial.cog.send_dm", new=AsyncMock()) as mock_dm:

        # 1. 在主服 A 初犯：3天警告（不禁言）
        inter_1 = make_interaction(guild_a, op_a)
        await AntiCommercialCommands.aff_punish.callback(
            cog, inter_1, offender_a, "推荐VPS带aff", None, None
        )
        offender_a.timeout.assert_not_awaited()
        offender_a.add_roles.assert_awaited_once_with(
            env["roles_a"]["warn"], reason="反商aff处罚附加警告 3 天"
        )
        env["sync_cog"].sync_punishment.assert_awaited_once()
        sync_kwargs_1 = env["sync_cog"].sync_punishment.await_args.kwargs
        assert sync_kwargs_1["duration"] is None
        assert sync_kwargs_1["warn_days"] == 3
        dm_embed_1 = mock_dm.await_args.kwargs["embed"]
        assert "第 1 次" in dm_embed_1.description
        assert "禁言 3天 + 警告 30 天" in dm_embed_1.description
        inter_1.followup.send.assert_awaited_once()
        assert inter_1.followup.send.await_args.kwargs["ephemeral"] is False

        # 2. 同一用户在副服 B 再犯：自动识别为第 2 次 -> 3天禁言 + 30天警告
        inter_2 = make_interaction(guild_b, op_b)
        await AntiCommercialCommands.aff_punish.callback(
            cog, inter_2, offender_b, "副服再次发aff", None, None
        )
        offender_b.timeout.assert_awaited_once_with(
            datetime.timedelta(days=3),
            reason="[第2次aff] 副服再次发aff",
        )
        offender_b.add_roles.assert_awaited_once_with(
            env["roles_b"]["warn"], reason="反商aff处罚附加警告 30 天"
        )
        sync_kwargs_2 = env["sync_cog"].sync_punishment.await_args.kwargs
        assert sync_kwargs_2["duration"] == 3 * 86400
        assert sync_kwargs_2["warn_days"] == 30
        dm_embed_2 = mock_dm.await_args.kwargs["embed"]
        assert "第 2 次" in dm_embed_2.description

        # 3. 第 3 次犯：继续按 3天禁言 + 30天警告
        offender_b.timeout.reset_mock()
        offender_b.add_roles.reset_mock()
        inter_3 = make_interaction(guild_b, op_b)
        await AntiCommercialCommands.aff_punish.callback(
            cog, inter_3, offender_b, "第三次发aff", None, None
        )
        offender_b.timeout.assert_awaited_once_with(
            datetime.timedelta(days=3),
            reason="[第3次aff] 第三次发aff",
        )
        offender_b.add_roles.assert_awaited_once_with(
            env["roles_b"]["warn"], reason="反商aff处罚附加警告 30 天"
        )


@pytest.mark.asyncio
async def test_dual_server_role_isolation_and_uncached_backfill_protections(dual_server_env):
    env = dual_server_env
    cog = env["cog"]
    guild_a = env["guild_a"]
    guild_b = env["guild_b"]

    wrong_guild_mod = make_member(201, guild_b, roles=[env["roles_a"]["ac"]], name="WrongMod")
    target_b = make_member(202, guild_b, roles=[], name="Target")
    inter_wrong = make_interaction(guild_b, wrong_guild_mod)

    p1, p2 = _patch_cfg(env["config"])
    with p1, p2:
        await AntiCommercialCommands.aff_punish.callback(cog, inter_wrong, target_b, None, None, None)
        inter_wrong.response.send_message.assert_awaited_once_with(
            "❌ 您不是反商业化委员或管理组成员，无权使用此命令", ephemeral=True
        )

        op_a = make_member(203, guild_a, roles=[env["roles_a"]["ac"]], name="ModA")
        peer_ac = make_member(204, guild_a, roles=[env["roles_a"]["ac"]], name="PeerAC")
        admin_b_uncached = make_member(205, guild_b, roles=[env["roles_b"]["admin"]], name="AdminB")

        inter_peer = make_interaction(guild_a, op_a)
        await AntiCommercialCommands.aff_punish.callback(cog, inter_peer, peer_ac, None, None, None)
        inter_peer.response.send_message.assert_awaited_once_with(
            "❌ 不能对反商业化委员执行此操作", ephemeral=True
        )

        # 通过 user_id 补录不在本地缓存但在副服担任管理员的成员 -> fetch_member 拦截
        guild_b.fetch_member = AsyncMock(
            side_effect=lambda uid: admin_b_uncached if int(uid) == 205 else None
        )
        inter_bf_admin = make_interaction(guild_a, op_a)
        await AntiCommercialCommands.aff_backfill.callback(cog, inter_bf_admin, None, "205", "试图补录副服管理")
        inter_bf_admin.followup.send.assert_awaited_once_with(
            "❌ 不能对管理组成员执行此操作", ephemeral=True
        )


@pytest.mark.asyncio
async def test_backfill_and_plan_b_revoke_by_committee_and_admin(dual_server_env):
    env = dual_server_env
    cog = env["cog"]
    guild_a = env["guild_a"]
    guild_b = env["guild_b"]

    op_a = make_member(301, guild_a, roles=[env["roles_a"]["ac"]], name="ModA")
    admin_b = make_member(302, guild_b, roles=[env["roles_b"]["admin"]], name="AdminB")
    offender_b = make_member(777, guild_b, roles=[env["roles_b"]["warn"]], name="Offender777")
    guild_b.get_member.side_effect = lambda uid: offender_b if int(uid) == 777 else None

    p1, p2 = _patch_cfg(env["config"])
    with p1, p2, \
         patch("src.anticommercial.cog.confirm_view_embed", new=AsyncMock(return_value=True)), \
         patch("src.anticommercial.cog.send_dm", new=AsyncMock()):

        # 1. 先在主服补录 1 次历史前科（不触发禁言）
        inter_bf = make_interaction(guild_a, op_a)
        await AntiCommercialCommands.aff_backfill.callback(
            cog, inter_bf, None, "777", "旧留痕区初犯补录"
        )
        db = AffPunishDatabase(env["config"]["anticommercial"]["db_path"])
        active_after_bf = await db.get_active_records(777)
        assert len(active_after_bf) == 1
        bf_id = active_after_bf[0]["punish_id"]

        # 2. 在副服触发正式处罚 -> 直接命中第 2 次（3d 禁言 + 30d 警告）
        inter_p = make_interaction(guild_b, admin_b)
        await AntiCommercialCommands.aff_punish.callback(
            cog, inter_p, offender_b, "再犯aff", None, None
        )
        offender_b.timeout.assert_awaited_once()
        active_after_p = await db.get_active_records(777)
        assert len(active_after_p) == 2
        punish_id = active_after_p[1]["punish_id"]

        # 3. 方案 B：反商委员跨服撤销这笔正式处罚 -> 解禁言 + 摘警告组 + 同步撤销 + 双方日志 + 次数退回 1
        offender_b.timeout.reset_mock()
        inter_rev = make_interaction(guild_a, op_a)
        await AntiCommercialCommands.aff_revoke.callback(
            cog, inter_rev, punish_id, "上诉核实非aff后缀"
        )
        offender_b.timeout.assert_awaited_once_with(None, reason="撤销aff处罚: 上诉核实非aff后缀")
        offender_b.remove_roles.assert_awaited_once_with(
            env["roles_b"]["warn"], reason="撤销aff处罚: 上诉核实非aff后缀"
        )
        env["sync_cog"].sync_revoke_punishment.assert_awaited_once_with(
            guild_b, punish_id, op_a, "上诉核实非aff后缀"
        )
        # 发起服 A 和原服 B 的管理日志频道均收到审计记录
        env["channels"]["log_b"].send.assert_awaited()
        env["channels"]["log_a"].send.assert_awaited()
        active_after_rev = await db.get_active_records(777)
        assert len(active_after_rev) == 1

        # 4. 撤销补录记录 -> 有效次数退回 0
        inter_rev_bf = make_interaction(guild_a, op_a)
        await AntiCommercialCommands.aff_revoke.callback(
            cog, inter_rev_bf, bf_id, "补录错UID"
        )
        assert len(await db.get_active_records(777)) == 0

        # 5. 不能用 /aff 撤销 越权去撤普通管理永封记录
        inter_foreign = make_interaction(guild_a, op_a)
        await AntiCommercialCommands.aff_revoke.callback(
            cog, inter_foreign, "deadbeef", "试图越权撤销"
        )
        inter_foreign.followup.send.assert_awaited_once()
        assert "仅可撤销 `/aff` 产生的" in inter_foreign.followup.send.await_args.args[0]


@pytest.mark.asyncio
async def test_dual_images_filename_sanitization_and_admin_revoke_hook(dual_server_env, monkeypatch):
    env = dual_server_env
    cog = env["cog"]
    guild_a = env["guild_a"]

    op_a = make_member(401, guild_a, roles=[env["roles_a"]["ac"]], name="ModA")
    offender = make_member(888, guild_a, roles=[env["roles_a"]["warn"]], name="Offender888")
    guild_a.get_member.side_effect = lambda uid: offender if int(uid) == 888 else None

    redacted_img = AsyncMock(spec=discord.Attachment)
    redacted_img.filename = "Screenshot 2026-11-22 (打码).PNG"
    redacted_img.read = AsyncMock(return_value=b"redacted-bytes")

    raw_img = AsyncMock(spec=discord.Attachment)
    raw_img.filename = "原图 (1).jpg"
    raw_img.read = AsyncMock(return_value=b"raw-bytes")

    p1, p2 = _patch_cfg(env["config"])
    with p1, p2, \
         patch("src.anticommercial.cog.confirm_view_embed", new=AsyncMock(return_value=True)), \
         patch("src.anticommercial.cog.send_dm", new=AsyncMock()):

        inter = make_interaction(guild_a, op_a)
        await AntiCommercialCommands.aff_punish.callback(
            cog, inter, offender, "挂返利链接", redacted_img, raw_img
        )

        # 验证含空格/中文/括号的文件名已被安全规范化，且公示与留痕各收各图
        pub_send = env["channels"]["announce_a"].send
        arch_send = env["channels"]["archive"].send
        pub_send.assert_awaited_once()
        arch_send.assert_awaited_once()
        assert pub_send.await_args.kwargs["file"].filename == "evidence_pub.png"
        assert arch_send.await_args.kwargs["file"].filename == "evidence_archive.jpg"

        db = AffPunishDatabase(env["config"]["anticommercial"]["db_path"])
        records = await db.get_active_records(888)
        assert len(records) == 1
        pid = records[0]["punish_id"]

        # 验证即使目标用户已退服（NotFound），/管理 撤销处罚 仍能完成文件清理与 SQLite 销账
        monkeypatch.chdir(env["tmp_path"])
        guild_a.get_member.side_effect = None
        guild_a.get_member.return_value = None
        guild_a.fetch_member = AsyncMock(side_effect=discord.NotFound(MagicMock(), "Member not found"))

        admin_cog = AdminCommands(env["bot"])
        senior_admin = make_member(402, guild_a, roles=[env["roles_a"]["admin"]], name="SeniorAdmin")
        inter_admin_rev = make_interaction(guild_a, senior_admin)
        await AdminCommands.revoke_punish.callback(admin_cog, inter_admin_rev, pid, "高级管理直接撤销")

        assert len(await db.get_active_records(888)) == 0


@pytest.mark.asyncio
async def test_sync_punishment_creates_and_cleans_warn_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bot = MagicMock()
    bot.logger = MagicMock()

    warn_role_b = make_role(9002, "已警告B")
    guild_b = make_guild(GUILD_B_ID, "副服务器B", {9002: warn_role_b})
    member_b = make_member(666, guild_b, roles=[warn_role_b], name="SyncedUser")
    guild_b.get_member.return_value = member_b

    sync_cog = ServerSyncCommands(bot)
    sync_cog._get_warned_role_id = MagicMock(return_value=9002)
    sync_cog._resolve_punish_announce_channel = MagicMock(return_value=None)

    record = {
        "id": "sync1234",
        "type": "mute",
        "user_id": 666,
        "reason": "同步测试",
        "duration": None,
        "warn_days": 3,
        "warn": 3,
    }
    await sync_cog._apply_punishment_in_guild(guild_b, record, {"punishment_sync": True})

    warn_files = list((tmp_path / "data" / "warn" / str(GUILD_B_ID)).glob("*.json"))
    assert len(warn_files) == 1
    saved_warn = json.loads(warn_files[0].read_text(encoding="utf-8"))
    assert saved_warn["punish_id"] == "sync1234"

    moderator = make_member(1, guild_b, name="Mod")
    await sync_cog._revoke_punishment_in_guild(guild_b, "sync1234", moderator, "同步撤销测试", {})
    assert list((tmp_path / "data" / "warn" / str(GUILD_B_ID)).glob("*.json")) == []
    member_b.remove_roles.assert_awaited_once()


def test_aff_command_group_names_and_filename_sanitizer():
    assert AntiCommercialCommands.anticommercial.name == "aff"
    sub_names = [cmd.name for cmd in AntiCommercialCommands.anticommercial.commands]
    assert sub_names == ["处罚", "查询", "补录", "撤销"]
    assert sanitize_image_filename("Screenshot 2026 (1).PNG", "evidence_pub") == "evidence_pub.png"
    assert sanitize_image_filename("weird.exe", "evidence_pub") == "evidence_pub.png"
