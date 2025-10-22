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
from src.utils.auth import is_admin


class BannerCommands(commands.Cog):
    """轮换通知命令处理"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = getattr(bot, 'logger', None)
        self.name = "轮换通知"
        self.db = BannerDatabase()
        self._config_cache = {}
        self._config_cache_mtime = None

    async def on_disable(self):
        """Cog卸载时停止后台任务"""
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
        self.rotation_task.start()
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

    @tasks.loop(seconds=20)  # 每20s检查一次
    async def rotation_task(self):
        """后台轮换任务"""
        try:
            # 遍历所有有配置的服务器
            for config_file in self.db.data_dir.glob("*.json"):
                try:
                    guild_id = int(config_file.stem)
                    guild = self.bot.get_guild(guild_id)
                    
                    if not guild:
                        continue
                    
                    config = self.db.load_config(guild_id)
                    
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
                    
                    # 计算距离event结束的时间
                    now = discord.utils.utcnow()
                    time_until_end = (event.end_time - now).total_seconds()
                    
                    # 如果event即将结束（小于20秒），更新到下一个条目
                    if time_until_end < 20:
                        await self._rotate_to_next_item(guild)
                
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"[轮换通知] 处理服务器 {guild_id} 时出错: {e}")
        
        except Exception as e:
            if self.logger:
                self.logger.error(f"[轮换通知] 轮换任务出错: {e}")

    @rotation_task.before_loop
    async def before_rotation_task(self):
        """等待bot准备完成"""
        await self.bot.wait_until_ready()

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
            end_time = start_time + datetime.timedelta(seconds=config.interval)
            
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
                    update_kwargs = {
                        'name': event_kwargs['name'],
                        'description': event_kwargs['description'],
                        'end_time': event_kwargs['end_time'],
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
