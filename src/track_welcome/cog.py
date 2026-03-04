import discord
from discord.ext import commands, tasks
import logging
from datetime import datetime

# 导入配置
from src.track_welcome.track_config import (
    NOTI_TEXT, NOTI_CHANNEL, TARGET, NOTI_IDENTITY_GROUP, guild_id, MODE, CHANGE_TIME
)

logger = logging.getLogger('track_welcome')

class TRACK_WELCOME(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.initial_count = None
        self.target_count = None
        self.has_notified = False
        self.status_msg = None
        self.start_time = None

    async def cog_load(self):
        """当 Cog 被加载时自动尝试初始化"""
        await self.bot.wait_until_ready()
        
        guild = self.bot.get_guild(int(guild_id))
        if guild:
            logger.debug(f"Cog 加载：尝试自动初始化服务器 {guild.name}")
            await self._ensure_initialized(guild)
        else:
            logger.error(f"Cog 加载失败：找不到指定的服务器 ID {guild_id}")

    def cog_unload(self):
        self.update_status_task.cancel()

    async def _ensure_initialized(self, guild):
        """初始化基础人数、目标人数及发送状态消息"""
        if guild.id != int(guild_id):
            return False

        if self.initial_count is not None:
            return True

        self.initial_count = guild.member_count
        self.start_time = datetime.now()
        
        # MODE=0: 相对增长模式 | MODE=1: 绝对目标模式
        if int(MODE) == 0:
            self.target_count = self.initial_count + int(TARGET)
            mode_desc = f"循环增长模式 (每增加 {TARGET} 人提醒)"
        else:
            self.target_count = int(TARGET)
            mode_desc = f"固定目标模式 (达到 {TARGET} 人提醒)"
            
        logger.debug(f"计数器初始化 [{guild.name}]: 初始 {self.initial_count}人, 目标 {self.target_count}人")
        
        channel = self.bot.get_channel(int(NOTI_CHANNEL))
        if channel and self.status_msg is None:
            embed = self._create_status_embed(guild, mode_desc)
            try:
                self.status_msg = await channel.send(embed=embed)
                logger.debug(f"初始化状态消息已发送至频道: {NOTI_CHANNEL}")
                if not self.update_status_task.is_running():
                    self.update_status_task.start()
            except Exception as e:
                logger.error(f"发送初始化报告失败: {e}")
        return True

    def _create_status_embed(self, guild, mode_desc):
        """创建用于显示状态的 Embed"""
        current_count = guild.member_count
        elapsed = datetime.now() - self.start_time

        hours, remainder = divmod(int(elapsed.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        time_str = f"{hours}小时 {minutes}分 {seconds}秒"

        embed = discord.Embed(
            title="Track追踪任务状态",
            color=discord.Color.green(),
            description="正在实时监控服务器人数变动..."
        )
        embed.add_field(name="当前人数", value=f"`{current_count}`", inline=True)
        embed.add_field(name="目标人数", value=f"`{self.target_count}`", inline=True)
        embed.add_field(name="运行模式", value=mode_desc, inline=False)
        embed.add_field(name="初始人数", value=f"`{self.initial_count}`", inline=True)

        embed.set_footer(text=f"状态：Active | 上次更新时间：{datetime.now().strftime('%H:%M:%S')}")
        return embed

    @tasks.loop(seconds=float(CHANGE_TIME if CHANGE_TIME else 3600))
    async def update_status_task(self):
        """每隔 CHANGE_TIME 自动更新一次 Embed 内容"""
        if self.status_msg:
            guild = self.bot.get_guild(int(guild_id))
            if guild:
                mode_desc = f"循环增长" if int(MODE) == 0 else "固定目标"
                try:
                    new_embed = self._create_status_embed(guild, mode_desc)
                    await self.status_msg.edit(embed=new_embed)
                    logger.debug(f"已执行定时更新：当前人数 {guild.member_count}")
                except Exception as e:
                    logger.debug(f"定时更新 Embed 失败 (可能消息被删): {e}")

    @update_status_task.before_loop
    async def before_update_status_task(self):
        await self.bot.wait_until_ready()

    def _format_noti_text(self, current_count):
        actual_growth = current_count - self.initial_count
        welcome_new = int(TARGET) if int(MODE) == 0 else (int(TARGET) - self.initial_count)
        text = NOTI_TEXT.replace("{{current_members}}", str(current_count))
        text = text.replace("{{welcome新增人数}}", str(welcome_new))
        text = text.replace("{{实际增长人数}}", str(actual_growth))
        return text

    def _get_mentions(self):
        if not NOTI_IDENTITY_GROUP:
            return ""
        role_ids = [rid.strip() for rid in str(NOTI_IDENTITY_GROUP).split(",") if rid.strip()]
        return " ".join([f"<@&{rid}>" for rid in role_ids])

    async def check_and_notify(self, guild):
        if not await self._ensure_initialized(guild):
            return
        current_count = guild.member_count
        if current_count >= self.target_count and not self.has_notified:
            channel = self.bot.get_channel(int(NOTI_CHANNEL))
            if channel:
                mentions = self._get_mentions()
                content = self._format_noti_text(current_count)
                await channel.send(f"{mentions}\n{content}")
                if int(MODE) == 0:
                    while self.target_count <= current_count:
                        self.target_count += int(TARGET)
                else:
                    self.has_notified = True
                logger.debug(f"目标达成，通知已发送。")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        await self.check_and_notify(member.guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if not await self._ensure_initialized(member.guild):
            return
        current_count = member.guild.member_count
        if current_count < self.target_count:
            if self.has_notified:
                self.has_notified = False

async def setup(bot):
    await bot.add_cog(TRACK_WELCOME(bot))