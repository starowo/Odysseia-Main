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

# 加载环境变量
load_dotenv()

# ---- 配置加载 ----
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

# ---- 日志设置 ----
# ── 日志处理器：单 Embed 维护 ──────────────────────────────

class SingleEmbedLogHandler(logging.Handler):
    """将日志集中写入指定频道中的同一个 Embed 消息 (最多 100 行)。"""

    def __init__(self, bot: commands.Bot, guild_id: int, channel_id: int, max_lines: int = 100):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.max_lines = max_lines

        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task = None

        self._message: discord.Message = None  # 日志 Embed 消息
        self._lines: list[str] = []  # 当前行缓存
        self._initialized = False  # 标记是否已初始化频道和消息

    async def setup(self):
        # 启动后台任务
        self._task = asyncio.create_task(self._worker())

    # -------- handler 接口 --------
    def emit(self, record: logging.LogRecord):
        # 格式化单行日志
        line = self.format(record)
        # 即使bot还没准备好，也将日志放入队列中缓存
        asyncio.create_task(self._queue.put(line))

    # -------- 后台任务 --------
    async def _worker(self):
        await self.bot.wait_until_ready()

        # 初始化频道和消息 (仅在bot准备好后进行)
        await self._ensure_message()
        self._initialized = True

        while True:
            line: str = await self._queue.get()
            try:
                # 添加行并裁剪
                ts = datetime.now().strftime('%H:%M:%S')
                self._lines.append(f"[{ts}] {line}")
                if len(self._lines) > self.max_lines:
                    self._lines = self._lines[-self.max_lines :]

                # 仅当已初始化时才更新消息
                if self._initialized and self._message:
                    await self._edit_message()
            except Exception:
                print(f"更新日志 Embed 失败: {traceback.format_exc()}")
            finally:
                self._queue.task_done()

    # -------- 私有工具 --------
    async def _ensure_message(self):
        guild = self.bot.get_guild(self.guild_id)
        channel = guild.get_channel(self.channel_id) if guild else None
        if channel is None:
            raise RuntimeError("无法找到日志频道，请检查配置 guild_id / channel_id")

        # 寻找已固定的日志消息 (标题为 'Bot Logs')
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

        desc = "```\n" + "\n".join(self._lines) + "\n```"
        # discord embed description 最大 4096 字符
        if len(desc) > 4000:
            # 超长时截断开头
            desc = desc[-4000:]

        embed = self._message.embeds[0]
        embed.description = desc
        embed.timestamp = datetime.now()

        await self._message.edit(embed=embed)

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

class OdysseiaBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix=CONFIG.get('prefix', '!'), intents=intents)
    # on_ready sync all commands to main guild

    async def on_ready(self):
        guild = self.get_guild(CONFIG.get('logging', {}).get('guild_id'))
        # 加载所有启用的Cog
        await cog_manager.load_all_enabled()

        # 确保bot_manage模块始终加载，因为它包含bot管理命令
        if "bot_manage" not in self.cogs:
            bot_manage_cog = cog_manager.cog_map["bot_manage"]
            await cog_manager.load_cog(bot_manage_cog)
            logger.info("已加载管理命令模块")

        logger.info(f"同步命令到主服务器: {guild.name} ({guild.id})")
        if guild:
            synced = await self.tree.sync()
            synced_guild = await self.tree.sync(guild=guild)
            logger.info(f"已同步 {len(synced)} 个全局命令")
            logger.info(f"已同步 {len(synced_guild)} 个命令到主服务器")
            for command in synced:
                logger.info(f"已同步全局命令: {command.name}")
            for command in synced_guild:
                logger.info(f"已同步服务器命令: {command.name}")

        # 设置机器人状态
        status_type = CONFIG.get('status', 'watching').lower()
        status_text = CONFIG.get('status_text', '子区里的一切')

        activity = None
        if status_type == 'playing':
            activity = discord.Game(name=status_text)
        elif status_type == 'watching':
            activity = discord.Activity(type=discord.ActivityType.watching, name=status_text)
        elif status_type == 'listening':
            activity = discord.Activity(type=discord.ActivityType.listening, name=status_text)
        else:
            # 处理未知的状态类型，设置默认状态并记录警告
            logger.warning(f"未知的状态类型 '{status_type}'，使用默认状态 'watching'")
            activity = discord.Activity(type=discord.ActivityType.watching, name=status_text)

        if activity:
            await self.change_presence(activity=activity)

bot = OdysseiaBot()
bot.logger = logger

# ---- 添加Discord日志处理器 ----
# 全局变量用于存储处理器实例
_discord_handler = None

# 提前创建Discord日志处理器
if CONFIG.get('logging', {}).get('enabled', False):
    guild_id = CONFIG['logging']['guild_id']
    channel_id = CONFIG['logging']['channel_id']

    _discord_handler = SingleEmbedLogHandler(bot, guild_id, channel_id)
    _discord_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

    # 设置日志级别
    level_str = CONFIG['logging'].get('level', 'INFO').upper()
    level_map = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR,
        'CRITICAL': logging.CRITICAL
    }
    _discord_handler.setLevel(level_map.get(level_str, logging.INFO))

    # 提前添加到logger，这样在bot ready前的日志也会被缓存
    logger.addHandler(_discord_handler)

    @bot.listen('on_ready')
    async def setup_logging_on_ready():
        """当机器人准备就绪时，设置Discord日志处理器"""
        global _discord_handler
        if _discord_handler is not None:
            # 只在ready时设置handler的频道和消息
            await _discord_handler.setup()
            logger.info(f"{bot.user}已连接到Discord! 机器人ID: {bot.user.id}")

# ---- Cog管理 ----
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
            "verify": verify.VerifyCommands(bot)
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
            await cog.on_ready()
            return True, f"✅ 已加载: {cog.name}"
        except Exception as e:
            logger.error(f"加载 {cog.name} 失败: {str(e)}")
            # traceback
            logger.error(traceback.format_exc())
            return False, f"❌ 加载失败: {cog.name} - {str(e)}"

    async def unload_cog(self, cog):
        """卸载指定的Cog"""
        try:
            await self.bot.remove_cog(cog.name)
            self.loaded_cogs.discard(cog)
            logger.info(f"已卸载: {cog.name}")
            return True, f"✅ 已卸载: {cog.name}"
        except Exception as e:
            logger.error(f"卸载 {cog.name} 失败: {str(e)}")
            return False, f"❌ 卸载失败: {cog.name} - {str(e)}"

    async def reload_cog(self, cog):
        """重载指定的Cog"""
        try:
            # 先卸载再加载
            await self.unload_cog(cog)
            await self.load_cog(cog)
            logger.info(f"已重载: {cog.name}")
            return True, f"✅ 已重载: {cog.name}"
        except Exception as e:
            logger.error(f"重载 {cog.name} 失败: {str(e)}")
            # 尝试重新加载
            try:
                await self.bot.add_cog(cog)
                self.loaded_cogs.add(cog)
                return True, f"✅ 重载失败但已重新加载: {cog.name}"
            except Exception as reload_error:
                logger.error(f"重新加载 {cog.name} 也失败: {str(reload_error)}")
                return False, f"❌ 重载失败: {cog.name} - {str(e)}"

# 创建Cog管理器
cog_manager = CogManager(bot, CONFIG)

@bot.event
async def on_command_error(ctx, error):
    """全局命令错误处理"""
    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ 你没有权限执行此命令")
        return

    logger.error(f"命令 {ctx.command} 执行时出错: {error}")
    await ctx.send(f"❌ 命令执行时出错: {str(error)}")


# ---- 运行机器人 ----
def main():
    """主要运行功能"""
    bot.logger = logger

    # 启动机器人
    token = CONFIG.get('token')
    if not token or token == "在此填入你的Discord Token":
        print("请在config.json中设置有效的Discord Token")
        return

    bot.run(token)


if __name__ == '__main__':
    main()
