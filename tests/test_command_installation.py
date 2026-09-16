"""验证真实命令注册数据及个人安装调用的全局拦截。"""

import importlib
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from discord import Intents, Interaction
from discord.ext.commands import Bot, Cog
import pytest

from src.thread_manage.cog import ThreadSelfManage
from src.utils.command_tree import GuildOnlyCommandTree


@pytest.fixture
def command_bot():
    # 保留真实类型，避免 conftest 的全局 Bot mock 隐藏注册行为。
    return Bot(
        command_prefix="!", intents=Intents.none(), tree_cls=GuildOnlyCommandTree
    )


def test_all_cog_commands_register_for_guild_install_only(command_bot):
    source_root = Path(__file__).resolve().parents[1] / "src"
    for cog_path in sorted(source_root.glob("*/cog.py")):
        module = importlib.import_module(f"src.{cog_path.parent.name}.cog")
        for _, cog_class in inspect.getmembers(module, inspect.isclass):
            if cog_class.__module__ != module.__name__ or not issubclass(cog_class, Cog):
                continue
            for command in cog_class.__cog_app_commands__:
                command_bot.tree.add_command(command)

    # 使用实际的右键菜单注册流程，不启动 Cog 的后台任务。
    thread_cog = ThreadSelfManage.__new__(ThreadSelfManage)
    thread_cog.bot = command_bot
    thread_cog._register_context_menus()

    registered = command_bot.tree.get_commands()
    assert any(command.name == "答疑组永封" for command in registered)
    assert any(command.name == "子区禁言" for command in registered)
    for command in registered:
        payload = command.to_dict(command_bot.tree)
        assert payload["integration_types"] == [0], command.name
        assert payload["contexts"] == [0], command.name


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("guild_id", "integration_owners", "allowed"),
    [
        (123, {0: 123}, True),
        (123, {0: 123, 1: 456}, True),
        (123, {1: 456}, False),
        (123, {}, False),
        (123, {0: 789}, False),
        (None, {1: 456}, False),
        (None, {0: 123}, False),
    ],
)
async def test_installation_check(command_bot, guild_id, integration_owners, allowed):
    interaction = SimpleNamespace(
        guild_id=guild_id,
        _integration_owners=integration_owners,
        response=AsyncMock(),
    )
    interaction.is_guild_integration = lambda: Interaction.is_guild_integration(interaction)

    assert await command_bot.tree.interaction_check(interaction) is allowed
    if allowed:
        interaction.response.send_message.assert_not_awaited()
    else:
        interaction.response.send_message.assert_awaited_once_with(
            "❌ 此命令只能通过服务器安装的机器人在服务器中使用", ephemeral=True
        )
