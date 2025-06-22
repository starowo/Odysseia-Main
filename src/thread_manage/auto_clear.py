import asyncio
import json
import pathlib
from datetime import datetime, timedelta
from typing import Dict, Optional, Set
import discord
from discord.ext import commands

from .thread_clear import clear_thread_members

class AutoClearTask:
    """自动清理任务状态"""
    def __init__(self, thread_id: int, thread_name: str):
        self.thread_id = thread_id
        self.thread_name = thread_name
        self.status = "准备中"  # 准备中/统计中/清理中/完成/失败
        self.start_time = datetime.now()
        self.progress = {"done": 0, "total": 0}
        self.stage = "init"  # init/stat/clear/done
        self.messages_processed = 0
        self.members_removed = 0
        self.error_msg = None

class AutoClearManager:
    """自动清理管理器"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = bot.logger
        
        # 当前正在执行的任务 {thread_id: AutoClearTask}
        self.active_tasks: Dict[int, AutoClearTask] = {}
        
        # 手动清理正在执行的子区集合
        self.manual_clearing: Set[int] = set()
        
        # 自动清理被禁用的子区集合
        self.disabled_threads: Set[int] = set()
        
        # 日志消息更新任务
        self._log_update_task: Optional[asyncio.Task] = None
        self._log_message: Optional[discord.Message] = None
        
        # 配置缓存
        self._config_cache = {}
        self._config_cache_mtime = None
        
        # 加载禁用列表
        self._load_disabled_threads()

    @property
    def config(self):
        """读取配置文件并缓存"""
        try:
            path = pathlib.Path('config.json')
            mtime = path.stat().st_mtime
            if self._config_cache_mtime != mtime:
                with open(path, 'r', encoding='utf-8') as f:
                    self._config_cache = json.load(f)
                self._config_cache_mtime = mtime
            return self._config_cache
        except Exception as e:
            if self.logger:
                self.logger.error(f"加载配置文件失败: {e}")
            return {}

    def _load_disabled_threads(self):
        """加载被禁用自动清理的子区列表"""
        try:
            path = pathlib.Path("data/auto_clear_disabled.json")
            if path.exists():
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.disabled_threads = set(data.get('disabled_threads', []))
        except Exception as e:
            if self.logger:
                self.logger.error(f"加载自动清理禁用列表失败: {e}")
            self.disabled_threads = set()

    def _save_disabled_threads(self):
        """保存被禁用自动清理的子区列表"""
        try:
            path = pathlib.Path("data")
            path.mkdir(exist_ok=True)
            path = path / "auto_clear_disabled.json"
            data = {"disabled_threads": list(self.disabled_threads)}
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            if self.logger:
                self.logger.error(f"保存自动清理禁用列表失败: {e}")

    def is_thread_disabled(self, thread_id: int) -> bool:
        """检查子区是否被禁用自动清理"""
        return thread_id in self.disabled_threads

    def disable_thread(self, thread_id: int):
        """禁用子区的自动清理"""
        self.disabled_threads.add(thread_id)
        self._save_disabled_threads()

    def enable_thread(self, thread_id: int):
        """启用子区的自动清理"""
        self.disabled_threads.discard(thread_id)
        self._save_disabled_threads()

    def is_clearing_active(self, thread_id: int) -> bool:
        """检查子区是否有正在进行的清理任务（自动或手动）"""
        return thread_id in self.active_tasks or thread_id in self.manual_clearing

    def mark_manual_clearing(self, thread_id: int, active: bool = True):
        """标记手动清理状态"""
        if active:
            self.manual_clearing.add(thread_id)
        else:
            self.manual_clearing.discard(thread_id)

    async def should_auto_clear(self, channel: discord.Thread) -> bool:
        """检查是否应该执行自动清理"""
        # 检查是否被禁用
        if self.is_thread_disabled(channel.id):
            return False
            
        # 检查是否有正在进行的清理任务
        if self.is_clearing_active(channel.id):
            return False
            
        # 检查成员数量
        try:
            members = await channel.fetch_members()
            return len(members) >= 1000
        except Exception:
            return False

    async def start_auto_clear(self, channel: discord.Thread) -> bool:
        """开始自动清理任务"""
        if not await self.should_auto_clear(channel):
            return False
            
        # 创建任务对象
        task = AutoClearTask(channel.id, channel.name)
        self.active_tasks[channel.id] = task
        
        # 启动日志更新任务（如果还没启动）
        if self._log_update_task is None or self._log_update_task.done():
            self._log_update_task = asyncio.create_task(self._log_update_loop())
        
        # 异步执行清理任务
        asyncio.create_task(self._execute_auto_clear(channel, task))
        
        return True

    async def _execute_auto_clear(self, channel: discord.Thread, task: AutoClearTask):
        """执行自动清理任务"""
        try:
            task.status = "正在执行"
            task.stage = "clear"
            
            # 进度回调函数
            async def progress_callback(done: int, total: int, member: Optional[discord.Member], stage: str):
                if stage == "stat_start":
                    task.stage = "stat"
                    task.status = "统计消息"
                elif stage == "stat_progress":
                    task.messages_processed = done
                elif stage == "stat_done":
                    task.messages_processed = done
                elif stage == "start":
                    task.stage = "clear"
                    task.status = "清理中"
                    task.progress = {"done": 0, "total": total}
                elif stage == "progress":
                    task.progress = {"done": done, "total": total}
                elif stage == "done":
                    task.stage = "done"
                    task.status = "完成"
            
            # 执行清理，阈值设为 950（清理50人）
            result = await clear_thread_members(
                channel,
                950,  # 1000 - 50 = 950
                self.bot,
                logger=self.logger,
                progress_cb=progress_callback
            )
            
            task.members_removed = result['removed_inactive'] + result['removed_active']
            task.status = "完成"
            
            if self.logger:
                self.logger.info(
                    f"自动清理完成: {channel.name} (ID: {channel.id}) - "
                    f"移除 {task.members_removed} 人，剩余 {result['final_count']} 人"
                )
                
            # 向子区发送自动清理完成报告
            try:
                summary_embed = discord.Embed(
                    title="自动清理完成 ✅",
                    colour=discord.Colour.green(),
                    description=(
                        f"🔹 已移除未发言成员：**{result['removed_inactive']}** 人\n"
                        f"🔹 已移除低活跃成员：**{result['removed_active']}** 人\n"
                        f"子区当前成员约为 **{result['final_count']}** 人"
                    ),
                    timestamp=datetime.now()
                )
                await channel.send("✅ 子区已自动清理完毕", embed=summary_embed)
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"发送自动清理完成报告失败: {e}")
                
        except Exception as e:
            task.status = "失败"
            task.error_msg = str(e)
            if self.logger:
                self.logger.error(f"自动清理失败: {channel.name} (ID: {channel.id}) - {e}")
        finally:
            # 5分钟后移除任务记录
            await asyncio.sleep(300)
            self.active_tasks.pop(channel.id, None)

    async def _get_log_channel(self) -> Optional[discord.TextChannel]:
        """获取日志频道"""
        try:
            config = self.config
            logging_config = config.get('logging', {})
            if not logging_config.get('enabled', False):
                return None
                
            guild_id = logging_config.get('guild_id')
            channel_id = logging_config.get('channel_id')
            
            if not guild_id or not channel_id:
                return None
                
            guild = self.bot.get_guild(guild_id)
            if not guild:
                return None
                
            channel = guild.get_channel(channel_id)
            return channel if isinstance(channel, discord.TextChannel) else None
        except Exception:
            return None

    async def _ensure_log_message(self) -> Optional[discord.Message]:
        """确保日志消息存在"""
        channel = await self._get_log_channel()
        if not channel:
            return None
            
        # 查找现有的自动清理状态消息
        async for message in channel.history(limit=50):
            if (message.author == self.bot.user and 
                message.embeds and 
                message.embeds[0].title == "🤖 自动清理任务状态"):
                self._log_message = message
                return message
        
        # 创建新的状态消息
        embed = discord.Embed(
            title="🤖 自动清理任务状态",
            description="暂无正在进行的自动清理任务",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        try:
            self._log_message = await channel.send(embed=embed)
            return self._log_message
        except Exception as e:
            if self.logger:
                self.logger.error(f"创建自动清理状态消息失败: {e}")
            return None

    async def _log_update_loop(self):
        """日志更新循环"""
        while True:
            try:
                if not self.active_tasks:
                    # 没有活跃任务时等待更长时间
                    await asyncio.sleep(60)
                    continue
                
                await self._update_log_message()
                await asyncio.sleep(30)  # 每30秒更新一次
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.logger:
                    self.logger.error(f"日志更新循环出错: {e}")
                await asyncio.sleep(30)

    async def _update_log_message(self):
        """更新日志消息"""
        message = await self._ensure_log_message()
        if not message:
            return
            
        if not self.active_tasks:
            # 没有活跃任务
            embed = discord.Embed(
                title="🤖 自动清理任务状态",
                description="暂无正在进行的自动清理任务",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
        else:
            # 有活跃任务
            embed = discord.Embed(
                title="🤖 自动清理任务状态",
                description=f"当前有 {len(self.active_tasks)} 个自动清理任务正在进行",
                color=discord.Color.orange(),
                timestamp=datetime.now()
            )
            
            # 添加每个任务的详细信息
            for task in list(self.active_tasks.values()):
                duration = (datetime.now() - task.start_time).total_seconds()
                duration_str = f"{int(duration//60)}分{int(duration%60)}秒"
                
                # 构建状态描述
                if task.stage == "stat":
                    progress_desc = f"📊 统计阶段: 已处理 {task.messages_processed} 条消息"
                elif task.stage == "clear":
                    if task.progress["total"] > 0:
                        pct = int(task.progress["done"] / task.progress["total"] * 100)
                        progress_desc = f"🧹 清理阶段: {task.progress['done']}/{task.progress['total']} ({pct}%)"
                    else:
                        progress_desc = "🧹 清理阶段: 准备中"
                elif task.stage == "done":
                    progress_desc = f"✅ 已完成: 移除了 {task.members_removed} 名成员"
                else:
                    progress_desc = f"⏳ {task.status}"
                
                if task.error_msg:
                    progress_desc = f"❌ 失败: {task.error_msg}"
                
                embed.add_field(
                    name=f"📝 {task.thread_name}",
                    value=f"{progress_desc}\n⏱️ 运行时间: {duration_str}",
                    inline=False
                )
        
        try:
            await message.edit(embed=embed)
        except Exception as e:
            if self.logger:
                self.logger.error(f"更新自动清理状态消息失败: {e}") 