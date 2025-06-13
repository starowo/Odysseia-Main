import os
import json
import logging
import traceback
from datetime import datetime
from pathlib import Path

import discord
from discord.ext import commands
import asyncio
from dotenv import load_dotenv

import src.thread_manage.cog as thread_manage
import src.bot_manage.cog as bot_manage
import src.admin.cog as admin
import src.verify.cog as verify
import src.misc.cog as misc
import src.event.cog as event
import src.anonymous_feedback.cog as anonymous_feedback
import src.sync.cog as sync
import src.license.cog as license_auto

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

# 日志设置
class SingleEmbedLogHandler(logging.Handler):
    """将日志集中写入指定频道中的同一个 Embed 消息 (最多 100 行)"""

    def __init__(self, bot: commands.Bot, guild_id: int, channel_id: int, max_lines: int = 100):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.max_lines = max_lines

        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task = None
        self._message: discord.Message = None
        self._lines: list[str] = []
        self._initialized = False
        self._last_update = 0
        self._update_interval = 10

    async def setup(self):
        self._task = asyncio.create_task(self._worker())

    def emit(self, record: logging.LogRecord):
        line = self.format(record)
        asyncio.create_task(self._queue.put(line))

    async def _worker(self):
        await self.bot.wait_until_ready()
        await self._ensure_message()
        self._initialized = True

        while True:
            try:
                lines_batch = []
                line = await self._queue.get()
                lines_batch.append(line)
                
                try:
                    while len(lines_batch) < 10:
                        extra_line = self._queue.get_nowait()
                        lines_batch.append(extra_line)
                except asyncio.QueueEmpty:
                    pass
                
                for log_line in lines_batch:
                    ts = datetime.now().strftime('%H:%M:%S')
                    self._lines.append(f"[{ts}] {log_line}")
                    if len(self._lines) > self.max_lines:
                        self._lines = self._lines[-self.max_lines:]
                
                current_time = asyncio.get_event_loop().time()
                if (self._initialized and self._message and 
                    current_time - self._last_update >= self._update_interval):
                    await self._edit_message()
                    self._last_update = current_time
                    
                for _ in lines_batch:
                    self._queue.task_done()
                    
                await asyncio.sleep(2)
                
            except Exception as e:
                print(f"日志处理器错误: {e}")
                try:
                    for _ in lines_batch:
                        self._queue.task_done()
                except:
                    pass
                await asyncio.sleep(5)

    async def _ensure_message(self):
        guild = self.bot.get_guild(self.guild_id)
        channel = guild.get_channel(self.channel_id) if guild else None
        if channel is None:
            raise RuntimeError("无法找到日志频道，请检查配置 guild_id / channel_id")

        pinned = await channel.pins()
        for msg in pinned:
            if msg.author == self.bot.user and msg.embeds and msg.embeds[0].title == 'Bot Logs':
                self._message = msg
                break

        if self._message is None:
            embed = discord.Embed(title='Bot Logs', description='(暂无日志)', color=discord.Color.green())
            self._message = await channel.send(embed=embed)
            try:
                await self._message.pin()
            except discord.HTTPException:
                pass

    async def _edit_message(self):
        if self._message is None:
            return

        try:
            desc = "```\n" + "\n".join(self._lines[-50:]) + "\n```"
            if len(desc) > 4000:
                desc = desc[-4000:]

            embed = self._message.embeds[0].copy()
            embed.description = desc
            embed.timestamp = datetime.now()

            await self._message.edit(embed=embed)
        except discord.HTTPException as e:
            if e.code == 30046:
                print("日志消息编辑次数超限，创建新消息")
                try:
                    await self._message.unpin()
                except:
                    pass
                
                channel = self._message.channel
                embed = discord.Embed(title='Bot Logs', description='```\n' + '\n'.join(self._lines[-50:]) + '\n```', color=discord.Color.green())
                embed.timestamp = datetime.now()
                
                self._message = await channel.send(embed=embed)
                try:
                    await self._message.pin()
                except:
                    pass
            else:
                print(f"更新日志消息失败: {e}")
        except Exception as e:
            print(f"更新日志消息时出现未知错误: {e}")

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

class CogManager:
    """Cog管理器，处理Cog的加载、卸载和重载"""
    def __init__(self, bot: commands.Bot, config: dict):
        self.bot: commands.Bot = bot
        self.config: dict = config
        self.loaded_cogs: set = set()
        self.cog_map: dict = {
            "thread_manage": thread_manage.ThreadSelfManage(bot),
            "bot_manage": bot_manage.BotManageCommands(bot),
            "admin": admin.AdminCommands(bot),
            "verify": verify.VerifyCommands(bot),
            "misc": misc.MiscCommands(bot),
            "event": event.EventCommands(bot),
            "anonymous_feedback": anonymous_feedback.AnonymousFeedbackCog(bot),
            "sync": sync.ServerSyncCommands(bot),
            "license": license_auto.LicenseCog(bot)
        }
    
    async def load_all_enabled(self):
        """加载所有配置中启用的Cog"""
        for cog_name, cog_config in self.config.get('cogs', {}).items():
            if cog_config.get('enabled', False):
                if cog_name in self.cog_map:
                    await self.load_cog(self.cog_map[cog_name])
                else:
                    logger.warning(f"模块 {cog_name} 在配置中启用但不在cog_map中")
    
    async def load_cog(self, cog):
        """加载指定的Cog"""
        try:
            await self.bot.add_cog(cog)
            self.loaded_cogs.add(cog)
            cog_name = getattr(cog, 'name', type(cog).__name__)
            logger.info(f"已加载模块: {cog_name}")
        except Exception as e:
            cog_name = getattr(cog, 'name', type(cog).__name__)
            logger.error(f"加载模块 {cog_name} 失败: {e}")
    
    async def unload_cog(self, cog):
        """卸载指定的Cog"""
        try:
            await self.bot.remove_cog(cog.qualified_name)
            self.loaded_cogs.discard(cog)
            cog_name = getattr(cog, 'name', type(cog).__name__)
            logger.info(f"已卸载模块: {cog_name}")
        except Exception as e:
            cog_name = getattr(cog, 'name', type(cog).__name__)
            logger.error(f"卸载模块 {cog_name} 失败: {e}")
    
    async def reload_cog(self, cog):
        """重载指定的Cog"""
        try:
            cog_name = getattr(cog, 'name', type(cog).__name__)
            await self.unload_cog(cog)
            await self.load_cog(cog)
            logger.info(f"已重载模块: {cog_name}")
        except Exception as e:
            cog_name = getattr(cog, 'name', type(cog).__name__)
            logger.error(f"重载模块 {cog_name} 失败: {e}")

cog_manager = CogManager(bot, CONFIG)

@bot.event
async def on_command_error(ctx, error):
    """全局错误处理"""
    if isinstance(error, commands.CommandNotFound):
        return
    
    logger.error(f"命令错误: {error}")
    if hasattr(ctx, 'send'):
        await ctx.send(f"执行命令时发生错误: {error}")

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
