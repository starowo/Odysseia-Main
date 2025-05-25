"""
pytest配置文件，包含测试夹具和共享配置
"""
import pytest
import asyncio
import json
import tempfile
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import discord
from discord.ext import commands


@pytest.fixture(scope="session")
def event_loop():
    """创建事件循环用于异步测试"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_config():
    """模拟配置数据"""
    return {
        "token": "test_token",
        "prefix": "!",
        "status": "watching",
        "status_text": "测试环境",
        "logging": {
            "enabled": True,
            "guild_id": 123456789,
            "channel_id": 987654321,
            "level": "INFO"
        },
        "cogs": {
            "thread_manage": {
                "enabled": True,
                "description": "子区自助管理功能"
            },
            "bot_manage": {
                "enabled": True,
                "description": "机器人管理功能"
            },
            "admin": {
                "enabled": True,
                "description": "管理员功能"
            },
            "verify": {
                "enabled": False,
                "description": "答题验证功能"
            }
        },
        "admins": [123456789]
    }


@pytest.fixture
def temp_config_file(mock_config):
    """创建临时配置文件"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
        json.dump(mock_config, f, indent=4, ensure_ascii=False)
        temp_path = f.name

    # 将临时文件复制到项目根目录作为config.json
    project_root = Path(__file__).parent.parent
    config_path = project_root / "config.json"

    # 备份原配置文件（如果存在）
    backup_path = None
    if config_path.exists():
        backup_path = config_path.with_suffix('.json.backup')
        # 如果备份文件已存在，先删除它
        if backup_path.exists():
            backup_path.unlink()
        config_path.rename(backup_path)

    # 直接写入配置到目标文件，避免编码问题
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(mock_config, f, indent=4, ensure_ascii=False)

    yield config_path

    # 清理：删除临时配置，恢复原配置
    config_path.unlink(missing_ok=True)
    if backup_path and backup_path.exists():
        backup_path.rename(config_path)

    # 删除临时文件
    os.unlink(temp_path)


@pytest.fixture
def mock_bot():
    """创建模拟的Discord机器人实例"""
    bot = AsyncMock(spec=commands.Bot)
    bot.user = MagicMock()
    bot.user.id = 123456789
    bot.user.mention = "<@123456789>"
    bot.latency = 0.1
    bot.extensions = {}
    bot.cogs = {}
    bot.logger = MagicMock()  # 添加logger属性

    # 模拟扩展管理方法
    async def mock_load_extension(name):
        bot.extensions[name] = MagicMock()

    async def mock_unload_extension(name):
        if name in bot.extensions:
            del bot.extensions[name]

    async def mock_reload_extension(name):
        if name in bot.extensions:
            del bot.extensions[name]
        bot.extensions[name] = MagicMock()

    bot.load_extension = AsyncMock(side_effect=mock_load_extension)
    bot.unload_extension = AsyncMock(side_effect=mock_unload_extension)
    bot.reload_extension = AsyncMock(side_effect=mock_reload_extension)
    bot.add_cog = AsyncMock()
    bot.remove_cog = AsyncMock()

    return bot


@pytest.fixture
def mock_guild():
    """创建模拟的Discord服务器实例"""
    guild = MagicMock(spec=discord.Guild)
    guild.id = 123456789
    guild.name = "测试服务器"
    return guild


@pytest.fixture
def mock_user():
    """创建模拟的Discord用户实例"""
    user = MagicMock(spec=discord.User)
    user.id = 987654321
    user.mention = "<@987654321>"
    user.display_name = "测试用户"
    return user


@pytest.fixture
def mock_interaction(mock_user, mock_guild):
    """创建模拟的Discord交互实例"""
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.user = mock_user
    interaction.guild = mock_guild
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.command = MagicMock()
    interaction.command.qualified_name = "test_command"
    return interaction


@pytest.fixture(autouse=True)
def mock_discord_imports():
    """自动模拟Discord相关导入，避免实际连接"""
    with patch('discord.Client'), \
         patch('discord.ext.commands.Bot'), \
         patch('discord.Intents'):
        yield
