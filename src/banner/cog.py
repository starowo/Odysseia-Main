"""
轮换通知 Cog
实现基于 Discord scheduled events 的轮换通知功能
"""

import asyncio
import discord
from discord import EventStatus, app_commands
from discord.ext import commands, tasks
from typing import Optional
import datetime
import pathlib
import json

from src.banner.database import BannerDatabase, BannerItem
from src.banner.ui import ApplicationButton, ReviewView, ApplicationModal, RejectModal, BannerListView
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
        self._task_running = True

    async def on_disable(self):
        """Cog卸载时停止后台任务"""
        # 设置停止标志
        self._task_running = False
        if self.logger:
            self.logger.info("轮换通知模块已卸载，后台任务将停止")

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
        
        # 启动轮换任务
        
        self._task_running = True
        asyncio.create_task(self.rotation_task())
        if self.logger:
            self.logger.info("✅ 轮换通知模块已加载，后台任务已启动")

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
            
            # 如果这是第一个条目，自动创建event
            config = self.db.load_config(interaction.guild.id)
            if len(config.items) == 1 and not config.event_id:
                await self._create_or_update_event(interaction.guild)
            
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
            
            # 如果删除后没有条目了，删除event
            config = self.db.load_config(interaction.guild.id)
            if len(config.items) == 0 and config.event_id:
                await self._delete_event(interaction.guild)
            else:
                # 更新event显示下一个条目
                await self._create_or_update_event(interaction.guild)
            
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
            
            # 更新event
            await self._create_or_update_event(interaction.guild)
            
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
            embed = discord.Embed(
                title="📝 轮换通知列表",
                description="当前没有轮换通知条目",
                color=discord.Color.orange()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 构建初始显示embed
        embed = discord.Embed(
            title="🔄 轮换通知管理",
            description=f"共有 **{len(items)}** 个条目\n\n使用下方的下拉菜单选择要操作的条目，然后点击编辑或删除按钮。",
            color=discord.Color.blue()
        )

        # 添加配置信息
        interval_str = self._format_interval(config.interval)
        embed.add_field(
            name="⚙️ 配置信息",
            value=f"**切换频率**: {interval_str}\n**当前索引**: {config.current_index + 1}/{len(items)}",
            inline=False
        )

        # 显示当前活跃的条目
        if items:
            current_item = items[config.current_index] if config.current_index < len(items) else items[0]
            embed.add_field(
                name="🔹 当前显示的条目",
                value=f"**ID**: `{current_item.id}`\n**标题**: {current_item.title}",
                inline=False
            )

        embed.set_footer(text="💡 提示：选择条目后可进行编辑或删除操作")

        # 创建交互式视图
        view = BannerListView(interaction.guild.id, items)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

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

    async def rotation_task(self):
        """动态轮换任务"""
        await self.bot.wait_until_ready()
        while self._task_running:
            try:
                next_rotation_time = None
                current_time = discord.utils.utcnow()
                
                # 遍历所有有配置的服务器
                for config_file in self.db.data_dir.glob("*.json"):
                    try:
                        guild_id = int(config_file.stem)
                        guild = self.bot.get_guild(guild_id)
                        
                        if not guild:
                            continue
                        
                        config = self.db.load_config(guild_id)
                        
                        # 清理过期的申请banner并记录
                        expired_items = self.db.cleanup_expired_with_details(guild_id)
                        if expired_items and self.logger:
                            self.logger.info(f"[轮换通知] 服务器 {guild.name} 清理了 {len(expired_items)} 个过期banner")
                            
                            # 为过期的banner记录审核日志
                            for expired_item in expired_items:
                                if expired_item.application_id:
                                    try:
                                        from src.banner.ui import _send_audit_log
                                        # 获取对应的申请
                                        application = self.db.get_application(guild_id, expired_item.application_id)
                                        if application:
                                            await _send_audit_log(
                                                guild,
                                                application,
                                                "过期",
                                                guild.me,  # 系统自动操作
                                                f"Banner已达到{get_config_value('banner_application', guild_id, {}).get('banner_duration_days', 7)}天期限"
                                            )
                                    except Exception as e:
                                        if self.logger:
                                            self.logger.error(f"[轮换通知] 记录过期日志失败: {e}")
                        
                        # 检查是否需要轮换
                        if not config.items or not config.event_id:
                            continue
                        
                        # 检查event是否存在
                        try:
                            event = await guild.fetch_scheduled_event(config.event_id)
                        except:
                            # Event不存在，尝试创建新的
                            await self._create_or_update_event(guild)
                            continue
                        
                        
                        await self._rotate_to_next_item(guild)
                
                    
                    except Exception as e:
                        if self.logger:
                            self.logger.error(f"[轮换通知] 处理服务器 {guild_id} 时出错: {e}")
                
                wait_seconds = config.interval  # 如果没有待轮换的服务器，等待1分钟
                
                if self.logger:
                    self.logger.debug(f"[轮换通知] 下次检查等待 {wait_seconds:.1f} 秒")
                
                # 等待到下次检查时间
                await asyncio.sleep(wait_seconds)
            
            except Exception as e:
                if self.logger:
                    self.logger.error(f"[轮换通知] 轮换任务出错: {e}")
                await asyncio.sleep(30)  # 出错时等待30秒再继续


    async def _create_or_update_event(self, guild: discord.Guild):
        """创建或更新event"""
        try:
            config = self.db.load_config(guild.id)
            
            if not config.items:
                return
            
            # 获取当前要显示的条目
            current_item = config.items[config.current_index]
            
            # 计算开始时间（从现在开始）
            start_time = discord.utils.utcnow() + datetime.timedelta(seconds=10)
            # 结束时间延后30秒，为下一次轮换的网络延迟预留空间
            end_time = start_time + datetime.timedelta(seconds=config.interval + 30)
            
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
                try:
                    import aiohttp
                    async with aiohttp.ClientSession() as session:
                        async with session.get(current_item.cover_image) as resp:
                            if resp.status == 200:
                                image_data = await resp.read()
                                event_kwargs['image'] = image_data
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"[轮换通知] 获取封面图时出错: {e}")
                    pass  # 如果获取图片失败，继续创建event但不带图片
            
            # 如果已有event，尝试编辑；否则创建新的
            if config.event_id:
                try:
                    # 为更新的事件也预留网络延迟时间
                    update_end_time = discord.utils.utcnow() + datetime.timedelta(seconds=config.interval + 30)
                    update_kwargs = {
                        'name': event_kwargs['name'],
                        'description': event_kwargs['description'],
                        'end_time': update_end_time,
                        'location': event_kwargs['location'],
                        'privacy_level': event_kwargs['privacy_level']
                    }
                    if 'image' in event_kwargs:
                        update_kwargs['image'] = event_kwargs['image']
                    else:
                        update_kwargs['image'] = None
                    event = await guild.fetch_scheduled_event(config.event_id)
                    await event.edit(**update_kwargs)
                    if self.logger:
                        self.logger.info(f"[轮换通知] 更新了服务器 {guild.name} 的event: {current_item.title}")
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"[轮换通知] 更新event时出错: {e}")
                    # Event不存在，创建新的
                    event = await guild.create_scheduled_event(**event_kwargs)
                    self.db.set_event_id(guild.id, event.id)
                    if self.logger:
                        self.logger.info(f"[轮换通知] 为服务器 {guild.name} 创建了新event: {current_item.title}")
            else:
                # 创建新event
                event = await guild.create_scheduled_event(**event_kwargs)
                self.db.set_event_id(guild.id, event.id)
                if self.logger:
                    self.logger.info(f"[轮换通知] 为服务器 {guild.name} 创建了event: {current_item.title}")
        
        except Exception as e:
            if self.logger:
                self.logger.error(f"[轮换通知] 创建/更新event时出错: {e}")

    async def _rotate_to_next_item(self, guild: discord.Guild):
        """轮换到下一个条目"""
        try:
            # 获取下一个条目（这会自动更新索引）
            next_item = self.db.get_next_item(guild.id)
            
            if not next_item:
                return
            
            # 更新event
            await self._create_or_update_event(guild)
            
            if self.logger:
                self.logger.info(f"[轮换通知] 服务器 {guild.name} 轮换到下一个条目: {next_item.title}")
        
        except Exception as e:
            if self.logger:
                self.logger.error(f"[轮换通知] 轮换条目时出错: {e}")

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
        
        except Exception as e:
            if self.logger:
                self.logger.error(f"[轮换通知] 删除event时出错: {e}")
