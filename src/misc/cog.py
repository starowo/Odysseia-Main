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

        # 读取管理员列表
        admins: list[int] = []
        try:
            config_path = pathlib.Path("config.json")
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                admins = cfg.get("admins", [])
        except Exception:
            pass

        # 非管理员用户一分钟限速
        if user.id not in admins:
            last_time = self.announce_cooldowns.get(user.id)
            if last_time and (now - last_time).total_seconds() < 60:
                await interaction.response.send_message(
                    "❌ 发送通知频率过高，请一分钟后再试", ephemeral=True
                )
                return
            self.announce_cooldowns[user.id] = now

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

