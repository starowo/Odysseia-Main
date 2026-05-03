import asyncio
import json
import pathlib
from datetime import datetime, timedelta
from typing import Dict, Optional, Set, Tuple
import discord
from discord.ext import commands

from . import db
from .thread_clear import clear_thread_members, rebuild_thread_cache

_CHECK_COOLDOWN_SECONDS = 300
_POST_CLEAR_COOLDOWN_SECONDS = 60
_TASK_TIMEOUT_SECONDS = 1800


class AutoClearTask:
    """自动清理任务状态"""
    def __init__(self, thread_id: int, thread_name: str):
        self.thread_id = thread_id
        self.thread_name = thread_name
        self.status = "准备中"
        self.start_time = datetime.now()
        self.progress = {"done": 0, "total": 0}
        self.stage = "init"
        self.messages_processed = 0
        self.members_removed = 0
        self.error_msg = None
        self._asyncio_task: Optional[asyncio.Task] = None


class AutoClearManager:
    """自动清理管理器"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = bot.logger

        self.active_tasks: Dict[int, AutoClearTask] = {}
        self.manual_clearing: Set[int] = set()
        self.disabled_threads: Set[int] = set()

        # {thread_id: (last_check_time, cooldown_seconds)}
        # 清理成功后使用短冷却，普通检查使用默认冷却
        self._check_cooldowns: Dict[int, Tuple[datetime, int]] = {}

        self._log_update_task: Optional[asyncio.Task] = None
        self._log_message: Optional[discord.Message] = None

        self._config_cache = {}
        self._config_cache_mtime = None

        self._initialized = False

    async def initialize(self) -> None:
        """异步初始化：加载禁用列表。在 on_ready 中调用。"""
        if self._initialized:
            return
        self._initialized = True
        self.disabled_threads = await self._load_disabled_threads()

    # ── 配置 ──────────────────────────────────────────────

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

    # ── 禁用列表 ─────────────────────────────────────────

    async def _load_disabled_threads(self) -> set[int]:
        try:
            return await db.load_disabled_threads()
        except Exception as e:
            if self.logger:
                self.logger.error(f"加载自动清理禁用列表失败: {e}")
            return set()

    async def _save_disabled_threads(self):
        try:
            await db.save_disabled_threads(self.disabled_threads)
        except Exception as e:
            if self.logger:
                self.logger.error(f"保存自动清理禁用列表失败: {e}")

    def is_thread_disabled(self, thread_id: int) -> bool:
        return thread_id in self.disabled_threads

    async def disable_thread(self, thread_id: int):
        self.disabled_threads.add(thread_id)
        await self._save_disabled_threads()

    async def enable_thread(self, thread_id: int):
        self.disabled_threads.discard(thread_id)
        await self._save_disabled_threads()

    # ── 状态查询 ─────────────────────────────────────────

    def is_clearing_active(self, thread_id: int) -> bool:
        """检查子区是否有正在进行的清理任务（自动或手动）。
        已完成或已失败的自动清理任务不算在内。
        """
        if thread_id in self.manual_clearing:
            return True
        task = self.active_tasks.get(thread_id)
        if task is None:
            return False
        return task.stage != "done" and task.status not in ("完成", "失败")

    def mark_manual_clearing(self, thread_id: int, active: bool = True):
        if active:
            self.manual_clearing.add(thread_id)
        else:
            self.manual_clearing.discard(thread_id)

    def can_trigger_auto_clear(self, thread_id: int) -> bool:
        """纯同步快速检查，决定是否值得做一次 fetch_members。
        不涉及任何 API 调用，可安全在 on_message 中高频使用。
        """
        if self.is_thread_disabled(thread_id):
            return False
        if self.is_clearing_active(thread_id):
            return False
        entry = self._check_cooldowns.get(thread_id)
        if entry:
            last_check, cooldown = entry
            if (datetime.now() - last_check).total_seconds() < cooldown:
                return False
        return True

    def cleanup_stale_tasks(self):
        """清理超时未完成的任务记录和过期冷却"""
        now = datetime.now()

        stale_ids = [
            tid for tid, task in self.active_tasks.items()
            if (now - task.start_time).total_seconds() > _TASK_TIMEOUT_SECONDS + 120
        ]
        for tid in stale_ids:
            task = self.active_tasks.pop(tid, None)
            if task:
                if task._asyncio_task and not task._asyncio_task.done():
                    task._asyncio_task.cancel()
                if self.logger:
                    self.logger.warning(f"强制清理过期任务: {task.thread_name} (ID: {tid})")

        expired_cooldowns = [
            tid for tid, (t, cd) in self._check_cooldowns.items()
            if (now - t).total_seconds() > cd * 2
        ]
        for tid in expired_cooldowns:
            del self._check_cooldowns[tid]

    # ── 中断 / 重建 ─────────────────────────────────────

    def cancel_task(self, thread_id: int) -> bool:
        """中断指定子区的清理任务。返回 True 表示成功取消。"""
        task = self.active_tasks.get(thread_id)
        if task is None:
            return False
        if task._asyncio_task and not task._asyncio_task.done():
            task._asyncio_task.cancel()
        task.status = "已取消"
        task.stage = "done"
        task.error_msg = "被手动中断"
        return True

    async def rebuild_and_clear(self, channel: discord.Thread, limit: int = 10000) -> bool:
        """重建缓存并执行清理。

        1. 以当前最新消息为锚点写入 meta
        2. 从新到旧统计最近 limit 条消息
        3. 重写数据库缓存
        4. 执行一次清理
        返回 True 表示成功启动重建清理。
        """
        if self.is_clearing_active(channel.id):
            return False

        task = AutoClearTask(channel.id, channel.name)
        self.active_tasks[channel.id] = task
        self._check_cooldowns[channel.id] = (datetime.now(), _CHECK_COOLDOWN_SECONDS)

        if self._log_update_task is None or self._log_update_task.done():
            self._log_update_task = asyncio.create_task(self._log_update_loop())

        task._asyncio_task = asyncio.create_task(
            self._execute_rebuild_and_clear(channel, task, limit)
        )
        return True

    async def _execute_rebuild_and_clear(self, channel: discord.Thread, task: AutoClearTask, limit: int):
        """执行重建缓存并清理的完整流程。"""
        try:
            task.status = "重建缓存中"
            task.stage = "rebuild"

            async def rebuild_progress_cb(done: int, _total: int, _member, stage: str):
                if stage == "stat_start":
                    task.stage = "rebuild"
                    task.status = "重建缓存中"
                elif stage == "stat_progress":
                    task.messages_processed = done
                elif stage == "stat_done":
                    task.messages_processed = done

            # 重建缓存
            await rebuild_thread_cache(
                channel, limit,
                logger=self.logger,
                progress_cb=rebuild_progress_cb,
            )

            if self.logger:
                self.logger.info(
                    f"重建缓存完成: {channel.name} (ID: {channel.id}) - 统计 {task.messages_processed} 条消息"
                )

            # 执行清理
            task.status = "正在执行"
            task.stage = "clear"

            async def clear_progress_cb(done: int, total: int, member, stage: str):
                if stage == "start":
                    task.stage = "clear"
                    task.status = "清理中"
                    task.progress = {"done": 0, "total": total}
                elif stage == "progress":
                    task.progress = {"done": done, "total": total}
                elif stage == "done":
                    task.stage = "done"
                    task.status = "完成"

            result = await asyncio.wait_for(
                clear_thread_members(
                    channel, 950, self.bot,
                    logger=self.logger,
                    progress_cb=clear_progress_cb,
                ),
                timeout=_TASK_TIMEOUT_SECONDS,
            )

            task.members_removed = result['removed_inactive'] + result['removed_active']
            task.status = "完成"
            task.stage = "done"

            if self.logger:
                self.logger.info(
                    f"重建清理完成: {channel.name} (ID: {channel.id}) - "
                    f"移除 {task.members_removed} 人，剩余 {result['final_count']} 人"
                )

            try:
                summary_embed = discord.Embed(
                    title="重建缓存并清理完成 ✅",
                    colour=discord.Colour.green(),
                    description=(
                        f"📊 已统计最近 **{limit}** 条消息\n"
                        f"🔹 已移除未发言成员：**{result['removed_inactive']}** 人\n"
                        f"🔹 已移除低活跃成员：**{result['removed_active']}** 人\n"
                        f"子区当前成员约为 **{result['final_count']}** 人"
                    ),
                    timestamp=datetime.now()
                )
                await channel.send("✅ 子区缓存已重建并完成清理", embed=summary_embed)
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"发送重建清理完成报告失败: {e}")

        except asyncio.TimeoutError:
            task.status = "失败"
            task.stage = "done"
            task.error_msg = f"超时（>{_TASK_TIMEOUT_SECONDS // 60}分钟）"
            if self.logger:
                self.logger.error(f"重建清理超时: {channel.name} (ID: {channel.id})")
        except asyncio.CancelledError:
            task.status = "已取消"
            task.stage = "done"
            task.error_msg = "被手动中断"
        except Exception as e:
            task.status = "失败"
            task.stage = "done"
            task.error_msg = str(e)
            if self.logger:
                self.logger.error(f"重建清理失败: {channel.name} (ID: {channel.id}) - {e}")
        finally:
            cooldown = _POST_CLEAR_COOLDOWN_SECONDS if task.status == "完成" else _CHECK_COOLDOWN_SECONDS
            self._check_cooldowns[task.thread_id] = (datetime.now(), cooldown)
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                pass
            self.active_tasks.pop(task.thread_id, None)

    # ── 核心流程 ─────────────────────────────────────────

    async def start_auto_clear(self, channel: discord.Thread) -> bool:
        """开始自动清理任务。

        关键：在第一个 await 之前就将任务写入 active_tasks，
        利用 asyncio 事件循环"两个 await 之间的同步代码不会被中断"的特性
        来杜绝竞态条件。
        """
        # ── 同步阶段（无 await，不会被其他协程打断）──
        if self.is_thread_disabled(channel.id):
            return False
        if self.is_clearing_active(channel.id):
            return False

        task = AutoClearTask(channel.id, channel.name)
        self.active_tasks[channel.id] = task
        self._check_cooldowns[channel.id] = (datetime.now(), _CHECK_COOLDOWN_SECONDS)

        # ── 异步阶段：检查成员数 ──
        try:
            members = await channel.fetch_members()
            if len(members) < 999:
                self.active_tasks.pop(channel.id, None)
                return False
        except Exception:
            self.active_tasks.pop(channel.id, None)
            return False

        # 启动日志更新循环
        if self._log_update_task is None or self._log_update_task.done():
            self._log_update_task = asyncio.create_task(self._log_update_loop())

        task._asyncio_task = asyncio.create_task(self._execute_auto_clear(channel, task))
        return True

    async def _execute_auto_clear(self, channel: discord.Thread, task: AutoClearTask):
        """执行自动清理任务"""
        _handled = False
        try:
            task.status = "正在执行"
            task.stage = "clear"

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

            result = await asyncio.wait_for(
                clear_thread_members(
                    channel, 950, self.bot,
                    logger=self.logger,
                    progress_cb=progress_callback,
                ),
                timeout=_TASK_TIMEOUT_SECONDS,
            )

            task.members_removed = result['removed_inactive'] + result['removed_active']
            task.status = "完成"
            task.stage = "done"

            if self.logger:
                self.logger.info(
                    f"自动清理完成: {channel.name} (ID: {channel.id}) - "
                    f"移除 {task.members_removed} 人，剩余 {result['final_count']} 人"
                )

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

        except asyncio.TimeoutError:
            _handled = True
            task.status = "失败"
            task.stage = "done"
            task.error_msg = f"超时（>{_TASK_TIMEOUT_SECONDS // 60}分钟），自动触发重建"
            if self.logger:
                self.logger.warning(
                    f"自动清理超时: {channel.name} (ID: {channel.id})，数据量可能过大，自动触发重建清理"
                )
            # 超时通常意味着消息历史太长，自动重建缓存（仅最近 10000 条）后重试
            self._check_cooldowns[task.thread_id] = (datetime.now(), _CHECK_COOLDOWN_SECONDS)
            self.active_tasks.pop(task.thread_id, None)
            try:
                await self.rebuild_and_clear(channel)
            except Exception as rebuild_err:
                if self.logger:
                    self.logger.error(f"超时后重建清理也失败: {channel.name} (ID: {channel.id}) - {rebuild_err}")
            return
        except Exception as e:
            task.status = "失败"
            task.stage = "done"
            task.error_msg = str(e)
            if self.logger:
                self.logger.error(f"自动清理失败: {channel.name} (ID: {channel.id}) - {e}")
        finally:
            if not _handled:
                cooldown = _POST_CLEAR_COOLDOWN_SECONDS if task.status == "完成" else _CHECK_COOLDOWN_SECONDS
                self._check_cooldowns[task.thread_id] = (datetime.now(), cooldown)
                # 短暂保留任务记录供日志面板展示完成状态
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    pass
                self.active_tasks.pop(task.thread_id, None)

    # ── 日志频道 ─────────────────────────────────────────

    async def _get_log_channel(self) -> Optional[discord.TextChannel | discord.Thread]:
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

            channel = await guild.fetch_channel(channel_id)
            return channel if isinstance(channel, discord.TextChannel) or isinstance(channel, discord.Thread) else None
        except Exception:
            return None

    async def _ensure_log_message(self) -> Optional[discord.Message]:
        channel = await self._get_log_channel()
        if not channel:
            return None

        async for message in channel.history(limit=50):
            if (message.author == self.bot.user and
                message.embeds and
                message.embeds[0].title == "🤖 自动清理任务状态"):
                self._log_message = message
                return message

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
        while True:
            try:
                self.cleanup_stale_tasks()

                if not self.active_tasks:
                    await asyncio.sleep(60)
                    continue

                await self._update_log_message()
                await asyncio.sleep(30)

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.logger:
                    self.logger.error(f"日志更新循环出错: {e}")
                await asyncio.sleep(30)

    async def _update_log_message(self):
        message = await self._ensure_log_message()
        if not message:
            return

        if not self.active_tasks:
            embed = discord.Embed(
                title="🤖 自动清理任务状态",
                description="暂无正在进行的自动清理任务",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
        else:
            embed = discord.Embed(
                title="🤖 自动清理任务状态",
                description=f"当前有 {len(self.active_tasks)} 个自动清理任务",
                color=discord.Color.orange(),
                timestamp=datetime.now()
            )

            for task in list(self.active_tasks.values()):
                duration = (datetime.now() - task.start_time).total_seconds()
                duration_str = f"{int(duration//60)}分{int(duration%60)}秒"

                if task.stage == "rebuild":
                    progress_desc = f"🔧 重建缓存: 已统计 {task.messages_processed} 条消息"
                elif task.stage == "stat":
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
