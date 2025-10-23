"""
轮换通知 Cog
实现基于 Discord scheduled events 的轮换通知功能
"""

import asyncio
import discord
from discord import EventStatus, app_commands
from discord.ext import commands, tasks
from typing import Optional, Dict, Set
import datetime
import pathlib
import json
import time

from src.banner.database import BannerDatabase, BannerItem
from src.banner.ui import ApplicationButton, ReviewView, ApplicationModal, RejectModal
from src.utils.auth import is_admin
from src.utils.config_helper import get_config_value


class BannerCommands(commands.Cog):
    """轮换通知命令处理"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = getattr(bot, 'logger', None)
        self.name = "轮换通知"
        self.db = BannerDatabase()
        self._config_cache = {}
        self._config_cache_mtime = None
        
        # 动态调度系统
        self._guild_schedules: Dict[int, float] = {}  # guild_id -> next_rotation_time
        self._event_cache: Dict[int, Dict] = {}       # guild_id -> event_info
        self._active_guilds: Set[int] = set()         # 有活跃轮换的服务器集合
        self._scheduler_task = None

    async def on_disable(self):
        """Cog卸载时停止后台任务"""
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
        if hasattr(self, 'rotation_task'):
            self.rotation_task.cancel()
        if self.logger:
            self.logger.info("轮换通知模块已卸载，后台任务已停止")

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

    @commands.Cog.listener()
    async def on_ready(self):
        """Cog加载完成"""
        # 添加持久视图（在事件循环运行后）
        try:
            self.bot.add_view(ApplicationButton())
            if self.logger:
                self.logger.info("✅ 轮换通知申请按钮视图已注册")
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ 注册申请按钮视图失败: {e}")
        
        # 启动动态调度系统
        await self._initialize_scheduler()
        if self.logger:
            self.logger.info("✅ 轮换通知模块已加载，动态调度系统已启动")

    banner = app_commands.Group(name="轮换通知", description="轮换通知管理")

    @banner.command(name="添加", description="添加一个轮换通知条目")
    @app_commands.describe(
        id="条目ID（唯一标识）",
        标题="通知标题",
        描述="通知描述",
        位置="活动位置",
        封面图="封面图片（可选）"
    )
    async def add_banner(
        self,
        interaction: discord.Interaction,
        id: str,
        标题: str,
        描述: str,
        位置: str,
        封面图: Optional[discord.Attachment] = None
    ):
        """添加轮换通知条目"""
        if not interaction.guild:
            await interaction.response.send_message("❌ 此命令只能在服务器中使用", ephemeral=True)
            return

        # 检查ID是否已存在
        existing = self.db.get_item(interaction.guild.id, id)
        if existing:
            await interaction.response.send_message(f"❌ ID `{id}` 已存在", ephemeral=True)
            return

        # 处理封面图
        cover_url = None
        if 封面图:
            cover_url = 封面图.url

        # 创建条目
        item = BannerItem(
            id=id,
            title=标题,
            description=描述,
            location=位置,
            cover_image=cover_url
        )

        # 保存到数据库
        if self.db.add_item(interaction.guild.id, item):
            await interaction.response.send_message(
                f"✅ 已添加轮换通知条目 `{id}`\n"
                f"**标题**: {标题}\n"
                f"**描述**: {描述}\n"
                f"**位置**: {位置}",
                ephemeral=True
            )
            
            # 触发调度更新
            self.schedule_guild_update(interaction.guild.id)
            
            if self.logger:
                self.logger.info(f"[轮换通知] {interaction.user} 添加了条目 {id}")
        else:
            await interaction.response.send_message("❌ 添加失败", ephemeral=True)

    @banner.command(name="删除", description="删除一个轮换通知条目")
    @app_commands.describe(id="要删除的条目ID")
    async def remove_banner(self, interaction: discord.Interaction, id: str):
        """删除轮换通知条目"""
        if not interaction.guild:
            await interaction.response.send_message("❌ 此命令只能在服务器中使用", ephemeral=True)
            return

        # 检查条目是否存在
        item = self.db.get_item(interaction.guild.id, id)
        if not item:
            await interaction.response.send_message(f"❌ 未找到ID为 `{id}` 的条目", ephemeral=True)
            return

        # 删除条目
        if self.db.remove_item(interaction.guild.id, id):
            await interaction.response.send_message(f"✅ 已删除轮换通知条目 `{id}`", ephemeral=True)
            
            # 检查删除后的状态
            config = self.db.load_config(interaction.guild.id)
            if len(config.items) == 0:
                # 没有条目了，删除event
                await self._delete_event(interaction.guild)
            else:
                # 触发调度更新
                self.schedule_guild_update(interaction.guild.id)
            
            if self.logger:
                self.logger.info(f"[轮换通知] {interaction.user} 删除了条目 {id}")
        else:
            await interaction.response.send_message("❌ 删除失败", ephemeral=True)

    @banner.command(name="编辑", description="编辑一个轮换通知条目")
    @app_commands.describe(
        id="要编辑的条目ID",
        标题="新标题",
        描述="新描述",
        位置="新位置",
        封面图="新封面图（可选）"
    )
    async def edit_banner(
        self,
        interaction: discord.Interaction,
        id: str,
        标题: str,
        描述: str,
        位置: str,
        封面图: Optional[discord.Attachment] = None
    ):
        """编辑轮换通知条目"""
        if not interaction.guild:
            await interaction.response.send_message("❌ 此命令只能在服务器中使用", ephemeral=True)
            return

        # 检查条目是否存在
        existing = self.db.get_item(interaction.guild.id, id)
        if not existing:
            await interaction.response.send_message(f"❌ 未找到ID为 `{id}` 的条目", ephemeral=True)
            return

        # 处理封面图
        cover_url = existing.cover_image
        if 封面图:
            cover_url = 封面图.url

        # 更新条目
        item = BannerItem(
            id=id,
            title=标题,
            description=描述,
            location=位置,
            cover_image=cover_url
        )

        if self.db.update_item(interaction.guild.id, item):
            await interaction.response.send_message(
                f"✅ 已更新轮换通知条目 `{id}`\n"
                f"**标题**: {标题}\n"
                f"**描述**: {描述}\n"
                f"**位置**: {位置}",
                ephemeral=True
            )
            
            # 触发调度更新
            self.schedule_guild_update(interaction.guild.id)
            
            if self.logger:
                self.logger.info(f"[轮换通知] {interaction.user} 编辑了条目 {id}")
        else:
            await interaction.response.send_message("❌ 编辑失败", ephemeral=True)

    @banner.command(name="列表", description="查看所有轮换通知条目")
    async def list_banners(self, interaction: discord.Interaction):
        """列出所有轮换通知条目"""
        if not interaction.guild:
            await interaction.response.send_message("❌ 此命令只能在服务器中使用", ephemeral=True)
            return

        items = self.db.get_all_items(interaction.guild.id)
        config = self.db.load_config(interaction.guild.id)

        if not items:
            await interaction.response.send_message("📝 当前没有轮换通知条目", ephemeral=True)
            return

        # 构建列表
        embed = discord.Embed(
            title="🔄 轮换通知列表",
            description=f"共有 {len(items)} 个条目",
            color=discord.Color.blue()
        )

        # 添加配置信息
        interval_str = self._format_interval(config.interval)
        embed.add_field(
            name="⚙️ 配置信息",
            value=f"**切换频率**: {interval_str}\n**当前索引**: {config.current_index + 1}/{len(items)}",
            inline=False
        )

        # 添加每个条目
        for i, item in enumerate(items, 1):
            current_marker = "🔹 " if i - 1 == config.current_index else ""
            field_value = f"{current_marker}**标题**: {item.title}\n**描述**: {item.description}\n**位置**: {item.location}"
            if item.cover_image:
                field_value += f"\n**封面**: [查看]({item.cover_image})"
            
            embed.add_field(
                name=f"#{i} - ID: `{item.id}`",
                value=field_value,
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @banner.command(name="切换频率", description="设置轮换通知的切换频率")
    @app_commands.describe(间隔时间="切换间隔（秒），例如：3600=1小时, 1800=30分钟")
    async def set_interval(self, interaction: discord.Interaction, 间隔时间: int):
        """设置轮换频率"""
        if not interaction.guild:
            await interaction.response.send_message("❌ 此命令只能在服务器中使用", ephemeral=True)
            return

        if 间隔时间 < 20:
            await interaction.response.send_message("❌ 间隔时间不能少于20秒", ephemeral=True)
            return

        if self.db.set_interval(interaction.guild.id, 间隔时间):
            interval_str = self._format_interval(间隔时间)
            await interaction.response.send_message(
                f"✅ 已设置切换频率为 {interval_str}",
                ephemeral=True
            )
            
            if self.logger:
                self.logger.info(f"[轮换通知] {interaction.user} 设置切换频率为 {间隔时间} 秒")
        else:
            await interaction.response.send_message("❌ 设置失败", ephemeral=True)

    @banner.command(name="创建申请按钮", description="在当前频道发送轮换通知申请按钮")
    @is_admin()
    async def create_application_button(self, interaction: discord.Interaction):
        """创建申请按钮"""
        if not interaction.guild:
            await interaction.response.send_message("❌ 此命令只能在服务器中使用", ephemeral=True)
            return
        
        # 检查配置
        config = get_config_value("banner_application", interaction.guild.id, {})
        if not config:
            await interaction.response.send_message(
                "❌ 轮换通知申请系统未配置\n请检查配置文件中的 `banner_application` 字段", 
                ephemeral=True
            )
            return
        
        required_fields = ["applicant_role_id", "review_channel_id", "reviewer_role_ids"]
        missing_fields = [field for field in required_fields if not config.get(field)]
        
        if missing_fields:
            await interaction.response.send_message(
                f"❌ 配置不完整，缺少字段: {', '.join(missing_fields)}", 
                ephemeral=True
            )
            return
        
        # 创建申请按钮视图
        embed = discord.Embed(
            title="🔄 banner申请",
            description="点击下方按钮申请您的轮换通知\n\n"
                       "📋 **申请要求**:\n"
                       f"• 需要具有 <@&{config['applicant_role_id']}> 身份组\n"
                       f"• 每人最多同时拥有 {config.get('max_applications_per_user', 1)} 个申请/轮换通知\n"
                       f"• 通过的申请将持续 {config.get('banner_duration_days', 7)} 天",
            color=discord.Color.blue()
        )
        embed.set_footer(text="申请系统 | Odysseia Bot")
        
        view = ApplicationButton()
        await interaction.response.send_message(embed=embed, view=view)
        
        if self.logger:
            self.logger.info(f"[轮换通知] {interaction.user} 创建了申请按钮")
    
    @banner.command(name="申请状态", description="查看轮换通知申请状态")
    async def application_status(self, interaction: discord.Interaction):
        """查看申请状态"""
        if not interaction.guild:
            await interaction.response.send_message("❌ 此命令只能在服务器中使用", ephemeral=True)
            return
        
        applications = self.db.get_all_applications(interaction.guild.id)
        user_applications = [app for app in applications if app.applicant_id == interaction.user.id]
        
        if not user_applications:
            await interaction.response.send_message("📝 您没有任何申请记录", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="📋 我的申请状态",
            color=discord.Color.blue()
        )
        
        status_map = {
            "pending": "⏳ 待审核",
            "approved": "✅ 已通过",
            "rejected": "❌ 已拒绝", 
            "active": "🔴 活跃中",
            "waitlisted": "⌛ 等待列表",
            "expired": "⏰ 已过期"
        }
        
        for app in user_applications:
            status_text = status_map.get(app.status.value, app.status.value)
            field_value = f"**状态**: {status_text}\n**标题**: {app.title}\n**位置**: {app.location}"
            
            if app.reviewed_at:
                field_value += f"\n**审核时间**: {app.reviewed_at[:19].replace('T', ' ')}"
            
            if app.rejection_reason:
                field_value += f"\n**拒绝理由**: {app.rejection_reason}"
            
            if app.expires_at:
                field_value += f"\n**到期时间**: {app.expires_at[:19].replace('T', ' ')}"
            
            embed.add_field(
                name=f"申请ID: `{app.id}`",
                value=field_value,
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @banner.command(name="管理申请", description="管理轮换通知申请（管理员专用）")
    async def manage_applications(self, interaction: discord.Interaction):
        """管理申请"""
        if not interaction.guild:
            await interaction.response.send_message("❌ 此命令只能在服务器中使用", ephemeral=True)
            return
        
        # 检查管理员权限
        if not is_admin(interaction.user, interaction.guild.id):
            await interaction.response.send_message("❌ 只有管理员可以使用此命令", ephemeral=True)
            return
        
        # 获取所有申请
        config = self.db.load_config(interaction.guild.id)
        pending_apps = [app for app in config.applications if app.status.value == "pending"]
        waitlist_apps = config.waitlist
        active_apps = [app for app in config.applications if app.status.value == "active"]
        
        embed = discord.Embed(
            title="🛠️ 申请管理面板",
            color=discord.Color.orange()
        )
        
        embed.add_field(
            name="⏳ 待审核申请",
            value=f"{len(pending_apps)} 个" if pending_apps else "无",
            inline=True
        )
        
        embed.add_field(
            name="⌛ 等待列表",
            value=f"{len(waitlist_apps)} 个" if waitlist_apps else "无", 
            inline=True
        )
        
        embed.add_field(
            name="🔴 活跃banner",
            value=f"{len([item for item in config.items if item.application_id])} 个",
            inline=True
        )
        
        # 显示待审核申请详情
        if pending_apps:
            pending_text = ""
            for app in pending_apps[:5]:  # 最多显示5个
                pending_text += f"• `{app.id}` - {app.title} (申请者: <@{app.applicant_id}>)\n"
            if len(pending_apps) > 5:
                pending_text += f"... 还有 {len(pending_apps) - 5} 个申请"
            embed.add_field(name="📋 待审核详情", value=pending_text, inline=False)
        
        # 显示等待列表详情
        if waitlist_apps:
            waitlist_text = ""
            for app in waitlist_apps[:5]:  # 最多显示5个
                waitlist_text += f"• `{app.id}` - {app.title} (申请者: <@{app.applicant_id}>)\n"
            if len(waitlist_apps) > 5:
                waitlist_text += f"... 还有 {len(waitlist_apps) - 5} 个申请"
            embed.add_field(name="⌛ 等待列表详情", value=waitlist_text, inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    def _format_interval(self, seconds: int) -> str:
        """格式化时间间隔"""
        if seconds < 60:
            return f"{seconds}秒"
        elif seconds < 3600:
            minutes = seconds // 60
            return f"{minutes}分钟"
        elif seconds < 86400:
            hours = seconds // 3600
            return f"{hours}小时"
        else:
            days = seconds // 86400
            return f"{days}天"

    # ==================== 动态调度系统 ====================
    
    async def _initialize_scheduler(self):
        """初始化动态调度系统"""
        try:
            # 扫描所有服务器，初始化调度信息
            await self._scan_and_schedule_all_guilds()
            
            # 启动主调度循环
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())
            
            if self.logger:
                self.logger.info(f"[调度器] 动态调度系统已初始化，管理 {len(self._active_guilds)} 个活跃服务器")
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"[调度器] 初始化失败: {e}")

    async def _scan_and_schedule_all_guilds(self):
        """扫描所有服务器并设置调度"""
        for config_file in self.db.data_dir.glob("*.json"):
            try:
                guild_id = int(config_file.stem)
                guild = self.bot.get_guild(guild_id)
                
                if not guild:
                    continue
                
                config = self.db.load_config(guild_id)
                
                # 清理过期项目
                expired_items = self.db.cleanup_expired_with_details(guild_id)
                if expired_items:
                    await self._handle_expired_items(guild, expired_items)
                
                # 检查是否有活跃的轮换
                if config.items and len(config.items) > 1:
                    await self._schedule_guild_rotation(guild_id)
                else:
                    # 移除非活跃服务器
                    self._active_guilds.discard(guild_id)
                    self._guild_schedules.pop(guild_id, None)
                    self._event_cache.pop(guild_id, None)
                    
            except Exception as e:
                if self.logger:
                    self.logger.error(f"[调度器] 扫描服务器 {guild_id} 时出错: {e}")

    async def _schedule_guild_rotation(self, guild_id: int):
        """为服务器安排轮换调度"""
        try:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                return
                
            config = self.db.load_config(guild_id)
            if not config.items:
                return
            
            # 检查或创建event
            event_info = await self._get_or_create_event(guild, config)
            if not event_info:
                return
            
            # 计算下次轮换时间
            next_rotation = event_info['end_time'].timestamp()
            
            # 更新调度信息
            self._guild_schedules[guild_id] = next_rotation
            self._event_cache[guild_id] = event_info
            self._active_guilds.add(guild_id)
            
            if self.logger:
                time_until = next_rotation - time.time()
                discord_end = event_info.get('discord_end_time')
                discord_buffer = (discord_end.timestamp() - next_rotation) if discord_end else 0
                self.logger.debug(f"[调度器] 服务器 {guild.name} 下次轮换: {time_until:.0f}秒后 (Discord事件缓冲: {discord_buffer:.0f}秒)")
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"[调度器] 安排服务器 {guild_id} 轮换时出错: {e}")

    async def _scheduler_loop(self):
        """主调度循环 - 智能等待和处理"""
        while True:
            try:
                current_time = time.time()
                next_action_time = None
                guilds_to_rotate = []
                
                # 检查需要轮换的服务器
                for guild_id in list(self._active_guilds):
                    scheduled_time = self._guild_schedules.get(guild_id)
                    
                    if scheduled_time is None:
                        continue
                        
                    if scheduled_time <= current_time + 10:  # 提前10秒准备
                        guilds_to_rotate.append(guild_id)
                    else:
                        if next_action_time is None or scheduled_time < next_action_time:
                            next_action_time = scheduled_time
                
                # 执行需要轮换的服务器
                for guild_id in guilds_to_rotate:
                    await self._execute_guild_rotation(guild_id)
                
                # 计算下次检查时间
                if next_action_time:
                    sleep_time = min(next_action_time - current_time, 300)  # 最多等待5分钟
                    sleep_time = max(sleep_time, 10)  # 最少等待10秒
                else:
                    sleep_time = 60  # 没有活跃轮换时，每分钟检查一次新的
                
                if self.logger and guilds_to_rotate:
                    self.logger.debug(f"[调度器] 处理了 {len(guilds_to_rotate)} 个轮换，下次检查: {sleep_time:.0f}秒后")
                
                # 定期重新扫描以发现新的轮换
                if current_time % 600 < sleep_time:  # 每10分钟扫描一次
                    asyncio.create_task(self._scan_and_schedule_all_guilds())
                
                await asyncio.sleep(sleep_time)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.logger:
                    self.logger.error(f"[调度器] 调度循环出错: {e}")
                await asyncio.sleep(30)  # 出错后等待30秒再重试

    async def _execute_guild_rotation(self, guild_id: int):
        """执行服务器轮换"""
        try:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                self._active_guilds.discard(guild_id)
                return
            
            # 获取下一个条目
            next_item = self.db.get_next_item(guild_id)
            if not next_item:
                # 没有更多条目，移除调度
                self._active_guilds.discard(guild_id)
                self._guild_schedules.pop(guild_id, None)
                self._event_cache.pop(guild_id, None)
                return
            
            # 更新event
            config = self.db.load_config(guild_id)
            event_info = await self._get_or_create_event(guild, config, force_update=True)
            
            if event_info:
                # 更新调度信息
                self._guild_schedules[guild_id] = event_info['end_time'].timestamp()
                self._event_cache[guild_id] = event_info
                
                if self.logger:
                    self.logger.info(f"[调度器] 服务器 {guild.name} 轮换到: {next_item.title}")
            else:
                # 创建失败，移除调度
                self._active_guilds.discard(guild_id)
                self._guild_schedules.pop(guild_id, None)
                self._event_cache.pop(guild_id, None)
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"[调度器] 执行服务器 {guild_id} 轮换时出错: {e}")

    async def _get_or_create_event(self, guild: discord.Guild, config, force_update=False):
        """获取或创建event，返回event信息"""
        try:
            current_item = config.items[config.current_index]
            
            # 如果强制更新或缓存中没有event信息，重新创建
            if force_update or guild.id not in self._event_cache:
                # 尝试获取现有event
                existing_event = None
                if config.event_id:
                    try:
                        existing_event = await guild.fetch_scheduled_event(config.event_id)
                    except:
                        pass  # Event可能已删除
                
                # 计算时间（给Discord事件结束时间添加缓冲，避免事件提前消失）
                start_time = discord.utils.utcnow() + datetime.timedelta(seconds=10)
                
                # Discord事件延后结束，给轮换系统留出缓冲时间
                # 原理：我们的轮换调度在设定时间执行，但Discord事件会延后结束
                # 这样即使轮换稍有延迟，Discord事件也不会提前消失
                buffer_time = min(300, max(60, config.interval // 10))  # 60-300秒缓冲，根据间隔动态调整
                end_time = start_time + datetime.timedelta(seconds=config.interval + buffer_time)
                
                # 准备event数据
                event_kwargs = {
                    'name': current_item.title,
                    'description': current_item.description,
                    'start_time': start_time,
                    'end_time': end_time,
                    'entity_type': discord.EntityType.external,
                    'location': current_item.location,
                    'privacy_level': discord.PrivacyLevel.guild_only
                }
                
                # 添加封面图
                if current_item.cover_image:
                    event_kwargs['image'] = await self._get_image_data(current_item.cover_image)
                
                # 创建或更新event
                try:
                    if existing_event:
                        update_kwargs = {k: v for k, v in event_kwargs.items() 
                                       if k not in ['start_time', 'entity_type']}
                        await existing_event.edit(**update_kwargs)
                        event = existing_event
                    else:
                        event = await guild.create_scheduled_event(**event_kwargs)
                        self.db.set_event_id(guild.id, event.id)
                    
                    # 返回轮换调度时间（不包含缓冲时间）
                    rotation_time = start_time + datetime.timedelta(seconds=config.interval)
                    return {
                        'event': event,
                        'end_time': rotation_time,  # 轮换时间，用于调度
                        'discord_end_time': event_kwargs['end_time'],  # Discord事件实际结束时间
                        'item': current_item
                    }
                    
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"[调度器] 创建/更新event失败 {guild.name}: {e}")
                    return None
            
            else:
                # 使用缓存的信息
                return self._event_cache[guild.id]
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"[调度器] 获取event信息时出错 {guild.name}: {e}")
            return None

    async def _get_image_data(self, image_url: str):
        """异步获取图片数据"""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as resp:
                    if resp.status == 200:
                        return await resp.read()
        except:
            pass
        return None

    async def _handle_expired_items(self, guild: discord.Guild, expired_items: list):
        """处理过期的项目"""
        try:
            if self.logger:
                self.logger.info(f"[调度器] 服务器 {guild.name} 清理了 {len(expired_items)} 个过期banner")
            
            # 为过期的banner记录审核日志
            for expired_item in expired_items:
                if expired_item.application_id:
                    try:
                        from src.banner.ui import _send_audit_log
                        application = self.db.get_application(guild.id, expired_item.application_id)
                        if application:
                            await _send_audit_log(
                                guild,
                                application,
                                "过期",
                                guild.me,
                                f"Banner已达到{get_config_value('banner_application', guild.id, {}).get('banner_duration_days', 7)}天期限"
                            )
                    except Exception as e:
                        if self.logger:
                            self.logger.error(f"[调度器] 记录过期日志失败: {e}")
                            
        except Exception as e:
            if self.logger:
                self.logger.error(f"[调度器] 处理过期项目时出错: {e}")

    def schedule_guild_update(self, guild_id: int):
        """手动触发服务器调度更新（用于命令调用后）"""
        if guild_id in self._active_guilds:
            # 清除缓存，强制重新计算
            self._event_cache.pop(guild_id, None)
            asyncio.create_task(self._schedule_guild_rotation(guild_id))


    async def _delete_event(self, guild: discord.Guild):
        """删除event"""
        try:
            config = self.db.load_config(guild.id)
            
            if not config.event_id:
                return
            
            try:
                event = await guild.fetch_scheduled_event(config.event_id)
                await event.delete()
                if self.logger:
                    self.logger.info(f"[轮换通知] 删除了服务器 {guild.name} 的event")
            except:
                pass  # Event可能已经不存在
            
            # 清除event ID
            self.db.set_event_id(guild.id, None)
            
            # 从调度系统移除
            self._active_guilds.discard(guild.id)
            self._guild_schedules.pop(guild.id, None)
            self._event_cache.pop(guild.id, None)
        
        except Exception as e:
            if self.logger:
                self.logger.error(f"[轮换通知] 删除event时出错: {e}")
