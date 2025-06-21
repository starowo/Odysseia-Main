import datetime
import json
import pathlib
import re
import asyncio

import discord
from discord import app_commands
from discord.ext import commands
from src.utils.confirm_view import confirm_view, confirm_view_embed


class TemporaryMessageView(discord.ui.View):
    def __init__(self, author_id: int, content: str, image_url: str = None):
        super().__init__(timeout=None)  # 不使用View的timeout
        self.author_id = author_id
        self.content = content
        self.image_url = image_url
        self.is_deleted = False  # 标记消息是否已被删除

    @discord.ui.button(label="查看消息", style=discord.ButtonStyle.primary, emoji="👁️")
    async def view_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        """查看临时消息内容"""
        if self.is_deleted:
            await interaction.response.send_message("❌ 消息已被删除！", ephemeral=True)
            return
            
        embed = discord.Embed(
            title="临时消息内容",
            description=self.content or "（无文字内容）",
            color=discord.Color.blue()
        )
        if self.image_url:
            embed.set_image(url=self.image_url)
        
        embed.set_footer(text="这是一条临时消息，仅您可见")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="删除消息", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        """删除临时消息（仅原发布者可操作）"""
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 只有消息的发布者才能删除此消息！", ephemeral=True)
            return
        
        if self.is_deleted:
            await interaction.response.send_message("❌ 消息已被删除！", ephemeral=True)
            return
        
        self.is_deleted = True
        embed = discord.Embed(
            title="临时消息已删除",
            description="消息已被发布者手动删除",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=None)

    async def auto_delete(self, message: discord.Message, timeout_seconds: int):
        """自动删除任务"""
        try:
            await asyncio.sleep(timeout_seconds)
            if not self.is_deleted:
                self.is_deleted = True
                embed = discord.Embed(
                    title="临时消息已过期",
                    description="消息已超过指定时长，自动删除",
                    color=discord.Color.orange()
                )
                await message.edit(embed=embed, view=None)
        except discord.NotFound:
            # 消息已被删除，忽略
            pass
        except Exception as e:
            # 其他错误，记录但不抛出
            print(f"自动删除临时消息时发生错误: {e}")


class MiscCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = getattr(bot, 'logger', None)
        self.name = "杂项命令"
        # 用户最后发送通知时间缓存，单位为UTC datetime
        self.announce_cooldowns: dict[int, datetime.datetime] = {}
        # 初始化配置缓存
        self._config_cache = {}
        self._config_cache_mtime = None
        # 临时消息自动删除任务管理
        self.temp_message_tasks: set[asyncio.Task] = set()

    def parse_duration(self, duration_str: str) -> int:
        """解析时长字符串，返回秒数"""
        pattern = r'^(\d+)([mh])$'
        match = re.match(pattern, duration_str.lower())
        
        if not match:
            raise ValueError("时长格式无效，请使用如：5m, 30m, 1h, 2h 等格式")
        
        value, unit = match.groups()
        value = int(value)
        
        if unit == 'm':
            seconds = value * 60
        elif unit == 'h':
            seconds = value * 3600
        
        # 限制最长3小时
        max_seconds = 3 * 3600
        if seconds > max_seconds:
            raise ValueError("时长不能超过3小时")
        
        if seconds < 60:
            raise ValueError("时长不能少于1分钟")
        
        return seconds

    async def on_ready(self):
        self.bot.logger.info(f"杂项命令已加载")

    async def cog_unload(self):
        """卸载Cog时清理所有未完成的任务"""
        for task in self.temp_message_tasks:
            task.cancel()
        # 等待所有任务取消完成
        if self.temp_message_tasks:
            await asyncio.gather(*self.temp_message_tasks, return_exceptions=True)
        self.temp_message_tasks.clear()

    # 权限检查装饰器
    def is_admin():
        async def predicate(interaction: discord.Interaction):
            try:
                guild = interaction.guild
                if not guild:
                    return False
                    
                cog = interaction.client.get_cog("MiscCommands")
                if not cog:
                    return False
                config = getattr(cog, 'config', {})
                for admin in config.get('admins', []):
                    role = guild.get_role(admin)
                    if role:
                        if role in interaction.user.roles:
                            return True
                return False
            except Exception:
                return False
        return app_commands.check(predicate)

    @property
    def config(self):
        """读取配置文件并缓存，只有在文件修改后重新加载"""
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

    @app_commands.command(name="临时消息", description="发送临时消息，指定时长后自动删除")
    @app_commands.describe(
        文字="消息内容（可选，但文字和图片至少要有一个）",
        图片="图片附件（可选，最多一张）",
        时长="消息保留时长，如：5m, 30m, 1h, 2h（最长3小时）"
    )
    async def temporary_message(
        self,
        interaction: discord.Interaction,
        时长: str,
        文字: str = None,
        图片: discord.Attachment = None
    ):
        # 验证参数
        if not 文字 and not 图片:
            await interaction.response.send_message("❌ 文字和图片至少要有一个！", ephemeral=True)
            return
        
        # 解析时长
        try:
            timeout_seconds = self.parse_duration(时长)
        except ValueError as e:
            await interaction.response.send_message(f"❌ {str(e)}", ephemeral=True)
            return
        
        # 验证图片
        if 图片 and not 图片.content_type.startswith('image/'):
            await interaction.response.send_message("❌ 只能上传图片文件！", ephemeral=True)
            return
        
        # 构造预览embed
        preview_embed = discord.Embed(
            title="📝 临时消息预览",
            color=discord.Color.blue()
        )
        
        if 文字:
            preview_embed.add_field(name="消息内容", value=文字, inline=False)
        
        if 图片:
            preview_embed.set_image(url=图片.url)
            
        # 计算时长显示
        hours = timeout_seconds // 3600
        minutes = (timeout_seconds % 3600) // 60
        duration_text = ""
        if hours > 0:
            duration_text += f"{hours}小时"
        if minutes > 0:
            duration_text += f"{minutes}分钟"
        
        preview_embed.add_field(name="保留时长", value=duration_text, inline=True)
        preview_embed.add_field(name="发布者", value=interaction.user.mention, inline=True)
        
        preview_embed.set_footer(text="⚠️ 请确保消息内容符合社区规范，不得发布违规内容")
        
        await interaction.response.defer(ephemeral=True)
        
        # 确认是否发送
        confirmed = await confirm_view_embed(
            interaction,
            embed=preview_embed,
            timeout=60
        )
        
        if not confirmed:
            return
        
        # 创建临时消息视图
        view = TemporaryMessageView(
            author_id=interaction.user.id,
            content=文字,
            image_url=图片.url if 图片 else None
        )
        
        # 发送临时消息通知
        notification_embed = discord.Embed(
            title="📨 有新的临时消息",
            description=f"来自 {interaction.user.mention} 的临时消息",
            color=discord.Color.green()
        )
        notification_embed.add_field(name="保留时长", value=duration_text, inline=True)
        notification_embed.add_field(name="操作", value="点击下方按钮查看或删除消息", inline=False)
        notification_embed.set_footer(text="消息将在指定时长后自动删除")
        
        # 发送到频道
        message = await interaction.channel.send(embed=notification_embed, view=view)
        
        # 创建自动删除任务并加入管理
        task = asyncio.create_task(view.auto_delete(message, timeout_seconds))
        self.temp_message_tasks.add(task)
        # 任务完成后自动从集合中移除
        task.add_done_callback(self.temp_message_tasks.discard)
        
        # 给用户发送成功确认
        success_embed = discord.Embed(
            title="✅ 临时消息发送成功",
            description="您的临时消息已发布到频道",
            color=discord.Color.green()
        )
        await interaction.edit_original_response(embed=success_embed, view=None)

    @app_commands.command(name="发送通知", description="发送公告通知，使用粉色 embed")
    
    @app_commands.describe(
        title="标题",
        content="内容",
        image="图片附件",
        thumbnail="缩略图（可选）"
    )
    @app_commands.rename(title="标题", content="内容", image="图片", thumbnail="缩略图")
    async def announce(
        self,
        interaction: discord.Interaction,
        title: str,
        content: str,
        image: discord.Attachment = None,
        thumbnail: discord.Attachment = None
    ):
        # 获取用户与时间
        user = interaction.user
        now = datetime.datetime.now(datetime.timezone.utc)

        # 构造粉色 embed
        embed = discord.Embed(
            title=title,
            description=content,
            color=discord.Color.pink(),
            timestamp=now
        )
        embed.set_footer(text=user.display_name, icon_url=user.display_avatar.url)
        if image:
            embed.set_image(url=image.url)
        if thumbnail:
            embed.set_thumbnail(url=thumbnail.url)

        # 预览通知 Embed
        await interaction.response.defer(ephemeral=True)

        confirm_embed = embed.copy()
        confirm_embed.set_footer(text="通知预览，点击按钮确认发送")

        # 确认是否发送
        confirmed = await confirm_view_embed(
            interaction,
            embed=confirm_embed,
            timeout=60
        )

        if not confirmed:
            return

        # 发送通知至频道
        await interaction.channel.send(embed=embed)

