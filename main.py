import os
import json
import logging
import traceback # 确保导入 traceback
from datetime import datetime
from pathlib import Path

import discord
from discord.ext import commands
import asyncio
from dotenv import load_dotenv

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
        
        self._last_update_time: datetime = datetime.min # 上次更新时间
        self._update_interval_seconds: int = 5 # 最小更新间隔，例如5秒
        self._update_pending: bool = False # 标记是否有待更新的日志
        self._update_lock = asyncio.Lock() # 添加锁对象
        
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
                if self._initialized and self._message: # 只有当处理器初始化完成且消息对象存在时，才尝试更新 Embed
                    self._update_pending = True # 标记有更新待处理
                    now = datetime.now()
                    # 如果距离上次更新时间超过了最小间隔，或者这是第一次更新
                    if (now - self._last_update_time).total_seconds() >= self._update_interval_seconds:
                        await self._edit_message()
                        self._last_update_time = now
                        self._update_pending = False # 更新完成后清除标记
                    # 只有在没有延迟更新任务在排队时才启动新的延迟更新任务
                    # 这里的 _task.done() 检查的是 _worker 任务本身，不是 _schedule_delayed_update 任务
                    # 更好的方式是依赖 _update_pending 标记和锁
                    elif not self._update_pending: # 确保只启动一个延迟更新任务
                        asyncio.create_task(self._schedule_delayed_update())

            except Exception:
                print(f"更新日志 Embed 失败: {traceback.format_exc()}")
            finally:
                self._queue.task_done()

    # ---- 修复点：添加锁机制，并确保所有内容都在锁内 ----
    async def _schedule_delayed_update(self):
        # async with self._update_lock: 语句将整个方法体包裹起来
        # 这确保了在任何给定时间，只有一个协程能够执行这段代码
        async with self._update_lock: 
            # 等待直到可以再次更新
            time_to_wait = self._update_interval_seconds - (datetime.now() - self._last_update_time).total_seconds()
            if time_to_wait > 0:
                await asyncio.sleep(time_to_wait)
            
            # 再次检查是否有待处理的更新，并且确保消息和初始化状态正确
            if self._update_pending and self._initialized and self._message:
                await self._edit_message()
                self._last_update_time = datetime.now()
                self._update_pending = False
    # ---------------------------------------------------

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
        intents.message_content = True # 需要在 Discord 开发者门户中为机器人开启
        intents.members = True # 需要在 Discord 开发者门户中开启 Guild Members Intent
        intents.presences = True # 需要在 Discord 开发者门户中开启 Guild Presences Intent
        super().__init__(command_prefix=CONFIG.get('prefix', '!'), intents=intents)

    async def on_ready(self):
        # 将 bot.config 赋值，以便 Cog 可以访问
        self.config = CONFIG
        # 将 cog_manager 赋值给 bot 实例，解决循环导入和 Cog 访问问题
        self.cog_manager = cog_manager
        
        # 加载所有启用的Cog
        await self.cog_manager.load_all_enabled()
        
        # 获取主服务器 ID
        main_guild_id = self.config.get('logging', {}).get('guild_id')
        guild = self.get_guild(main_guild_id) # 尝试获取 Guild 对象

        # 在尝试访问 guild 的属性之前，检查 guild 是否为 None
        if guild:
            logger.info(f"同步命令到主服务器: {guild.name} ({guild.id})")
            synced = await self.tree.sync() # 同步全局命令
            synced_guild = await self.tree.sync(guild=guild) # 同步到指定服务器
            logger.info(f"已同步 {len(synced)} 个全局命令")
            logger.info(f"已同步 {len(synced_guild)} 个命令到主服务器")
            for command in synced:
                logger.info(f"已同步全局命令: {command.name}")
            for command in synced_guild:
                logger.info(f"已同步服务器命令: {command.name}")
        else:
            # 如果 guild 为 None，记录一个警告信息，说明无法找到主服务器
            logger.warning(f"无法找到配置中指定的主服务器 (ID: {main_guild_id})，跳过服务器命令同步。请确保机器人已加入该服务器且 'SERVER MEMBERS INTENT' 已开启。")
            # 即使没有主服务器，全局命令仍然可以同步
            synced = await self.tree.sync()
            logger.info(f"已同步 {len(synced)} 个全局命令 (无指定主服务器)")
            for command in synced:
                logger.info(f"已同步全局命令: {command.name}")
            
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
        else: # 处理未知的 status 类型，设置默认值
            self.logger.warning(f"未知的 status 类型: '{status_type}'. 将使用默认的 'watching' 状态。")
            activity = discord.Activity(type=discord.ActivityType.watching, name=status_text)
            
        await self.change_presence(activity=activity)

bot = OdysseiaBot()
bot.logger = logger # 将 logger 赋值给 bot 实例

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
        self.loaded_cogs: set = set() # 存储已加载的 cog 名称 (字符串)
        # 存储 Cog 的模块路径，而不是实例，以便使用 bot.load_extension
        self.cog_module_paths: dict = {
            "thread_manage": "src.thread_manage.cog",
            "bot_manage": "src.bot_manage.cog", # 机器人管理模块
            "admin": "src.admin.cog"
        }
    
    async def load_all_enabled(self):
        """加载所有配置中启用的Cog"""
        for cog_name, cog_config_data in self.config.get('cogs', {}).items():
            if cog_config_data.get('enabled', False):
                if cog_name in self.cog_module_paths:
                    await self.load_cog_by_name(cog_name)
                else:
                    logger.warning(f"模块 {cog_name} 在配置中启用但其模块路径未在 cog_module_paths 中定义")
    
    async def load_cog_by_name(self, cog_name: str):
        """加载指定的Cog模块"""
        if cog_name not in self.cog_module_paths:
            logger.error(f"模块 {cog_name} 不在 cog_module_paths 中定义")
            return False, f"❌ 模块 {cog_name} 未定义"
        
        module_path = self.cog_module_paths[cog_name]
        try:
            logger.info(f"加载扩展 {module_path}") 
            await self.bot.load_extension(module_path)
            self.loaded_cogs.add(cog_name) # 记录已加载的 cog 名称
            logger.info(f"已加载: {cog_name} (扩展: {module_path})")
            return True, f"✅ 已加载: {cog_name}"
        except commands.ExtensionAlreadyLoaded:
            logger.warning(f"扩展 {module_path} (模块 {cog_name}) 已加载，跳过")
            return True, f"⚠️ 模块 {cog_name} 已加载" # 视为成功，因为它已在运行
        except Exception as e:
            logger.error(f"加载 {module_path} 失败: {str(e)}")
            logger.error(traceback.format_exc())
            return False, f"❌ 加载失败: {cog_name} - {str(e)}"
    
    async def unload_cog_by_name(self, cog_name: str):
        """卸载指定的Cog模块"""
        if cog_name not in self.cog_module_paths:
            logger.error(f"模块 {cog_name} 不在 cog_module_paths 中定义")
            return False, f"❌ 模块 {cog_name} 未定义"
        
        module_path = self.cog_module_paths[cog_name]
        try:
            await self.bot.unload_extension(module_path)
            self.loaded_cogs.discard(cog_name)
            logger.info(f"已卸载: {cog_name} (扩展: {module_path})")
            return True, f"✅ 已卸载: {cog_name}"
        except commands.ExtensionNotLoaded:
            logger.warning(f"扩展 {module_path} (模块 {cog_name}) 未加载，跳过卸载")
            return True, f"⚠️ 模块 {cog_name} 未加载" # 视为成功，因为它本来就没加载
        except Exception as e:
            logger.error(f"卸载 {module_path} 失败: {str(e)}")
            return False, f"❌ 卸载失败: {cog_name} - {str(e)}"
    
    # ---- 修复点：将 reload_cog_by_name 移回 CogManager 类内部 ----
    async def reload_cog_by_name(self, cog_name: str):
        """重载指定的Cog模块 (会重新导入模块文件)"""
        if cog_name not in self.cog_module_paths:
            logger.error(f"模块 {cog_name} 不在 cog_module_paths 中定义")
            return False, f"❌ 模块 {cog_name} 未定义"
        
        module_path = self.cog_module_paths[cog_name]
        try:
            logger.info(f"重载扩展 {module_path} (模块 {cog_name})")
            # 先卸载再加载以确保完全刷新
            try:
                await self.bot.unload_extension(module_path)
            except commands.ExtensionNotLoaded:
                pass  # 如果模块未加载，忽略异常
            await self.bot.load_extension(module_path)  # 然后重新加载
            self.loaded_cogs.add(cog_name)  # 确保重载后标记为已加载
            logger.info(f"已重载: {cog_name} (扩展: {module_path})")
            return True, f"✅ 已重载: {cog_name}"
        except Exception as e:
            logger.error(f"重载 {module_path} 失败: {str(e)}")
            logger.error(traceback.format_exc())
            return False, f"❌ 重载失败: {cog_name} - {str(e)}"
    # -----------------------------------------------------------


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
    # bot.logger = logger # 已经在 bot = OdysseiaBot() 后赋值

    # 启动机器人
    token = CONFIG.get('token')
    if not token or token == "在此填入你的Discord Token":
        print("请在config.json中设置有效的Discord Token")
        return

    bot.run(token)


if __name__ == '__main__':
    main()

