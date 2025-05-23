import json
import logging
import traceback
from datetime import datetime
from pathlib import Path

import discord
from discord.ext import commands
import asyncio
from dotenv import load_dotenv

# 移除直接导入Cog模块，改用扩展系统

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
        bot_manage_cog_key = "bot_manage"
        if bot_manage_cog_key in cog_manager.cog_module_paths:
            bot_manage_module_path = cog_manager.cog_module_paths[bot_manage_cog_key]
            if bot_manage_module_path not in self.extensions:
                success, msg = await cog_manager.load_extension(bot_manage_module_path, bot_manage_cog_key)
                if success:
                    logger.info("已加载管理命令模块 (通过 on_ready 确保)")
                else:
                    logger.error(f"无法在 on_ready 中加载管理命令模块: {msg}")

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
bot.config = CONFIG  # 让配置在 bot 实例上可访问

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
        # 使用模块路径而不是实例，支持真正的热重载
        self.cog_module_paths: dict = {
            "thread_manage": "src.thread_manage.cog",
            "bot_manage": "src.bot_manage.cog",
            "admin": "src.admin.cog",
            "verify": "src.verify.cog"
        }

    async def load_all_enabled(self):
        """加载所有配置中启用的Cog"""
        for cog_name, cog_config in self.config.get('cogs', {}).items():
            if cog_config.get('enabled', False):
                if cog_name in self.cog_module_paths:
                    module_path = self.cog_module_paths[cog_name]
                    await self.load_extension(module_path, cog_name)
                else:
                    logger.warning(f"模块 {cog_name} 在配置中启用但未在 cog_module_paths 中定义")

    async def load_extension(self, module_path: str, cog_key: str):
        """加载指定的Cog扩展"""
        try:
            await self.bot.load_extension(module_path)
            logger.info(f"✅ 已加载扩展: {cog_key} (来自 {module_path})")
            return True, f"✅ 已加载: {cog_key}"
        except commands.ExtensionAlreadyLoaded:
            logger.info(f"⚠️ 模块 {cog_key} ({module_path}) 已经加载。")
            return True, f"⚠️ 模块 {cog_key} 已经处于启用状态"
        except commands.ExtensionNotFound:
            logger.error(f"❌ 扩展模块未找到: {module_path} (对于 {cog_key})")
            return False, f"❌ 模块路径未找到: {module_path}"
        except commands.NoEntryPointError:
            logger.error(f"❌ 扩展 {module_path} (对于 {cog_key}) 没有定义 'async def setup(bot)' 函数。")
            return False, f"❌ 模块 {module_path} 缺少 setup 函数。"
        except Exception as e:
            logger.error(f"加载扩展 {cog_key} ({module_path}) 失败: {str(e)}")
            logger.error(traceback.format_exc())
            return False, f"❌ 加载失败: {cog_key} - {str(e)}"

    async def unload_extension(self, module_path: str, cog_key: str):
        """卸载指定的Cog扩展"""
        try:
            await self.bot.unload_extension(module_path)
            logger.info(f"已卸载扩展: {cog_key} (来自 {module_path})")
            return True, f"✅ 已卸载: {cog_key}"
        except commands.ExtensionNotLoaded:
            logger.warning(f"⚠️ 模块 {cog_key} ({module_path}) 未加载，无需卸载。")
            return True, f"⚠️ 模块 {cog_key} 已经处于禁用状态"
        except Exception as e:
            logger.error(f"卸载扩展 {cog_key} ({module_path}) 失败: {str(e)}")
            logger.error(traceback.format_exc())
            return False, f"❌ 卸载失败: {cog_key} - {str(e)}"

    async def reload_extension(self, module_path: str, cog_key: str):
        """重载指定的Cog扩展"""
        try:
            await self.bot.reload_extension(module_path)
            logger.info(f"已重载扩展: {cog_key} (来自 {module_path})")
            return True, f"✅ 已重载: {cog_key}"
        except commands.ExtensionNotLoaded:
            logger.warning(f"模块 {cog_key} ({module_path}) 未加载，尝试加载...")
            return await self.load_extension(module_path, cog_key)
        except commands.ExtensionNotFound:
            logger.error(f"❌ 扩展模块未找到: {module_path} (对于 {cog_key})")
            return False, f"❌ 模块路径未找到: {module_path}"
        except commands.NoEntryPointError:
            logger.error(f"❌ 扩展 {module_path} (对于 {cog_key}) 没有定义 'async def setup(bot)' 函数。")
            return False, f"❌ 模块 {module_path} 缺少 setup 函数。"
        except Exception as e:
            logger.error(f"重载扩展 {cog_key} ({module_path}) 失败: {str(e)}")
            logger.error(traceback.format_exc())
            return False, f"❌ 重载失败: {cog_key} - {str(e)}"

# 创建Cog管理器
cog_manager = CogManager(bot, CONFIG)
bot.cog_manager = cog_manager  # 将 cog_manager 实例附加到 bot 上，方便 cogs 内部访问

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
