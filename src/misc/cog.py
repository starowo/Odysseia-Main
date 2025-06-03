import datetime
import json
import pathlib

import discord
from discord import app_commands
from discord.ext import commands
from src.utils.confirm_view import confirm_view, confirm_view_embed


class MiscCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = getattr(bot, 'logger', None)
        self.name = "杂项命令"
        # 用户最后发送通知时间缓存，单位为UTC datetime
        self.announce_cooldowns: dict[int, datetime.datetime] = {}

    async def on_ready(self):
        self.bot.logger.info(f"杂项命令已加载")

    # 权限检查装饰器
    def is_admin():
        async def predicate(ctx):
            try:
                guild = ctx.guild
                if not guild:
                    return False
                    
                cog = ctx.cog
                # 优先使用服务器特定的admin配置
                admin_roles = cog.get_guild_config(guild.id, 'admins', [])
                
                # 检查用户是否拥有任何管理员身份组
                for admin_role_id in admin_roles:
                    role = guild.get_role(int(admin_role_id))
                    if role and role in ctx.author.roles:
                        return True
                return False
            except Exception:
                return False
        return commands.check(predicate)

    @property
    def config(self):
        """读取配置文件并缓存，只有在文件修改后重新加载"""
        try:
            path = pathlib.Path('config.json')
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            if self.logger:
                self.logger.error(f"加载配置文件失败: {e}")
            return {}
    
    def get_guild_config(self, guild_id: int, key: str, default=None):
        """获取服务器特定的配置值"""
        config = self.config
        
        # 从服务器特定配置获取
        guild_configs = config.get("guild_configs", {})
        guild_config = guild_configs.get(str(guild_id), {})
        if key in guild_config:
            return guild_config[key]
        
        return default

    @app_commands.command(name="发送通知", description="发送公告通知，使用粉色 embed")
    @is_admin()
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

