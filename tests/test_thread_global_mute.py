from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

import src.thread_manage.db as thread_db
import src.thread_manage.self_manage_ui as self_manage_ui
from src.thread_manage.cog import ThreadSelfManage
from src.thread_manage.self_manage_ui import SelfManageMainSelect

pytestmark = pytest.mark.asyncio


def make_cog() -> ThreadSelfManage:
    cog = ThreadSelfManage.__new__(ThreadSelfManage)
    cog.logger = MagicMock()
    cog._mute_cache = {}
    cog._thread_owner_cache = {}
    cog._thread_owner_ready = set()
    return cog


async def test_mute_schema_migration_preserves_legacy_single_thread_records(tmp_path):
    """新增全贴层字段时，旧 muted_until 必须原样保留为单帖禁言。"""
    previous_db = thread_db._db
    connection = await aiosqlite.connect(tmp_path / "legacy-mutes.db")
    connection.row_factory = aiosqlite.Row
    thread_db._db = connection
    try:
        await connection.execute(
            """CREATE TABLE thread_mutes (
                guild_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                muted_until INTEGER,
                violations INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, thread_id, user_id)
            )"""
        )
        await connection.execute(
            "INSERT INTO thread_mutes VALUES (?, ?, ?, ?, ?)",
            (1, 2, 3, -1, 4),
        )
        await connection.commit()

        await thread_db._ensure_thread_mute_columns()
        await thread_db._ensure_thread_mute_columns()

        columns = {
            row[1]
            for row in await connection.execute_fetchall(
                "PRAGMA table_info(thread_mutes)"
            )
        }
        row = (
            await connection.execute_fetchall(
                "SELECT muted_until, global_muted_until FROM thread_mutes"
            )
        )[0]
        assert "global_muted_until" in columns
        assert tuple(row) == (-1, None)
    finally:
        await connection.close()
        thread_db._db = previous_db


async def test_layered_mute_writes_preserve_the_other_layer(monkeypatch):
    """单帖与全贴禁言分别写入时，不得覆盖另一层。"""
    cog = make_cog()
    monkeypatch.setattr("src.thread_manage.cog.db.save_mute_record", AsyncMock())
    channel = SimpleNamespace(
        id=50, guild=SimpleNamespace(id=1), send=AsyncMock()
    )
    member = SimpleNamespace(id=42, mention="<@42>")
    actor = SimpleNamespace(mention="<@10>")

    await cog._mute_thread_user(
        channel,
        member,
        muted_until="2030-01-01T00:00:00",
        reason=None,
        actor=actor,
        announce=False,
    )
    await cog._mute_thread_user(
        channel,
        member,
        muted_until=-1,
        reason=None,
        actor=actor,
        announce=False,
        scope="global",
    )

    assert cog._mute_cache[(1, 50, 42)] == {
        "muted_until": "2030-01-01T00:00:00",
        "global_muted_until": -1,
        "violations": 0,
    }


async def test_mute_layer_expiry_clears_only_expired_layer(monkeypatch):
    """一层过期时只清该层，另一有效层仍继续阻止发言。"""
    cog = make_cog()
    monkeypatch.setattr("src.thread_manage.cog.db.save_mute_record", AsyncMock())
    cog._mute_cache[(1, 50, 42)] = {
        "muted_until": "2000-01-01T00:00:00",
        "global_muted_until": -1,
        "violations": 0,
    }

    assert await cog._is_thread_muted(1, 50, 42) is True
    assert cog._mute_cache[(1, 50, 42)]["muted_until"] is None
    assert cog._mute_cache[(1, 50, 42)]["global_muted_until"] == -1


@pytest.mark.parametrize(
    ("actor_id", "owner_id", "is_admin", "expected"),
    [
        (10, 10, False, True),
        (20, 10, True, True),
        (30, 10, False, False),  # 协管不能获得全贴权限
        (40, 10, False, False),  # 普通成员不能获得全贴权限
    ],
)
async def test_global_thread_action_permission_excludes_delegate(
    actor_id, owner_id, is_admin, expected
):
    cog = make_cog()
    cog.is_admin = AsyncMock(return_value=is_admin)
    interaction = SimpleNamespace(user=SimpleNamespace(id=actor_id))
    thread = SimpleNamespace(owner_id=owner_id)

    assert await cog.can_global_thread_action(interaction, thread) is expected


async def test_scan_all_forum_threads_builds_owner_index(monkeypatch):
    cog = make_cog()

    class FakeForum:
        def __init__(self, active, archived):
            self.id = 99
            self.threads = active
            self._archived = archived

        def archived_threads(self, *, limit=None):
            async def iterator():
                for thread in self._archived:
                    yield thread

            return iterator()

    monkeypatch.setattr("src.thread_manage.cog.discord.ForumChannel", FakeForum)
    owned_active = SimpleNamespace(id=1, owner_id=42)
    other_active = SimpleNamespace(id=2, owner_id=7)
    owned_archived = SimpleNamespace(id=3, owner_id=42)
    duplicate = SimpleNamespace(id=1, owner_id=42)
    forum = FakeForum(
        [owned_active, other_active],
        [owned_archived, duplicate],
    )
    guild = SimpleNamespace(channels=[forum, SimpleNamespace(id=100)])

    result = await cog._scan_all_forum_threads(guild)

    assert result == {42: {1, 3}, 7: {2}}


async def test_global_mute_scans_current_thread_owner_posts(monkeypatch):
    """A 对 B 执行全贴禁言时，应扫描 A 的帖子而不是 B 的帖子。"""
    cog = make_cog()
    source = SimpleNamespace(id=100, owner_id=10)
    target = SimpleNamespace(id=42, mention="<@42>")
    owner = SimpleNamespace(id=10, mention="<@10>")
    guild = SimpleNamespace(id=1, get_member=lambda user_id: owner if user_id == 10 else None)
    message = SimpleNamespace(edit=AsyncMock())
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=10),
        response=SimpleNamespace(defer=AsyncMock()),
        original_response=AsyncMock(return_value=message),
    )
    cog.can_global_thread_action = AsyncMock(return_value=True)
    cog._build_owner_index = AsyncMock()
    cog._lookup_owned_threads = MagicMock(return_value=[])
    cog._apply_global_thread_mute = AsyncMock(return_value=0)
    monkeypatch.setattr(
        "src.thread_manage.cog.wait_menu_confirm_on_message",
        AsyncMock(return_value=True),
    )

    await cog.menu_run_global_thread_action(
        interaction, source, target, is_mute=True
    )

    cog._build_owner_index.assert_awaited_once_with(guild)
    cog._lookup_owned_threads.assert_called_once_with(guild, 10)


async def test_global_mute_announces_only_in_source_thread(monkeypatch):
    """全贴禁言仅在执行指令的当前帖子公示，其他帖子静默按 id 写库。"""
    cog = make_cog()
    monkeypatch.setattr("src.thread_manage.cog.db.save_mute_records_bulk", AsyncMock())
    announce_channel = SimpleNamespace(id=100, send=AsyncMock())
    target = SimpleNamespace(id=42)
    actor = SimpleNamespace(id=10)
    cog._mute_thread_user = AsyncMock(return_value=True)
    cog._apply_mute_by_ids = AsyncMock(return_value=True)

    affected = await cog._apply_global_thread_mute(
        7, [100, 101], target, actor, announce_channel=announce_channel
    )

    assert affected == 2
    cog._mute_thread_user.assert_awaited_once()
    announce_call = cog._mute_thread_user.await_args
    assert announce_call.args[0] is announce_channel
    assert announce_call.kwargs["announce"] is True
    assert announce_call.kwargs["announcement_title"] == "🔒 全贴禁言"
    cog._apply_mute_by_ids.assert_awaited_once()
    assert cog._apply_mute_by_ids.await_args.args[:5] == (7, 101, 42, -1, "global")


async def test_apply_global_thread_mute_uses_bulk_helper_for_non_announce(monkeypatch):
    cog = make_cog()
    monkeypatch.setattr("src.thread_manage.cog.db.save_mute_records_bulk", AsyncMock())
    member = SimpleNamespace(id=42)
    actor = SimpleNamespace(id=10)
    cog._mute_thread_user = AsyncMock(return_value=True)
    cog._apply_mute_by_ids = AsyncMock(return_value=True)

    affected = await cog._apply_global_thread_mute(7, [1, 2], member, actor)

    assert affected == 2
    cog._mute_thread_user.assert_not_awaited()
    assert cog._apply_mute_by_ids.await_count == 2
    for call in cog._apply_mute_by_ids.await_args_list:
        assert call.args[0] == 7
        assert call.args[3] == -1
        assert call.args[4] == "global"


async def test_apply_global_thread_mute_forwards_custom_mute_parameters(monkeypatch):
    """防止批量入口丢失直接命令传入的时长、原因或显示标签。"""
    cog = make_cog()
    monkeypatch.setattr("src.thread_manage.cog.db.save_mute_records_bulk", AsyncMock())
    announce_channel = SimpleNamespace(id=1, send=AsyncMock())
    member = SimpleNamespace(id=42)
    actor = SimpleNamespace(id=10)
    cog._mute_thread_user = AsyncMock(return_value=True)
    cog._apply_mute_by_ids = AsyncMock(return_value=True)

    await cog._apply_global_thread_mute(
        7,
        [1, 2],
        member,
        actor,
        muted_until="2026-07-27T12:00:00",
        reason="跨帖测试",
        duration_label="1d",
        announce_channel=announce_channel,
    )

    announce_call = cog._mute_thread_user.await_args
    assert announce_call.kwargs["muted_until"] == "2026-07-27T12:00:00"
    assert announce_call.kwargs["reason"] == "跨帖测试"
    assert announce_call.kwargs["duration_label"] == "1d"
    assert cog._apply_mute_by_ids.await_args.args[3] == "2026-07-27T12:00:00"


async def test_apply_global_thread_unmute_only_counts_existing_records(monkeypatch):
    cog = make_cog()
    monkeypatch.setattr("src.thread_manage.cog.db.save_mute_records_bulk", AsyncMock())
    member = SimpleNamespace(id=42)
    actor = SimpleNamespace(id=10)
    cog._apply_unmute_by_ids = AsyncMock(
        side_effect=[(True, False), (False, False)]
    )

    affected, still_single = await cog._apply_global_thread_unmute(
        7, [1, 2], member, actor
    )

    assert (affected, still_single) == (1, 0)
    assert cog._apply_unmute_by_ids.await_count == 2
    for call in cog._apply_unmute_by_ids.await_args_list:
        assert call.args[0] == 7
        assert call.args[3] == "global"


async def test_global_unmute_clears_global_layer_and_preserves_single_layer(monkeypatch):
    """撤销全贴禁言后，独立存在的单帖禁言必须继续生效。"""
    cog = make_cog()
    monkeypatch.setattr("src.thread_manage.cog.db.save_mute_record", AsyncMock())
    channel = SimpleNamespace(
        id=50, guild=SimpleNamespace(id=1), send=AsyncMock()
    )
    member = SimpleNamespace(id=42, mention="<@42>")
    actor = SimpleNamespace(mention="<@10>")
    cog._mute_cache[(1, 50, 42)] = {
        "muted_until": -1,
        "global_muted_until": -1,
        "violations": 0,
    }

    removed = await cog._unmute_thread_user(
        channel,
        member,
        actor=actor,
        announce=False,
        scope="global",
    )

    assert removed is True
    assert cog._mute_cache[(1, 50, 42)]["muted_until"] == -1
    assert cog._mute_cache[(1, 50, 42)]["global_muted_until"] is None
    assert await cog._is_thread_muted(1, 50, 42) is True


async def test_global_unmute_returns_removed_and_still_single_counts(monkeypatch):
    """批量撤销只统计实际全贴层，并报告撤销后仍有单帖层的帖子。"""
    cog = make_cog()
    monkeypatch.setattr("src.thread_manage.cog.db.save_mute_records_bulk", AsyncMock())
    member = SimpleNamespace(id=42)
    actor = SimpleNamespace(id=10)
    cog._apply_unmute_by_ids = AsyncMock(
        side_effect=[(True, True), (True, False), (False, False)]
    )

    result = await cog._apply_global_thread_unmute(7, [1, 2, 3], member, actor)

    assert result == (2, 1)
    for call in cog._apply_unmute_by_ids.await_args_list:
        assert call.args[3] == "global"


async def test_global_unmute_announces_only_in_source_thread(monkeypatch):
    """解除全贴禁言公示只发送到执行命令的当前帖子。"""
    cog = make_cog()
    monkeypatch.setattr("src.thread_manage.cog.db.save_mute_records_bulk", AsyncMock())
    announce_channel = SimpleNamespace(id=1, send=AsyncMock())
    member = SimpleNamespace(id=42)
    actor = SimpleNamespace(id=10)
    cog._unmute_thread_user = AsyncMock(return_value=True)
    cog._apply_unmute_by_ids = AsyncMock(return_value=(True, False))

    await cog._apply_global_thread_unmute(
        7, [1, 2], member, actor, announce_channel=announce_channel
    )

    cog._unmute_thread_user.assert_awaited_once()
    announce_call = cog._unmute_thread_user.await_args
    assert announce_call.args[0] is announce_channel
    assert announce_call.kwargs["announce"] is True
    assert announce_call.kwargs["scope"] == "global"
    cog._apply_unmute_by_ids.assert_awaited_once()
    assert cog._apply_unmute_by_ids.await_args.args[:3] == (7, 2, 42)


@pytest.mark.parametrize(
    ("show_global_actions", "expected_labels"),
    [
        (
            False,
            {
                "慢速模式",
                "全体通知",
                "修改标题",
                "编辑标签",
                "锁定并归档",
                "删帖",
                "更多功能",
            },
        ),
        (
            True,
            {
                "慢速模式",
                "全体通知",
                "修改标题",
                "编辑标签",
                "锁定并归档",
                "删帖",
                "全贴禁言用户",
                "撤销全贴禁言",
                "更多功能",
            },
        ),
    ],
)
async def test_global_menu_options_follow_advanced_permission(
    show_global_actions, expected_labels
):
    select = SelfManageMainSelect(
        MagicMock(), MagicMock(), show_global_actions=show_global_actions
    )
    options = {option.label: option.description for option in select.options}

    assert set(options) == expected_labels
    if show_global_actions:
        assert options["全贴禁言用户"] == "禁止用户在所有历史帖子发言"
        assert options["撤销全贴禁言"] == "恢复用户所有帖子发言权限"


@pytest.mark.parametrize("allowed", [False, True])
async def test_main_menu_builder_uses_global_action_permission(allowed):
    cog = SimpleNamespace(can_global_thread_action=AsyncMock(return_value=allowed))
    interaction = SimpleNamespace(user=SimpleNamespace(id=10))
    thread = SimpleNamespace(owner_id=10)

    view = await self_manage_ui.build_self_manage_main_menu_view(
        cog, interaction, thread
    )
    labels = {option.label for option in view.children[0].options}

    assert ("全贴禁言用户" in labels) is allowed
    assert ("撤销全贴禁言" in labels) is allowed


@pytest.mark.parametrize(
    ("value", "expected_message"),
    [
        ("global_mute", "只有帖子楼主和管理组可以执行全贴禁言。"),
        ("global_unmute", "只有帖子楼主和管理组可以撤销全贴禁言。"),
    ],
)
async def test_global_menu_rejects_delegate_with_specific_message(
    monkeypatch, value, expected_message
):
    class FakeThread:
        def __init__(self):
            self.id = 50

    monkeypatch.setattr("src.thread_manage.self_manage_ui.discord.Thread", FakeThread)
    thread = FakeThread()
    cog = SimpleNamespace(
        can_global_thread_action=AsyncMock(return_value=False),
        can_manage_thread=AsyncMock(return_value=True),
    )
    select = SelfManageMainSelect(cog, thread)
    select._values = [value]
    interaction = SimpleNamespace(
        channel=thread,
        response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()),
    )

    await select.callback(interaction)

    interaction.response.send_message.assert_awaited_once_with(
        expected_message, ephemeral=True
    )
    cog.can_manage_thread.assert_not_awaited()


async def test_single_thread_helpers_update_only_target_record(monkeypatch):
    cog = make_cog()
    save_record = AsyncMock()
    monkeypatch.setattr("src.thread_manage.cog.db.save_mute_record", save_record)
    guild = SimpleNamespace(id=1)
    channel = SimpleNamespace(id=50, guild=guild, send=AsyncMock())
    member = SimpleNamespace(id=42, mention="<@42>")
    actor = SimpleNamespace(mention="<@10>")
    other_key = (1, 50, 99)
    cog._mute_cache[other_key] = {"muted_until": -1, "violations": 0}

    await cog._mute_thread_user(
        channel,
        member,
        muted_until=-1,
        reason="测试",
        actor=actor,
        announce=True,
    )
    assert cog._mute_cache[(1, 50, 42)]["muted_until"] == -1
    channel.send.assert_awaited_once()
    embed = channel.send.await_args.kwargs["embed"]
    assert embed.title == "🔒 子区禁言"

    removed = await cog._unmute_thread_user(
        channel,
        member,
        actor=actor,
        announce=False,
    )

    assert removed is True
    assert (1, 50, 42) not in cog._mute_cache
    assert cog._mute_cache[other_key]["muted_until"] == -1


async def test_global_unmute_announcement_distinguishes_scope_and_warns_single_layer(
    monkeypatch,
):
    """全贴解除公示须与单帖解除区分，并说明本帖仍有单帖禁言。"""
    cog = make_cog()
    monkeypatch.setattr("src.thread_manage.cog.db.save_mute_record", AsyncMock())
    channel = SimpleNamespace(
        id=50, guild=SimpleNamespace(id=1), send=AsyncMock()
    )
    member = SimpleNamespace(id=42, mention="<@42>")
    actor = SimpleNamespace(mention="<@10>")
    cog._mute_cache[(1, 50, 42)] = {
        "muted_until": -1,
        "global_muted_until": -1,
        "violations": 0,
    }

    await cog._unmute_thread_user(
        channel,
        member,
        actor=actor,
        announce=True,
        scope="global",
    )

    embed = channel.send.await_args.kwargs["embed"]
    assert embed.title == "🔓 全贴禁言"
    assert embed.description == "👤 <@42> 已被解除全贴禁言"
    assert any(field.name == "执行者" and field.value == "<@10>" for field in embed.fields)
    assert any("本帖仍有单帖禁言" in field.value for field in embed.fields)


async def test_single_unmute_rejects_global_layer_without_changing_record(monkeypatch):
    """斜杠单帖解除不能绕过仍有效的全贴禁言层。"""
    class FakeThread:
        def __init__(self):
            self.id = 50
            self.guild = SimpleNamespace(id=1)

    monkeypatch.setattr("src.thread_manage.cog.discord.Thread", FakeThread)
    channel = FakeThread()
    cog = make_cog()
    cog.can_manage_thread = AsyncMock(return_value=True)
    cog._unmute_thread_user = AsyncMock()
    original = {
        "muted_until": -1,
        "global_muted_until": -1,
        "violations": 0,
    }
    cog._mute_cache[(1, 50, 42)] = original.copy()
    interaction = SimpleNamespace(
        channel=channel,
        user=SimpleNamespace(id=30),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    member = SimpleNamespace(id=42, mention="<@42>")

    await ThreadSelfManage.unmute.callback(cog, interaction, member)

    interaction.response.send_message.assert_awaited_once_with(
        "该用户仍处于全贴禁言，请由帖子楼主或管理组使用 /自助管理 撤销全贴禁言。",
        ephemeral=True,
    )
    cog._unmute_thread_user.assert_not_awaited()
    assert cog._mute_cache[(1, 50, 42)] == original


async def test_context_single_unmute_rejects_global_layer(monkeypatch):
    """右键单帖解除同样不能绕过全贴禁言层。"""
    class FakeThread:
        def __init__(self):
            self.id = 50
            self.guild = SimpleNamespace(id=1)

    monkeypatch.setattr("src.thread_manage.cog.discord.Thread", FakeThread)
    channel = FakeThread()
    cog = make_cog()
    cog.can_manage_thread = AsyncMock(return_value=True)
    cog._unmute_thread_user = AsyncMock()
    cog._mute_cache[(1, 50, 42)] = {
        "muted_until": -1,
        "global_muted_until": -1,
        "violations": 0,
    }
    interaction = SimpleNamespace(
        channel=channel,
        user=SimpleNamespace(id=30),
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    member = SimpleNamespace(id=42, mention="<@42>")

    await cog.thread_unmute_user_context_menu(interaction, member)

    interaction.followup.send.assert_awaited_once_with(
        "该用户仍处于全贴禁言，请由帖子楼主或管理组使用 /自助管理 撤销全贴禁言。",
        ephemeral=True,
    )
    cog._unmute_thread_user.assert_not_awaited()


async def test_thread_owner_is_still_blocked_when_mute_record_exists(monkeypatch):
    """防止楼主豁免使全贴禁言记录失效。"""
    cog = make_cog()
    cog.auto_clear_manager = SimpleNamespace(
        can_trigger_auto_clear=lambda _thread_id: False
    )
    cog._is_protected_from_thread_mute = MagicMock(return_value=False)
    cog._is_thread_muted = AsyncMock(return_value=True)
    cog._get_mute_record = MagicMock(return_value={"muted_until": -1})
    cog._increment_violations = AsyncMock(return_value=1)
    cog.bot = SimpleNamespace(config={})
    monkeypatch.setattr("src.thread_manage.cog.dm.send_dm", AsyncMock())

    channel = MagicMock()
    monkeypatch.setattr("src.thread_manage.cog.discord.Thread", type(channel))
    channel.id = 50
    channel.owner_id = 42
    channel.name = "楼主的帖子"
    channel.guild = SimpleNamespace(id=1, get_role=lambda _role_id: None)
    author = SimpleNamespace(id=42, bot=False, roles=[])
    message = SimpleNamespace(
        author=author,
        channel=channel,
        guild=channel.guild,
        delete=AsyncMock(),
    )

    await cog.on_message(message)

    message.delete.assert_awaited_once()


async def test_global_thread_slash_commands_are_registered():
    """关键旧指令和全贴指令都必须注册到 /自助管理 命令组。"""
    for command_name in ("禁言", "授权协管", "全贴禁言", "撤销全贴禁言"):
        assert ThreadSelfManage.self_manage.get_command(command_name) is not None


@pytest.mark.parametrize(
    ("actor_id", "owner_id", "is_admin", "expected"),
    [
        (10, 10, False, True),
        (20, 10, True, True),
        (30, 10, False, False),
    ],
)
async def test_delegate_settings_remain_owner_or_admin_only(
    actor_id, owner_id, is_admin, expected
):
    cog = make_cog()
    cog.is_admin = AsyncMock(return_value=is_admin)
    interaction = SimpleNamespace(user=SimpleNamespace(id=actor_id))
    thread = SimpleNamespace(owner_id=owner_id)

    assert await cog.can_manage_delegate_settings(interaction, thread) is expected


async def test_authorize_delegate_command_still_saves_member(monkeypatch):
    class FakeThread:
        def __init__(self):
            self.id = 50
            self.owner_id = 10
            self.guild = SimpleNamespace(id=1)
            self.send = AsyncMock()

    monkeypatch.setattr("src.thread_manage.cog.discord.Thread", FakeThread)
    channel = FakeThread()
    cog = make_cog()
    cog.can_manage_delegate_settings = AsyncMock(return_value=True)
    cog._load_thread_delegates = AsyncMock(return_value=set())
    cog._save_thread_delegates = AsyncMock()
    interaction = SimpleNamespace(
        channel=channel,
        user=SimpleNamespace(id=10, mention="<@10>"),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    member = SimpleNamespace(id=42, mention="<@42>", bot=False)

    await ThreadSelfManage.add_thread_delegate.callback(cog, interaction, member)

    cog._save_thread_delegates.assert_awaited_once_with(1, 50, {42})
    interaction.response.send_message.assert_awaited_once_with(
        "✅ 已授予 <@42> 本子区协管权限", ephemeral=True
    )
    channel.send.assert_awaited_once()


@pytest.mark.parametrize(
    ("command_name", "expected_message"),
    [
        ("global_mute", "只有帖子楼主和管理组可以执行全贴禁言。"),
        ("global_unmute", "只有帖子楼主和管理组可以撤销全贴禁言。"),
    ],
)
async def test_global_thread_slash_command_rejects_delegate(
    monkeypatch, command_name, expected_message
):
    class FakeThread:
        owner_id = 10

    monkeypatch.setattr("src.thread_manage.cog.discord.Thread", FakeThread)
    cog = make_cog()
    cog.can_global_thread_action = AsyncMock(return_value=False)
    cog.menu_run_global_thread_action = AsyncMock()
    interaction = SimpleNamespace(
        channel=FakeThread(),
        user=SimpleNamespace(id=30),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    member = SimpleNamespace(id=42, bot=False)
    command = getattr(ThreadSelfManage, command_name)

    if command_name == "global_mute":
        await command.callback(cog, interaction, member, None, None)
    else:
        await command.callback(cog, interaction, member)

    interaction.response.send_message.assert_awaited_once_with(
        expected_message, ephemeral=True
    )
    cog.menu_run_global_thread_action.assert_not_awaited()


async def test_global_mute_slash_command_rejects_invalid_duration(monkeypatch):
    class FakeThread:
        owner_id = 10

    monkeypatch.setattr("src.thread_manage.cog.discord.Thread", FakeThread)
    cog = make_cog()
    cog.can_global_thread_action = AsyncMock(return_value=True)
    cog._is_protected_from_thread_mute = MagicMock(return_value=False)
    cog._parse_time = MagicMock(return_value=(-1, ""))
    cog.menu_run_global_thread_action = AsyncMock()
    interaction = SimpleNamespace(
        channel=FakeThread(),
        user=SimpleNamespace(id=10),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    member = SimpleNamespace(id=42, bot=False)

    await ThreadSelfManage.global_mute.callback(
        cog, interaction, member, "bad", "测试"
    )

    interaction.response.send_message.assert_awaited_once_with(
        "❌ 无效时长，请使用m/h/d结尾", ephemeral=True
    )
    cog.menu_run_global_thread_action.assert_not_awaited()


@pytest.mark.parametrize(
    ("member_id", "member_bot", "protected", "expected_message"),
    [
        (10, False, False, "无法禁言自己"),
        (42, True, False, "❌ 不能禁言机器人"),
        (42, False, True, "无法禁言管理组成员"),
    ],
)
async def test_global_mute_slash_command_rejects_protected_targets(
    monkeypatch, member_id, member_bot, protected, expected_message
):
    """防止直接命令绕过菜单已有的目标用户保护。"""
    class FakeThread:
        owner_id = 10

    monkeypatch.setattr("src.thread_manage.cog.discord.Thread", FakeThread)
    cog = make_cog()
    cog.can_global_thread_action = AsyncMock(return_value=True)
    cog._is_protected_from_thread_mute = MagicMock(return_value=protected)
    cog.menu_run_global_thread_action = AsyncMock()
    interaction = SimpleNamespace(
        channel=FakeThread(),
        user=SimpleNamespace(id=10),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    member = SimpleNamespace(id=member_id, bot=member_bot)

    await ThreadSelfManage.global_mute.callback(
        cog, interaction, member, None, None
    )

    interaction.response.send_message.assert_awaited_once_with(
        expected_message, ephemeral=True
    )
    cog.menu_run_global_thread_action.assert_not_awaited()


async def test_global_mute_slash_command_forwards_duration_and_reason(monkeypatch):
    class FakeThread:
        owner_id = 10

    monkeypatch.setattr("src.thread_manage.cog.discord.Thread", FakeThread)
    cog = make_cog()
    cog.can_global_thread_action = AsyncMock(return_value=True)
    cog._is_protected_from_thread_mute = MagicMock(return_value=False)
    cog._parse_time = MagicMock(return_value=(86400, "1天"))
    cog.menu_run_global_thread_action = AsyncMock()
    interaction = SimpleNamespace(
        channel=FakeThread(),
        user=SimpleNamespace(id=10),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    member = SimpleNamespace(id=42, bot=False)
    before = datetime.now() + timedelta(seconds=86395)

    await ThreadSelfManage.global_mute.callback(
        cog, interaction, member, "1d", "跨帖测试"
    )

    cog.menu_run_global_thread_action.assert_awaited_once()
    call = cog.menu_run_global_thread_action.await_args
    assert call.args == (interaction, interaction.channel, member)
    assert call.kwargs["is_mute"] is True
    assert datetime.fromisoformat(call.kwargs["muted_until"]) >= before
    assert call.kwargs["reason"] == "跨帖测试"
    assert call.kwargs["duration_label"] == "1d"


async def test_global_unmute_slash_command_reuses_existing_flow(monkeypatch):
    class FakeThread:
        owner_id = 10

    monkeypatch.setattr("src.thread_manage.cog.discord.Thread", FakeThread)
    cog = make_cog()
    cog.can_global_thread_action = AsyncMock(return_value=True)
    cog.menu_run_global_thread_action = AsyncMock()
    interaction = SimpleNamespace(
        channel=FakeThread(),
        user=SimpleNamespace(id=10),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    member = SimpleNamespace(id=42, bot=False)

    await ThreadSelfManage.global_unmute.callback(cog, interaction, member)

    cog.menu_run_global_thread_action.assert_awaited_once_with(
        interaction,
        interaction.channel,
        member,
        is_mute=False,
    )


async def test_global_mute_collects_records_for_single_commit(monkeypatch):
    """批量禁言通过收集列表一次性提交，而非逐帖 commit。"""
    cog = make_cog()
    bulk_save = AsyncMock()
    monkeypatch.setattr("src.thread_manage.cog.db.save_mute_records_bulk", bulk_save)
    member = SimpleNamespace(id=42)
    actor = SimpleNamespace(id=10)

    affected = await cog._apply_global_thread_mute(7, [1, 2], member, actor)

    assert affected == 2
    bulk_save.assert_awaited_once()
    records = bulk_save.await_args.args[0]
    assert len(records) == 2
    keys = {(g, t, u) for g, t, u, _ in records}
    assert keys == {(7, 1, 42), (7, 2, 42)}
    assert all(rec[3] is not None for rec in records)


async def test_global_unmute_collects_deletes_for_single_commit(monkeypatch):
    """批量撤销收集删除记录并一次性提交，不逐帖 commit。"""
    cog = make_cog()
    bulk_save = AsyncMock()
    monkeypatch.setattr("src.thread_manage.cog.db.save_mute_records_bulk", bulk_save)
    member = SimpleNamespace(id=42)
    actor = SimpleNamespace(id=10)
    cog._mute_cache[(7, 1, 42)] = {
        "muted_until": None,
        "global_muted_until": -1,
        "violations": 0,
    }

    affected, still_single = await cog._apply_global_thread_unmute(
        7, [1], member, actor
    )

    assert (affected, still_single) == (1, 0)
    bulk_save.assert_awaited_once()
    records = bulk_save.await_args.args[0]
    assert len(records) == 1
    assert records[0][:3] == (7, 1, 42)
    assert records[0][3] is None  # 全清后删除记录


async def test_global_action_backgrounds_when_over_threshold(monkeypatch):
    """影响帖子数超过阈值时，转为后台执行而非同步批量处理。"""
    cog = make_cog()
    cog._GLOBAL_BACKGROUND_THRESHOLD = 2
    cog.can_global_thread_action = AsyncMock(return_value=True)
    source = SimpleNamespace(id=100, owner_id=10)
    target = SimpleNamespace(id=42, mention="<@42>")
    guild = SimpleNamespace(
        id=1,
        get_member=lambda user_id: SimpleNamespace(mention="<@10>") if user_id == 10 else None,
    )
    message = SimpleNamespace(edit=AsyncMock())
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=10, mention="<@10>"),
        response=SimpleNamespace(defer=AsyncMock()),
        original_response=AsyncMock(return_value=message),
    )
    cog._build_owner_index = AsyncMock()
    cog._lookup_owned_threads = MagicMock(return_value=[1, 2, 3])
    cog._apply_global_thread_mute = AsyncMock()
    cog._start_background_global_action = AsyncMock()
    monkeypatch.setattr(
        "src.thread_manage.cog.wait_menu_confirm_on_message",
        AsyncMock(return_value=True),
    )

    await cog.menu_run_global_thread_action(
        interaction, source, target, is_mute=True
    )

    cog._start_background_global_action.assert_awaited_once()
    cog._apply_global_thread_mute.assert_not_awaited()


async def test_global_action_sync_when_under_threshold(monkeypatch):
    """影响帖子数未超阈值时，保持同步批量处理。"""
    cog = make_cog()
    cog._GLOBAL_BACKGROUND_THRESHOLD = 2
    cog.can_global_thread_action = AsyncMock(return_value=True)
    source = SimpleNamespace(id=100, owner_id=10)
    target = SimpleNamespace(id=42, mention="<@42>")
    guild = SimpleNamespace(
        id=1,
        get_member=lambda user_id: SimpleNamespace(mention="<@10>") if user_id == 10 else None,
    )
    message = SimpleNamespace(edit=AsyncMock())
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=10, mention="<@10>"),
        response=SimpleNamespace(defer=AsyncMock()),
        original_response=AsyncMock(return_value=message),
    )
    cog._build_owner_index = AsyncMock()
    cog._lookup_owned_threads = MagicMock(return_value=[1])
    cog._apply_global_thread_mute = AsyncMock(return_value=1)
    cog._start_background_global_action = AsyncMock()
    monkeypatch.setattr(
        "src.thread_manage.cog.wait_menu_confirm_on_message",
        AsyncMock(return_value=True),
    )

    await cog.menu_run_global_thread_action(
        interaction, source, target, is_mute=True
    )

    cog._start_background_global_action.assert_not_awaited()
    cog._apply_global_thread_mute.assert_awaited_once()


async def test_notify_background_result_prefers_dm(monkeypatch):
    """后台结果优先私信执行者，成功时不发帖内消息。"""
    cog = make_cog()
    send_dm = AsyncMock()
    monkeypatch.setattr("src.thread_manage.cog.dm.send_dm", send_dm)
    guild = SimpleNamespace(id=1)
    channel = SimpleNamespace(id=10, send=AsyncMock())
    actor = SimpleNamespace(id=42)

    await cog._notify_background_result(guild, channel, actor, "完成")

    send_dm.assert_awaited_once_with(guild, actor, "完成")
    channel.send.assert_not_awaited()


async def test_notify_background_result_falls_back_to_channel(monkeypatch):
    """私信失败时，降级为在帖子内发送结果。"""
    cog = make_cog()
    send_dm = AsyncMock(side_effect=Exception("no dm bot"))
    monkeypatch.setattr("src.thread_manage.cog.dm.send_dm", send_dm)
    guild = SimpleNamespace(id=1)
    channel = SimpleNamespace(id=10, send=AsyncMock())
    actor = SimpleNamespace(id=42)

    await cog._notify_background_result(guild, channel, actor, "完成")

    send_dm.assert_awaited_once()
    channel.send.assert_awaited_once_with("完成")


async def test_background_mute_notifies_actor_on_completion(monkeypatch):
    """后台禁言完成后，将结果通知给执行者。"""
    cog = make_cog()
    notify = AsyncMock()
    cog._notify_background_result = notify
    cog._apply_global_thread_mute = AsyncMock(return_value=5)
    guild = SimpleNamespace(id=1)
    channel = SimpleNamespace(id=10)
    member = SimpleNamespace(id=42, mention="<@42>")
    actor = SimpleNamespace(id=10)

    await cog._run_global_action_in_background(
        guild=guild,
        channel=channel,
        member=member,
        actor=actor,
        is_mute=True,
        thread_ids=[1],
        muted_until=-1,
        reason="测试",
        duration_label="永久",
    )

    notify.assert_awaited_once()
    text = notify.await_args.args[3]
    assert "5 个" in text
