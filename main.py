import json
import logging
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from src.bot_manage.cogUtils import CogManager
from src.utils import dm
from src.utils.log import SingleEmbedLogHandler

# 加载环境变量
load_dotenv()

# 配置加载
def load_config():
    """加载配置文件"""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"加载配置文件失败: {e}")
        return None

CONFIG = load_config()
if not CONFIG:
    print("无法加载配置，程序终止")
    exit(1)


# 设置日志记录器
logger = logging.getLogger('bot')
logger.setLevel(logging.INFO)

# 添加控制台处理器
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(console_handler)

# 添加文件处理器
log_dir = Path('logs')
log_dir.mkdir(exist_ok=True)
file_handler = logging.FileHandler(
    filename=log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.log",
    encoding='utf-8',
    mode='a'
)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

# Discord日志处理器（如果配置了）
_discord_handler = None
if CONFIG.get('logging', {}).get('enabled', False):
    guild_id = CONFIG['logging'].get('guild_id')
    channel_id = CONFIG['logging'].get('channel_id')
    
    if guild_id and channel_id:
        _discord_handler = SingleEmbedLogHandler(None, guild_id, channel_id)
        _discord_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        
        level_str = CONFIG['logging'].get('level', 'INFO').upper()
        level_map = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
            'CRITICAL': logging.CRITICAL
        }
        
        _discord_handler.setLevel(level_map.get(level_str, logging.INFO))

class OdysseiaBot(commands.Bot):
    logger: logging.Logger

    def __init__(self, **kwargs):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        # 由于本机器人只使用斜杠命令，前缀设置为默认值即可
        super().__init__(command_prefix='!', intents=intents, **kwargs)

    async def on_ready(self):
        # 设置Discord日志处理器的bot引用
        global _discord_handler
        if _discord_handler:
            _discord_handler.bot = self
            await _discord_handler.setup()
            logger.addHandler(_discord_handler)
        
        guild = self.get_guild(CONFIG.get('logging', {}).get('guild_id'))
        
        await cog_manager.load_all_enabled()
        
        if "BotManageCommands" not in self.cogs:
            bot_manage_cog = cog_manager.cog_map["bot_manage"]
            await cog_manager.load_cog(bot_manage_cog)
            logger.info("已加载管理命令模块")

        if guild:
            logger.info(f"同步命令到主服务器: {guild.name} ({guild.id})")
            synced = await self.tree.sync()
            synced_guild = await self.tree.sync(guild=guild)
            logger.info(f"已同步 {len(synced)} 个全局命令")
            logger.info(f"已同步 {len(synced_guild)} 个命令到主服务器")
            for command in synced:
                logger.info(f"已同步全局命令: {command.name}")
            for command in synced_guild:
                logger.info(f"已同步服务器命令: {command.name}")
        else:
            logger.warning("未找到主服务器配置，跳过命令同步")

        # 修复兼容现有config
        dm_bot_token = CONFIG.get('dm_bot_token')
        if dm_bot_token and dm_bot_token != CONFIG.get('token'):
            await dm.init_dm_bot(dm_bot_token)
        else:
            dm.dm_bot = self

        status_type = CONFIG.get('status', 'watching').lower()
        status_text = CONFIG.get('status_text', '子区里的一切')
            
        activity = None
        if status_type == 'playing':
            activity = discord.Game(name=status_text)
        elif status_type == 'watching':
            activity = discord.Activity(type=discord.ActivityType.watching, name=status_text)
        elif status_type == 'listening':
            activity = discord.Activity(type=discord.ActivityType.listening, name=status_text)
            
        if activity:
            await self.change_presence(activity=activity)

# 从配置中获取代理
proxy_url = CONFIG.get("proxy")

# 根据是否有代理来初始化Bot
if proxy_url:
    logger.info(f"检测到代理配置，将通过 {proxy_url} 初始化机器人")
    bot = OdysseiaBot(proxy=proxy_url)
else:
    logger.info("未配置代理，直接初始化机器人")
    bot = OdysseiaBot()

bot.logger = logger

cog_manager = CogManager(bot, CONFIG)

@bot.event
async def on_command_error(ctx, error):
    """全局错误处理"""
    if isinstance(error, commands.CommandNotFound):
        return
    
    logger.error(f"命令错误: {error}")
    if hasattr(ctx, 'send'):
        await ctx.send(f"执行命令时发生错误: {error}")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """全局应用命令错误处理"""
    command_name = interaction.command.name if interaction.command else "未知命令"
    if isinstance(error, app_commands.errors.CheckFailure):
        # 此时 auth.py 中的检查函数已经发送了临时消息，所以这里不需要再发送
        return

    # 对于其他错误，记录日志并回复用户
    logger.error(f"应用命令 '{command_name}' 发生未处理的错误: {error}", exc_info=True)
    
    # 尝试回复用户，防止交互卡死
    try:
        if interaction.response.is_done():
            await interaction.followup.send("❌ 执行命令时发生了一个未知错误，请联系管理员。", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 执行命令时发生了一个未知错误，请联系管理员。", ephemeral=True)
    except Exception as e:
        logger.error(f"在发送错误消息时再次发生错误: {e}")


def main():
    """主函数"""
    try:
        bot.run(CONFIG['token'])
    except KeyboardInterrupt:
        logger.info("机器人被手动停止")
    except Exception as e:
        logger.error(f"机器人运行出错: {e}")

if __name__ == "__main__":
    main()
