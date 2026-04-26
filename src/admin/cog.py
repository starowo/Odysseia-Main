import asyncio
import io
import os
from discord.ext import commands
from discord import Attachment, app_commands, file
import discord
import json
import uuid
import datetime
import pathlib
from typing import List, Tuple, Optional

from src.utils import dm
from src.utils.confirm_view import confirm_view, confirm_view_embed
from src.utils.auth import is_admin, is_senior_admin, check_admin_permission, is_admin_member, guild_only
from src.utils.config_helper import get_config_value, get_config_for_guild

# ---- 持久视图：删除子区审批 ----
class ThreadDeleteApprovalView(discord.ui.View):
    """一个持久视图，收集管理员对删除子区的投票。

    需要至少 5 位管理员点击同意才会执行删除；任何管理员点击拒绝即刻否决。
    """

    def __init__(self, cog: "AdminCommands", thread: discord.Thread, initiator: discord.Member):
        super().__init__(timeout=None)  # 持久视图
        self.cog = cog
        self.thread = thread
        self.initiator = initiator
        self.approvals: set[int] = set()
        self.denied: bool = False
        self.message: discord.Message | None = None  # 由外部在发送后赋值

    async def _is_admin(self, interaction: discord.Interaction) -> bool:
        """校验交互用户是否为管理员。"""
        return await check_admin_permission(interaction)

    async def _refresh_message(self):
        """更新原始消息中的进度显示。"""
        if self.message and not self.denied:
            content = f"🗑️ 删除子区投票进行中：已获得 {len(self.approvals)}/5 位管理员同意。"
            await self.message.edit(content=content, view=self)

    @discord.ui.button(label="✅ 同意删除", style=discord.ButtonStyle.green, custom_id="thread_delete_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore
        # 权限校验
        if not await self._is_admin(interaction):
            await interaction.response.send_message("❌ 只有管理员可以操作该按钮", ephemeral=True)
            return

        if self.denied:
            await interaction.response.send_message("❌ 该请求已被否决", ephemeral=True)
            return

        # 记录同意
        self.approvals.add(interaction.user.id)
        await interaction.response.send_message(f"✅ 已记录您的同意 (当前 {len(self.approvals)}/5)", ephemeral=True)

        # 刷新进度
        await self._refresh_message()

        # 判断是否达到删除条件
        if len(self.approvals) >= 5:
            try:
                name = self.thread.name
                await self.thread.delete(reason=f"管理员共识删除 by {interaction.user}")
                # 记录删除日志
                moderation_log_channel_id = self.config.get("moderation_log_channel_id", 0)
                if moderation_log_channel_id:
                    await interaction.guild.get_channel_or_thread(int(moderation_log_channel_id)).send(embed=discord.Embed(title="🔴 子区删除", description=f"【{name}】已被管理员共识删除。"))
                if self.message:
                    await self.message.edit(content=f"✅ 线程【{name}】已被删除", view=None)
            except Exception as e:
                if self.message:
                    await self.message.edit(content=f"❌ 删除线程失败: {e}", view=None)
            finally:
                self.stop()

    @discord.ui.button(label="❌ 拒绝删除", style=discord.ButtonStyle.red, custom_id="thread_delete_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):  # type: ignore
        # 权限校验
        if not await self._is_admin(interaction):
            await interaction.response.send_message("❌ 只有管理员可以操作该按钮", ephemeral=True)
            return

        # 记录否决
        self.denied = True
        await interaction.response.send_message("已否决删除请求", ephemeral=True)

        if self.message:
            await self.message.edit(content=f"❌ 删除请求已被 {interaction.user.mention} 否决", view=None)

        self.stop()

class AdminCommands(commands.Cog):
    def __init__(self, bot):
        self.bot: discord.Client = bot
        self.logger = bot.logger
        self.name = "管理命令"
        # 初始化配置缓存
        self._config_cache = {}
        self._config_cache_mtime = None
    
    admin = app_commands.Group(name="管理", description="管理员专用命令")
    
    @commands.Cog.listener()
    async def on_ready(self):
        if self.logger:
            self.logger.info("管理命令已加载")
        # 启动警告自动移除任务
        self.auto_remove_warn_task = asyncio.create_task(self._auto_remove_warn())
        if self.logger:
            self.logger.info("警告自动移除任务已启动")
        # 启动永封审查自动处理任务
        self.auto_ban_checker_task = asyncio.create_task(self._auto_ban_checker())
        if self.logger:
            self.logger.info("永封审查自动处理任务已启动")
        
        # 初始化答题处罚记录
        self.quiz_punish_init_task = asyncio.create_task(self._quiz_punish_init())

    async def on_disable(self):
        if self.auto_remove_warn_task and not self.auto_remove_warn_task.done():
            self.auto_remove_warn_task.cancel()
        if self.auto_ban_checker_task and not self.auto_ban_checker_task.done():
            self.auto_ban_checker_task.cancel()
        if self.quiz_punish_init_task and not self.quiz_punish_init_task.done():
            self.quiz_punish_init_task.cancel()
    
    async def _auto_remove_warn(self):
        while True:
            # 每小时检查一次
            await asyncio.sleep(60 * 60)
            base_dir = pathlib.Path("data") / "warn"
            if not base_dir.exists():
                continue
                
            for guild_dir in base_dir.iterdir():
                if not guild_dir.is_dir():
                    continue
                    
                try:
                    guild_id = int(guild_dir.name)
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        continue
                        
                    # 遍历警告文件，时间到则移除并删除文件
                    if guild_dir.exists():
                        to_remove_warn = dict()
                        for file in guild_dir.glob("*.json"):

                            try:
                                with open(file, "r", encoding="utf-8") as f:
                                    warn_record = json.load(f)
                                    
                                if warn_record.get("until", None):
                                    user_id = warn_record.get("user_id")
                                    until_time = datetime.datetime.fromisoformat(warn_record["until"])
                                    timestamp = datetime.datetime.fromisoformat(warn_record.get("timestamp"))
                                    if user_id not in to_remove_warn:
                                        to_remove_warn[user_id] = (until_time, timestamp)
                                    elif timestamp > to_remove_warn[user_id][1]:
                                        # 如果有多个警告，则更新为最新的警告时间
                                        to_remove_warn[user_id] = (until_time, timestamp)
                                    if datetime.datetime.now(datetime.timezone.utc) > until_time:
                                        # 删除过期的记录文件
                                        file.unlink(missing_ok=True)
                            except Exception as e:
                                if self.logger:
                                    self.logger.error(f"处理警告文件失败: {file}, 错误: {e}")
                        for user_id, (until_time, timestamp) in to_remove_warn.items():
                            if datetime.datetime.now(datetime.timezone.utc) > until_time:
                                # 获取用户对象并移除警告身份组
                                if user_id:
                                    try:
                                        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
                                        if member:
                                            # 使用服务器特定配置
                                            warned_role_id = self.get_guild_config("warned_role_id", guild.id, 0)
                                            warned_role = guild.get_role(int(warned_role_id)) if warned_role_id else None
                                            if warned_role and warned_role in member.roles:
                                                sync_cog = self.bot.get_cog("ServerSyncCommands")
                                                if sync_cog:
                                                    sync_cog._mark_guard(guild.id, member.id, warned_role.id, "remove")
                                                await member.remove_roles(warned_role, reason=f"警告到期自动移除 by {self.bot.user}")
                                                if self.logger:
                                                    self.logger.info(f"自动移除警告: 用户 {member} (ID: {user_id}) 在服务器 {guild.name}")
                                    except Exception as e:
                                        if self.logger:
                                            self.logger.error(f"移除警告身份组失败: 用户ID {user_id}, 错误: {e}")
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"处理服务器警告目录失败: {guild_dir}, 错误: {e}")
                    continue

    async def _auto_ban_checker(self):
        """后台任务，定期检查并处理到期的永封审查。"""
        while True:
            # 每小时检查一次
            await asyncio.sleep(60 * 60)
            base_dir = pathlib.Path("data") / "pending_bans"
            if not base_dir.exists():
                continue

            for guild_dir in base_dir.iterdir():
                if not guild_dir.is_dir():
                    continue

                try:
                    guild_id = int(guild_dir.name)
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        continue

                    for file in guild_dir.glob("*.json"):
                        try:
                            with open(file, "r", encoding="utf-8") as f:
                                record = json.load(f)

                            expires_at = datetime.datetime.fromisoformat(record["expires_at"])
                            if datetime.datetime.now(datetime.timezone.utc) > expires_at:
                                user_id = record["user_id"]
                                reason = f"{record.get('reason', 'N/A')}"
                                appeal_thread_id = record.get("appeal_thread_id")
                                
                                try:
                                    await guild.ban(discord.Object(id=user_id), reason=reason)
                                    if self.logger:
                                        self.logger.info(f"永封审查到期，已自动在服务器 {guild.name} 中封禁用户 {user_id}")

                                    # 锁定帖子
                                    if appeal_thread_id:
                                        try:
                                            thread = await self.bot.fetch_channel(appeal_thread_id)
                                            await thread.edit(locked=True, archived=True, reason="审查到期，自动关闭")
                                        except Exception as e:
                                            if self.logger:
                                                self.logger.warning(f"无法自动锁定申诉帖 {appeal_thread_id}: {e}")

                                    # 公示（使用服务器特定配置）
                                    channel_id = self.get_guild_config("punish_announce_channel_id", guild.id, 0)
                                    announce_channel = guild.get_channel(int(channel_id)) if channel_id else None
                                    moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", guild.id, 0)
                                    moderation_log_channel = guild.get_channel_or_thread(int(moderation_log_channel_id)) if moderation_log_channel_id else None
                                    if announce_channel or moderation_log_channel:
                                        embed = discord.Embed(title="⛔ 永封审查通过", color=discord.Color.red())
                                        embed.add_field(name="成员", value=f"<@{user_id}> ({user_id})")
                                        embed.add_field(name="审查原因", value=reason, inline=False)
                                        if appeal_thread_id:
                                            embed.add_field(name="申诉帖", value=f"<#{appeal_thread_id}>", inline=False)
                                        embed.set_footer(text=f"审查ID: {record['id']}")
                                    if announce_channel:
                                        await announce_channel.send(embed=embed)
                                    if moderation_log_channel:
                                        await moderation_log_channel.send(embed=embed)
                                     # 删除记录文件
                                    file.unlink(missing_ok=True)

                                except discord.Forbidden:
                                    if self.logger:
                                        self.logger.error(f"自动封禁失败（无权限）: 用户 {user_id}")
                                except Exception as e:
                                    if self.logger:
                                        self.logger.error(f"自动封禁时发生错误: {e}")

                        except Exception as e:
                            if self.logger:
                                self.logger.error(f"处理永封审查文件失败: {file}, 错误: {e}")
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"处理服务器永封审查目录失败: {guild_dir}, 错误: {e}")
                    continue

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
    
    def get_guild_config(self, key: str, guild_id: Optional[int] = None, default=None):
        """获取服务器特定配置值"""
        return get_config_value(key, guild_id, default)
    
    
    # ---- 工具函数：将字符串时间转换为数字时长 ----
    def _parse_time(self, time_str: str) -> tuple[int, str]:
        if time_str == "0":
            return 0, "0秒"
        """将字符串时间转换为数字时长"""
        if time_str.endswith("m"):
            return int(time_str[:-1]) * 60, time_str[:-1] + "分钟"
        elif time_str.endswith("h"):
            return int(time_str[:-1]) * 3600, time_str[:-1] + "小时"
        elif time_str.endswith("d"):
            return int(time_str[:-1]) * 86400, time_str[:-1] + "天"
        else:
            return -1, "未知时间"

    def _truncate_embed_value(self, value: str, limit: int = 1024) -> str:
        """限制 embed field 内容长度，避免 Discord 拒绝发送。"""
        if value is None:
            value = "未提供"
        value = str(value)
        if len(value) <= limit:
            return value
        return value[:limit - 3] + "..."

    def _format_target_identity(self, user) -> tuple[str, str, str]:
        """返回用户的服务器昵称、账号名和头像 URL。"""
        nickname = (
            getattr(user, "display_name", None)
            or getattr(user, "global_name", None)
            or getattr(user, "name", None)
            or str(user)
        )
        username = getattr(user, "name", None) or str(user)
        discriminator = getattr(user, "discriminator", None)
        if discriminator and discriminator != "0" and "#" not in username:
            username = f"{username}#{discriminator}"

        avatar = getattr(user, "display_avatar", None)
        avatar_url = getattr(avatar, "url", None) if avatar else None
        return nickname, username, avatar_url

    def _build_punishment_confirm_embed(
        self,
        *,
        title: str,
        target,
        target_id: int,
        punishment_lines: List[str],
        reason: Optional[str],
        moderator,
        colour: discord.Colour,
        target_avatar_url: Optional[str] = None,
        target_mention: Optional[str] = None,
        attachment: Optional[discord.Attachment] = None,
        timeout: int = 60,
    ) -> discord.Embed:
        nickname, username, avatar_url = self._format_target_identity(target)
        avatar_url = target_avatar_url or avatar_url
        mention = target_mention or getattr(target, "mention", f"<@{target_id}>")

        embed = discord.Embed(
            title=title,
            description="请确认处罚信息无误，确认后将立即执行。",
            colour=colour,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.add_field(
            name="Discord昵称",
            value=self._truncate_embed_value(nickname),
            inline=True,
        )
        embed.add_field(
            name="名字",
            value=self._truncate_embed_value(username),
            inline=True,
        )
        embed.add_field(name="数字ID", value=f"`{target_id}`", inline=True)
        embed.add_field(name="用户", value=mention, inline=False)
        embed.add_field(
            name="处罚内容",
            value=self._truncate_embed_value("\n".join(punishment_lines)),
            inline=False,
        )
        embed.add_field(
            name="原因",
            value=self._truncate_embed_value(reason or "未提供"),
            inline=False,
        )
        embed.add_field(name="执行者", value=moderator.mention, inline=True)
        if attachment:
            embed.add_field(
                name="公告图片",
                value=self._truncate_embed_value(attachment.filename),
                inline=True,
            )
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        embed.set_footer(text=f"仅命令使用者可确认，按钮将在 {timeout} 秒后失效")
        return embed
    
    # ---- 工具函数：发送处罚公告并保存记录 ----
    def _save_punish_record(self, guild_id: int, record: dict):
        """保存处罚记录到 data/punish 目录，文件名为 id.json"""
        record_id = uuid.uuid4().hex[:8]
        record["id"] = record_id
        record["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

        punish_dir = pathlib.Path("data") / "punish" / str(guild_id)
        punish_dir.mkdir(parents=True, exist_ok=True)
        with open(punish_dir / f"{record_id}.json", "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        return record_id

    def _get_punish_record(self, guild_id: int, record_id: str):
        path = pathlib.Path("data") / "punish" / str(guild_id) / f"{record_id}.json"
        if not path.exists():
            return None, path
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), path
        
    def _save_warn_record(self, guild_id: int, record: dict):
        record_id = uuid.uuid4().hex[:8]
        record["id"] = record_id
        record["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

        warn_dir = pathlib.Path("data") / "warn" / str(guild_id)
        warn_dir.mkdir(parents=True, exist_ok=True)
        with open(warn_dir / f"{record_id}.json", "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        return record_id

    def _save_pending_ban_record(self, guild_id: int, record: dict):
        """保存永封审查记录到 data/pending_bans 目录"""
        record_id = uuid.uuid4().hex[:8]
        record["id"] = record_id
        record["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

        pending_ban_dir = pathlib.Path("data") / "pending_bans" / str(guild_id)
        pending_ban_dir.mkdir(parents=True, exist_ok=True)
        with open(pending_ban_dir / f"{record_id}.json", "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        return record_id

    def _get_pending_ban_record(self, guild_id: int, record_id: str):
        """获取永封审查记录"""
        path = pathlib.Path("data") / "pending_bans" / str(guild_id) / f"{record_id}.json"
        if not path.exists():
            return None, path
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), path

    # ---- 添加/移除身份组 ----
    @admin.command(name="身份组", description="添加/移除身份组")
    @app_commands.describe(
        member="成员",
        action="操作",
        role="身份组",
        reason="原因"
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="添加", value="添加"),
            app_commands.Choice(name="移除", value="移除"),
        ]
    )
    @is_admin()
    @guild_only()
    async def add_role(
        self,
        interaction, # type: discord.Interaction
        member: "discord.Member",
        action: str,
        role: "discord.Role",
        reason: Optional[str] = None,
    ):
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)

        # 阻止用户操作比自己权限高的身份组
        if role.position >= interaction.user.top_role.position:
            await interaction.followup.send("❌ 无法操作比自己权限高的身份组", ephemeral=True)
            return

        if action == "添加":
            # 检查是否启用同步模块
            sync_cog = self.bot.get_cog("ServerSyncCommands")
            if sync_cog:
                await sync_cog.sync_add_role(guild, member, role, reason)
            else:
                await member.add_roles(role, reason=reason)
        elif action == "移除":
            # 检查是否启用同步模块
            sync_cog = self.bot.get_cog("ServerSyncCommands")
            if sync_cog:
                await sync_cog.sync_remove_role(guild, member, role, reason)
            else:
                await member.remove_roles(role, reason=reason)
        
        await interaction.followup.send(f"✅ 已{action}身份组 {role.mention} {member.mention}", ephemeral=True)

    # ---- 批量删除消息 ----
    @admin.command(name="批量删除消息", description="在当前频道，从指定消息开始到指定消息结束，删除全部消息")
    @app_commands.describe(
        start_message="开始消息链接",
        end_message="结束消息链接"
    )
    @is_senior_admin()
    async def bulk_delete_messages(
        self,
        interaction, # type: discord.Interaction
        start_message: str,
        end_message: str,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = interaction.channel
        if channel is None:
            await interaction.followup.send("此命令只能在频道中使用", ephemeral=True)
            return
        
        try:
            start_message_obj = await channel.fetch_message(int(start_message.split("/")[-1]))
            end_message_obj = await channel.fetch_message(int(end_message.split("/")[-1]))
        except (ValueError, discord.NotFound):
            await interaction.followup.send("❌ 无效的消息链接或消息不存在", ephemeral=True)
            return
            
        if start_message_obj.channel.id != channel.id or end_message_obj.channel.id != channel.id:
            await interaction.followup.send("消息必须在当前频道", ephemeral=True)
            return
        if start_message_obj.created_at > end_message_obj.created_at:
            await interaction.followup.send("开始消息必须在结束消息之前", ephemeral=True)
            return
        
        # 调用统一的确认视图
        confirmed = await confirm_view(
            interaction,
            title="批量删除消息",
            description="\n".join(
                [
                    f"确定要删除从 {start_message_obj.created_at} 到 {end_message_obj.created_at} 的消息吗？",
                ]
            ),
            colour=discord.Colour(0x808080),
            timeout=60,
        )

        if not confirmed:
            return

        deleted = 0
        current_after = start_message_obj.created_at - datetime.timedelta(seconds=1)  # 稍早于起始消息以包含它
        
        # 分批删除消息
        while True:
            fetched: List[discord.Message] = []
            backup_text = ""
            async for message in channel.history(limit=100, after=current_after, before=end_message_obj.created_at + datetime.timedelta(seconds=1)):
                # 确保消息在时间范围内
                if start_message_obj.created_at <= message.created_at <= end_message_obj.created_at:
                    fetched.append(message)
                    backup_text += f"{message.author.name}({message.author.id}): {message.content}\n"
            if len(fetched) == 0:
                break
                
            try:
                # Discord批量删除有限制，超过14天的消息需要单独删除
                bulk_delete_messages = []
                old_messages = []
                now = datetime.datetime.now(datetime.timezone.utc)
                
                for msg in fetched:
                    if (now - msg.created_at).days < 14:
                        bulk_delete_messages.append(msg)
                    else:
                        old_messages.append(msg)
                
                # 批量删除新消息
                if bulk_delete_messages:
                    await channel.delete_messages(bulk_delete_messages)
                    deleted += len(bulk_delete_messages)
                
                # 单独删除旧消息
                for msg in old_messages:
                    try:
                        await msg.delete()
                        deleted += 1
                    except discord.NotFound:
                        # 消息已被删除，跳过
                        pass
                        
            except discord.Forbidden:
                await interaction.followup.send("❌ 没有删除消息的权限", ephemeral=True)
                return
            except Exception as e:
                await interaction.followup.send(f"❌ 删除消息时出错: {str(e)}", ephemeral=True)
                return
            
            # 更新进度
            await interaction.edit_original_response(content=f"已删除 {deleted} 条消息")
            
            # 更新current_after为最后一条处理的消息时间
            if fetched:
                current_after = fetched[-1].created_at
            else:
                break
                
        # 记录删除日志
        # 临时保存备份文本
        with open(".backup.txt", "w") as f:
            f.write(backup_text)
            f.close()
        backup_file = discord.File(".backup.txt")

        # 使用服务器特定配置
        moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", interaction.guild.id, 0)
        if moderation_log_channel_id:
            await interaction.guild.get_channel_or_thread(int(moderation_log_channel_id)).send(
                embed=discord.Embed(title="🔴 批量删除消息", description=f"管理员 {interaction.user.mention} 在 {channel.mention} 批量删除了 {deleted} 条消息。"),
                files=[backup_file]
            )
        # 删除临时文件
        os.remove(".backup.txt")
        backup_file.close()

        await interaction.followup.send(f"✅ 已删除 {deleted} 条消息", ephemeral=True)

    # ---- 批量转移身份组 ----
    @admin.command(name="批量转移身份组", description="给具有指定身份组的成员添加新身份组，可选是否移除原身份组")
    @app_commands.describe(
        source_role="需要转移的原身份组",
        target_role="要添加的新身份组",
        remove_source="是否移除原身份组",
        limit="限制转移数量(0为全部转移)"
    )
    @app_commands.rename(source_role="原身份组", target_role="新身份组", remove_source="移除原身份组", limit="限制数量")
    @is_senior_admin()
    async def bulk_move_role(
        self,
        interaction,  # type: discord.Interaction
        source_role: "discord.Role",
        target_role: "discord.Role",
        remove_source: bool = False,
        limit: int = 100
    ):
        guild: discord.Guild = interaction.guild

        await interaction.response.defer(ephemeral=True, thinking=True)

        # 防止越权
        if source_role.position >= interaction.user.top_role.position or target_role.position >= interaction.user.top_role.position:
            await interaction.followup.send("❌ 无法操作比自己权限高的身份组", ephemeral=True)
            return
        
        # 操作确认
        confirmed = await confirm_view(
            interaction,
            title="批量转移身份组",
            description=f"确定要转移 {limit} 名成员的身份组吗？",
            colour=discord.Colour(0x808080),
            timeout=60,
        )

        if not confirmed:
            return

        await interaction.edit_original_response(content="正在加载成员...")

        
        
        members = source_role.members

        await interaction.edit_original_response(content=f"已加载 {len(members)} 名成员")
            
            
        # 如果有数量限制，则先按加入时间排序
        if limit > 0:
            members.sort(key=lambda x: x.joined_at)

        affected = 0

        for member in members:
            # 如果已有目标身份组，则跳过
            if target_role in member.roles:
                continue
            try:
                # 检查是否启用同步模块
                sync_cog = self.bot.get_cog("ServerSyncCommands")
                if sync_cog:
                    await sync_cog.sync_add_role(guild, member, target_role, f"批量转移身份组 by {interaction.user}")
                    if remove_source:
                        await sync_cog.sync_remove_role(guild, member, source_role, f"批量转移身份组 remove source by {interaction.user}")
                else:
                    await member.add_roles(target_role, reason=f"批量转移身份组 by {interaction.user}")
                    if remove_source:
                        await member.remove_roles(source_role, reason=f"批量转移身份组 remove source by {interaction.user}")
                affected += 1
                if affected % 10 == 0:
                    await interaction.edit_original_response(content=f"已转移 {affected} 名成员")
                if affected >= limit and limit > 0:
                    break
            except discord.Forbidden:
                continue
        await interaction.edit_original_response(content=f"✅ 已对 {affected} 名成员完成身份组转移")

    @admin.command(name="发送私聊", description="发送私聊")
    @app_commands.describe(
        member="要发送私聊的成员",
        message="要发送的消息",
        img="附图（可选）"
    )
    @app_commands.rename(member="成员", message="消息", img="附图")
    @is_admin()
    @guild_only()
    async def send_private_message(
        self,
        interaction,  # type: discord.Interaction
        member: "discord.Member",
        message: str,
        img: discord.Attachment = None,
    ):
        guild = interaction.guild
        member = interaction.guild.get_member(member.id) or await interaction.guild.fetch_member(member.id)
        if not member:
            await interaction.response.send_message("❌ 成员不存在", ephemeral=True)    
            return
        try:
            img_bytes = None
            img_filename = None
            if img:
                img_bytes = await img.read()
                img_filename = img.filename

            embed = discord.Embed(
                title="来自管理组的私聊消息",
                description=message,
                color=discord.Color.blue()
            )
            embed.set_footer(text=f"来自服务器: {guild.name}")
            if img_bytes:
                embed.set_image(url=f"attachment://{img_filename}")
            await dm.send_dm(guild=guild, user=member, embed=embed,
                             file=discord.File(io.BytesIO(img_bytes), filename=img_filename) if img_bytes else None)
            # 记录私聊日志（使用服务器特定配置）
            moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", guild.id, 0)
            if moderation_log_channel_id:
                embed = discord.Embed(
                    title="🔴 私聊消息",
                    description=f"管理员 {interaction.user.mention} 向 {member.mention} 发送了私聊消息。",
                    color=discord.Color.blue()
                )
                embed.add_field(name="消息内容", value=message)
                if img_bytes:
                    embed.set_image(url=f"attachment://{img_filename}")
                await interaction.guild.get_channel_or_thread(int(moderation_log_channel_id)).send(
                    embed=embed,
                    file=discord.File(io.BytesIO(img_bytes), filename=img_filename) if img_bytes else None,
                )
            await interaction.response.send_message("✅ 私聊发送成功", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ 无权限对该成员发送私聊", ephemeral=True)
            return
        except Exception as e:
            await interaction.response.send_message(f"❌ 发送私聊失败: {e}", ephemeral=True)
            return

    # ---- 禁言 ----
    @admin.command(name="禁言", description="将成员禁言（最长28天）并公示")
    @app_commands.describe(
        member="要禁言的成员",
        time="禁言时长（5m, 12h, 3d）",
        reason="原因（可选）",
        img="图片（可选）",
        warn="警告天数"
    )
    @app_commands.rename(member="成员", time="时长", reason="原因", img="图片", warn="警告天数")
    @is_admin()
    @guild_only()
    async def mute_member(
        self,
        interaction,  # type: discord.Interaction
        member: "discord.Member",
        time: str = "0d",
        reason: str = None,
        img: discord.Attachment = None,
        warn: int = 0,
    ):
        guild = interaction.guild
        # 将字符串时间转换为数字时长
        mute_time, mute_time_str = self._parse_time(time)
        if mute_time == -1:
            await interaction.response.send_message("❌ 未知时间", ephemeral=True)
            return
        
        duration = datetime.timedelta(seconds=mute_time)

        await interaction.response.defer(ephemeral=True)
        if duration.total_seconds() <= 0 and warn <= 0:
            await interaction.followup.send("❌ 时长和警告天数不能同时为0", ephemeral=True)
            return

        punishment_lines = []
        if duration.total_seconds() > 0:
            punishment_lines.append(f"禁言：{mute_time_str}")
        else:
            punishment_lines.append("禁言：不执行")
        if warn > 0:
            punishment_lines.append(f"警告：{warn}天")
        else:
            punishment_lines.append("警告：不执行")

        confirm_embed = self._build_punishment_confirm_embed(
            title="🔇 禁言处罚确认",
            target=member,
            target_id=member.id,
            punishment_lines=punishment_lines,
            reason=reason,
            moderator=interaction.user,
            colour=discord.Colour.orange(),
            attachment=img,
            timeout=60,
        )
        confirmed = await confirm_view_embed(interaction, embed=confirm_embed, timeout=60)
        if not confirmed:
            return

        try:
            if duration.total_seconds() > 0:
                await member.timeout(duration, reason=reason or "管理员禁言")
            # 使用服务器特定配置
            warned_role_id = self.get_guild_config("warned_role_id", guild.id, 0)
            warned_role = guild.get_role(int(warned_role_id)) if warned_role_id else None
            if warned_role and warn > 0:
                sync_cog_ref = self.bot.get_cog("ServerSyncCommands")
                if sync_cog_ref:
                    sync_cog_ref._mark_guard(guild.id, member.id, warned_role.id, "add")
                await member.add_roles(warned_role, reason=f"处罚附加警告 {warn} 天")
        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限对该成员执行禁言", ephemeral=True)
            return

        # 保存记录 & 公示
        record_id = self._save_punish_record(guild.id, {
            "type": "mute",
            "user_id": member.id,
            "moderator_id": interaction.user.id,
            "reason": reason,
            "warn": warn,
            "duration": duration.total_seconds(),
        })

        if warn > 0:
            self._save_warn_record(guild.id, {
                "type": "warn",
                "user_id": member.id,
                "moderator_id": interaction.user.id,
                "reason": reason,
                "until": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=warn)).isoformat(),
            })

        # 同步处罚到其他服务器
        sync_cog = self.bot.get_cog("ServerSyncCommands")
        if sync_cog:
            await sync_cog.sync_punishment(
                guild=guild,
                punishment_type="mute",
                member=member,
                moderator=interaction.user,
                reason=reason,
                duration=duration.total_seconds() if duration.total_seconds() > 0 else None,
                warn_days=warn,
                punishment_id=record_id,
                img=img
            )

        await interaction.followup.send(f"✅ 已禁言 {member.mention} ({mute_time_str})。处罚ID: `{record_id}`", ephemeral=True)

        # 私聊通知
        guild_name = guild.name
        if duration.total_seconds() > 0:
            try:
                dm_embed = discord.Embed(title="🔇 禁言处罚", description=f"您在 **{guild_name}** 因 **{reason}** 被禁言 {mute_time_str}。请注意遵守社区规则。")
                dm_embed.set_footer(text=f"来自服务器: {guild_name}")
                await dm.send_dm(member.guild, member, embed=dm_embed)
            except discord.Forbidden:
                pass
            except Exception as e:
                self.logger.error(f"禁言处罚私聊通知失败: {e}")
        elif warn > 0:
            try:
                dm_embed = discord.Embed(title="⚠️ 警告处罚", description=f"您在 **{guild_name}** 因 **{reason}** 被警告 {warn} 天。请注意遵守社区规则。")
                dm_embed.set_footer(text=f"来自服务器: {guild_name}")
                await dm.send_dm(member.guild, member, embed=dm_embed)
            except discord.Forbidden:
                pass
            except Exception as e:
                self.logger.error(f"警告处罚私聊通知失败: {e}")

        # 当前频道公示
        if duration.total_seconds() > 0:
            await interaction.followup.send(embed=discord.Embed(title="🔇 禁言处罚", description=f"{member.mention} 因 {reason} 被禁言 {mute_time_str}。请注意遵守社区规则。"), ephemeral=False)
        elif warn > 0:
            await interaction.followup.send(embed=discord.Embed(title="⚠️ 警告处罚", description=f"{member.mention} 因 {reason} 被警告 {warn} 天。请注意遵守社区规则。"), ephemeral=False)

        # 公示频道 + 记录日志（使用服务器特定配置）
        channel_id = self.get_guild_config("punish_announce_channel_id", guild.id, 0)
        announce_channel = guild.get_channel(int(channel_id)) if channel_id else None
        moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", guild.id, 0)
        moderation_log_channel = guild.get_channel_or_thread(int(moderation_log_channel_id)) if moderation_log_channel_id else None
        if announce_channel or moderation_log_channel:
            img_bytes = None
            img_filename = None
            if img:
                try:
                    img_bytes = await img.read()
                    img_filename = img.filename
                except Exception:
                    pass
            embed = discord.Embed(title="🔇 禁言处罚" if duration.total_seconds() > 0 else "⚠️ 警告处罚", color=discord.Color.orange())
            if duration.total_seconds() > 0:
                embed.add_field(name="时长", value=mute_time_str)
            embed.add_field(name="成员", value=member.mention)
            embed.add_field(name="管理员", value=interaction.user.mention)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="原因", value=reason or "未提供", inline=False)
            if warn > 0:
                embed.add_field(name="警告", value=f"{warn}天", inline=False)
            if img_bytes:
                embed.set_image(url=f"attachment://{img_filename}")
            embed.set_footer(text=f"处罚ID: {record_id}")
            if announce_channel:
                await announce_channel.send(
                    embed=embed,
                    file=discord.File(io.BytesIO(img_bytes), filename=img_filename) if img_bytes else None,
                )
            if moderation_log_channel:
                await moderation_log_channel.send(
                    embed=embed,
                    file=discord.File(io.BytesIO(img_bytes), filename=img_filename) if img_bytes else None,
                )

    # ---- 踢出 ----
    @admin.command(name="踢出", description="踢出成员并公示")
    @app_commands.describe(member="要踢出的成员", reason="原因（可选）", img="图片（可选）")
    @app_commands.rename(member="成员", reason="原因", img="图片")
    @is_senior_admin()
    @guild_only()
    async def kick_member(
        self,
        interaction,  # type: discord.Interaction
        member: "discord.Member",
        reason: str = None,
        img: discord.Attachment = None,
    ):
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)

        # 私聊通知
        try:
            kick_embed = discord.Embed(title="👋 移出服务器", description=f"您因 **{reason}** 被踢出 **{guild.name}**。如有异议，请联系管理组成员。")
            kick_embed.set_footer(text=f"来自服务器: {guild.name}")
            await dm.send_dm(member.guild, member, embed=kick_embed)
        except discord.Forbidden:
            pass
        except Exception:
            pass
        
        # 执行踢出
        try:
            await guild.kick(member, reason=reason)
        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限踢出该成员", ephemeral=True)
            return
        except discord.NotFound:
            await interaction.followup.send("❌ 成员不存在", ephemeral=True)
            return

        # 保存记录 & 公示
        record_id = self._save_punish_record(guild.id, {
            "type": "kick",
            "user_id": member.id,
            "moderator_id": interaction.user.id,
            "reason": reason,
        })

        await interaction.followup.send(f"✅ 已踢出 {member.mention}。处罚ID: `{record_id}`", ephemeral=True)

        # 同步处罚到其他服务器
        sync_cog = self.bot.get_cog("ServerSyncCommands")
        if sync_cog:
            await sync_cog.sync_punishment(
                guild=guild,
                punishment_type="kick",
                member=member,
                moderator=interaction.user,
                reason=reason,
                punishment_id=record_id,
                img=img
            )

        # 当前频道公示
        await interaction.followup.send(embed=discord.Embed(title="👋 移出服务器", description=f"{member.mention} 因 {reason} 被踢出服务器。请注意遵守社区规则。"), ephemeral=False)

        # 公示频道（使用服务器特定配置）
        channel_id = self.get_guild_config("punish_announce_channel_id", guild.id, 0)
        announce_channel = guild.get_channel(int(channel_id)) if channel_id else None
        moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", guild.id, 0)
        moderation_log_channel = guild.get_channel_or_thread(int(moderation_log_channel_id)) if moderation_log_channel_id else None
        if announce_channel or moderation_log_channel:
            img_bytes = None
            img_filename = None
            if img:
                try:
                    img_bytes = await img.read()
                    img_filename = img.filename
                except Exception:
                    pass
            embed = discord.Embed(title="👋 移出服务器", color=discord.Color.orange())
            embed.add_field(name="成员", value=f"{member.mention} ({member.id})")
            embed.add_field(name="管理员", value=interaction.user.mention)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="原因", value=reason or "未提供", inline=False)
            if img_bytes:
                embed.set_image(url=f"attachment://{img_filename}")
            embed.set_footer(text=f"处罚ID: {record_id}")
            if announce_channel:
                await announce_channel.send(
                    embed=embed,
                    file=discord.File(io.BytesIO(img_bytes), filename=img_filename) if img_bytes else None,
                )
            if moderation_log_channel:
                await moderation_log_channel.send(
                    embed=embed,
                    file=discord.File(io.BytesIO(img_bytes), filename=img_filename) if img_bytes else None,
                )

    # ---- 永封 ----
    @admin.command(name="永封", description="永久封禁成员并公示")
    @app_commands.describe(member="要封禁的成员", user_id="用户ID（可直接封禁不在服务器的用户）", reason="原因（可选）", img="图片（可选）", delete_message_days="删除消息天数（0-7）")
    @app_commands.rename(member="成员", user_id="用户id", reason="原因", img="图片", delete_message_days="删除消息天数")
    @is_senior_admin()
    @guild_only()
    async def ban_member(
        self,
        interaction,  # type: discord.Interaction
        member: "discord.Member" = None,
        user_id: str = None,
        reason: str = None,
        img: discord.Attachment = None,
        delete_message_days: int = 0,
    ):
        guild = interaction.guild
        # 验证至少提供了一个参数
        if not member and not user_id:
            await interaction.response.send_message("❌ 请提供要封禁的成员或用户ID", ephemeral=True)
            return
            
        # 验证不能同时提供两个参数
        if member and user_id:
            await interaction.response.send_message("❌ 请只提供成员或用户ID中的一个", ephemeral=True)
            return

        if delete_message_days < 0 or delete_message_days > 7:
            await interaction.response.send_message("❌ 删除消息天数必须在0到7之间", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        
        # 确定要封禁的用户
        target_user = None
        target_user_id = None
        target_user_name = None
        target_user_mention = None
        target_user_avatar = None
        is_member = False
        
        if member:
            # 使用提供的成员对象
            target_user = member
            target_user_id = member.id
            target_user_name = str(member)
            target_user_mention = member.mention
            target_user_avatar = member.display_avatar.url
            is_member = True
        else:
            # 使用用户ID - 先验证ID格式
            try:
                target_user_id = int(user_id)
            except (ValueError, TypeError):
                await interaction.followup.send("❌ 请提供有效的用户ID（纯数字）", ephemeral=True)
                return
                
            try:
                # 尝试获取用户对象（可能不在服务器中）
                target_user = await self.bot.fetch_user(target_user_id)
                target_user_name = str(target_user)
                target_user_mention = f"<@{target_user_id}>"
                target_user_avatar = target_user.display_avatar.url
            except discord.NotFound:
                # 用户不存在
                await interaction.followup.send("❌ 找不到该用户ID对应的用户", ephemeral=True)
                return
            except Exception as e:
                # 其他错误，仍然可以尝试封禁，但使用默认信息
                target_user_name = f"用户 {target_user_id}"
                target_user_mention = f"<@{target_user_id}>"
                target_user_avatar = None
                if self.logger:
                    self.logger.warning(f"无法获取用户信息 {target_user_id}: {e}")

        confirm_embed = self._build_punishment_confirm_embed(
            title="⛔ 永久封禁确认",
            target=target_user or target_user_name,
            target_id=target_user_id,
            target_mention=target_user_mention,
            target_avatar_url=target_user_avatar,
            punishment_lines=[
                "永久封禁：是",
                f"删除消息天数：{delete_message_days}天",
            ],
            reason=reason,
            moderator=interaction.user,
            colour=discord.Colour.red(),
            attachment=img,
            timeout=60,
        )
        confirmed = await confirm_view_embed(interaction, embed=confirm_embed, timeout=60)
        if not confirmed:
            return

        # 私聊通知（仅当能获取到用户对象时）
        if target_user is not None:
            try:
                ban_embed = discord.Embed(title="⛔ 永久封禁", description=f"您因 **{reason}** 被 **{guild.name}** 永久封禁。如有异议，请联系管理组成员。")
                ban_embed.set_footer(text=f"来自服务器: {guild.name}")
                await dm.send_dm(target_user.guild, target_user, embed=ban_embed)
            except discord.Forbidden:
                pass
            except Exception:
                pass
        
        # 执行封禁
        try:
            if is_member:
                await guild.ban(member, reason=reason, delete_message_days=delete_message_days)
            else:
                # 使用用户ID进行封禁
                await guild.ban(discord.Object(id=target_user_id), reason=reason, delete_message_days=delete_message_days)
        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限封禁该用户", ephemeral=True)
            return
        except discord.NotFound:
            await interaction.followup.send("❌ 用户不存在或已被封禁", ephemeral=True)
            return

        # 保存记录 & 公示
        record_id = self._save_punish_record(guild.id, {
            "type": "ban",
            "user_id": target_user_id,
            "moderator_id": interaction.user.id,
            "reason": reason,
        })

        await interaction.followup.send(f"✅ 已永久封禁 {target_user_name}。处罚ID: `{record_id}`", ephemeral=True)

        # 同步处罚到其他服务器
        sync_cog = self.bot.get_cog("ServerSyncCommands")
        if sync_cog:
            await sync_cog.sync_punishment(
                guild=guild,
                punishment_type="ban",
                member=target_user if is_member else None,
                moderator=interaction.user,
                reason=reason,
                punishment_id=record_id,
                img=img,
                user_id=target_user_id if not is_member else None
            )

        # 当前频道公示
        await interaction.followup.send(embed=discord.Embed(title="⛔ 永久封禁", description=f"{target_user_mention} 因 {reason} 被永久封禁。请注意遵守社区规则。"), ephemeral=False)

        # 公示频道（使用服务器特定配置）
        channel_id = self.get_guild_config("punish_announce_channel_id", guild.id, 0)
        announce_channel = guild.get_channel(int(channel_id)) if channel_id else None
        moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", guild.id, 0)
        moderation_log_channel = guild.get_channel_or_thread(int(moderation_log_channel_id)) if moderation_log_channel_id else None
        if announce_channel or moderation_log_channel:
            img_bytes = None
            img_filename = None
            if img:
                try:
                    img_bytes = await img.read()
                    img_filename = img.filename
                except Exception:
                    pass
            embed = discord.Embed(title="⛔ 永久封禁", color=discord.Color.red())
            embed.add_field(name="成员", value=f"{target_user_name} ({target_user_id})")
            embed.add_field(name="管理员", value=interaction.user.mention)
            if target_user_avatar:
                embed.set_thumbnail(url=target_user_avatar)
            embed.add_field(name="原因", value=reason or "未提供", inline=False)
            if img_bytes:
                embed.set_image(url=f"attachment://{img_filename}")
            embed.set_footer(text=f"处罚ID: {record_id}")
            if announce_channel:
                await announce_channel.send(
                    embed=embed,
                    file=discord.File(io.BytesIO(img_bytes), filename=img_filename) if img_bytes else None,
                )
            if moderation_log_channel:
                await moderation_log_channel.send(
                    embed=embed,
                    file=discord.File(io.BytesIO(img_bytes), filename=img_filename) if img_bytes else None,
                )

    # ---- 批量永封 ----
    @admin.command(name="批量永封", description="批量永久封禁多个用户（使用逗号分隔的用户ID）")
    @app_commands.describe(
        user_ids="用户ID列表（用逗号分隔，例如：123456789,987654321,111222333）",
        reason="原因（可选）",
        delete_message_days="删除消息天数（0-7）"
    )
    @app_commands.rename(user_ids="用户id列表", reason="原因", delete_message_days="删除消息天数")
    @is_senior_admin()
    @guild_only()
    async def bulk_ban_members(
        self,
        interaction,  # type: discord.Interaction
        user_ids: str,
        reason: str = None,
        delete_message_days: int = 0,
    ):
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)

        # 解析用户ID列表
        raw_ids = [id_str.strip() for id_str in user_ids.split(",") if id_str.strip()]
        
        if not raw_ids:
            await interaction.followup.send("❌ 请提供至少一个有效的用户ID", ephemeral=True)
            return

        # 验证并转换ID
        valid_ids: List[int] = []
        invalid_ids: List[str] = []
        
        for raw_id in raw_ids:
            try:
                user_id = int(raw_id)
                valid_ids.append(user_id)
            except ValueError:
                invalid_ids.append(raw_id)

        if not valid_ids:
            await interaction.followup.send(f"❌ 没有有效的用户ID。无效的ID: {', '.join(invalid_ids)}", ephemeral=True)
            return

        # 确认操作
        confirmed = await confirm_view(
            interaction,
            title="批量永封确认",
            description=f"确定要永久封禁以下 {len(valid_ids)} 个用户吗？\n\n用户ID: {', '.join(str(uid) for uid in valid_ids[:20])}" + (f"\n...还有 {len(valid_ids) - 20} 个" if len(valid_ids) > 20 else "") + f"\n\n原因: {reason or '未提供'}",
            colour=discord.Colour.red(),
            timeout=60,
        )

        if not confirmed:
            return

        # 执行批量封禁
        success_list: List[Tuple[int, str]] = []  # (user_id, user_name)
        failed_list: List[Tuple[int, str]] = []   # (user_id, error_reason)

        await interaction.edit_original_response(content=f"正在执行批量封禁... (0/{len(valid_ids)})")

        for idx, user_id in enumerate(valid_ids):
            try:
                # 尝试获取用户信息
                user_name = f"用户 {user_id}"
                try:
                    user = await self.bot.fetch_user(user_id)
                    user_name = str(user)
                except Exception:
                    pass

                # 执行封禁
                await guild.ban(discord.Object(id=user_id), reason=reason, delete_message_days=delete_message_days)
                success_list.append((user_id, user_name))

                # 保存记录
                self._save_punish_record(guild.id, {
                    "type": "ban",
                    "user_id": user_id,
                    "moderator_id": interaction.user.id,
                    "reason": f"[批量永封] {reason}" if reason else "[批量永封]",
                })

            except discord.Forbidden:
                failed_list.append((user_id, "无权限"))
            except discord.NotFound:
                failed_list.append((user_id, "用户不存在或已被封禁"))
            except Exception as e:
                failed_list.append((user_id, str(e)))

            # 每10个更新一次进度
            if (idx + 1) % 10 == 0:
                await interaction.edit_original_response(content=f"正在执行批量封禁... ({idx + 1}/{len(valid_ids)})")

        # 构建结果消息
        result_parts = []
        
        if success_list:
            success_text = f"✅ 成功封禁 {len(success_list)} 个用户"
            if len(success_list) <= 20:
                success_text += ":\n" + "\n".join([f"• {name} ({uid})" for uid, name in success_list])
            result_parts.append(success_text)

        if failed_list:
            failed_text = f"❌ 失败 {len(failed_list)} 个用户"
            if len(failed_list) <= 20:
                failed_text += ":\n" + "\n".join([f"• {uid}: {err}" for uid, err in failed_list])
            result_parts.append(failed_text)

        if invalid_ids:
            result_parts.append(f"⚠️ 无效的ID格式: {', '.join(invalid_ids[:10])}" + (f" 等{len(invalid_ids)}个" if len(invalid_ids) > 10 else ""))

        result_message = "\n\n".join(result_parts)
        await interaction.edit_original_response(content=result_message)

        # 公示和日志（使用服务器特定配置）
        if success_list:
            channel_id = self.get_guild_config("punish_announce_channel_id", guild.id, 0)
            announce_channel = guild.get_channel(int(channel_id)) if channel_id else None
            moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", guild.id, 0)
            moderation_log_channel = guild.get_channel_or_thread(int(moderation_log_channel_id)) if moderation_log_channel_id else None
            
            if announce_channel or moderation_log_channel:
                embed = discord.Embed(title="⛔ 批量永久封禁", color=discord.Color.red())
                embed.add_field(name="封禁数量", value=f"{len(success_list)} 人")
                embed.add_field(name="管理员", value=interaction.user.mention)
                embed.add_field(name="原因", value=reason or "未提供", inline=False)
                
                # 显示部分封禁用户
                user_list_text = "\n".join([f"• <@{uid}>" for uid, name in success_list[:15]])
                if len(success_list) > 15:
                    user_list_text += f"\n...还有 {len(success_list) - 15} 个用户"
                embed.add_field(name="封禁用户", value=user_list_text, inline=False)
                
                if announce_channel:
                    await announce_channel.send(embed=embed)
                if moderation_log_channel:
                    await moderation_log_channel.send(embed=embed)

            # 同步处罚到其他服务器
            sync_cog = self.bot.get_cog("ServerSyncCommands")
            if sync_cog:
                for user_id, user_name in success_list:
                    try:
                        await sync_cog.sync_punishment(
                            guild=guild,
                            punishment_type="ban",
                            member=None,
                            moderator=interaction.user,
                            reason=f"[批量永封] {reason}" if reason else "[批量永封]",
                            punishment_id=None,
                            img=None,
                            user_id=user_id
                        )
                    except Exception as e:
                        if self.logger:
                            self.logger.warning(f"同步批量封禁失败 (用户 {user_id}): {e}")

        if self.logger:
            self.logger.info(f"批量永封完成: 成功 {len(success_list)}, 失败 {len(failed_list)}, 操作者: {interaction.user.id}")

    # ---- 永封审查 ----
    @admin.command(name="永封审查", description="启动永封审查流程")
    @app_commands.describe(
        member="要审查的成员",
        reason="原因",
        check_days="审查天数 (5-30天, 默认7天)",
        attachment="附件（可选）",
    )
    @app_commands.rename(member="成员", reason="原因", check_days="审查天数", attachment="附件")
    @is_admin()
    @guild_only()
    async def pending_ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str,
        check_days: app_commands.Range[int, 5, 30] = 7,
        attachment: discord.Attachment = None,
    ):
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)

        # 检查目标是否为管理员
        if is_admin_member(member):
            await interaction.followup.send("❌ 无法对管理员启动永封审查。", ephemeral=True)
            return

        # 从配置加载频道和身份组ID（使用服务器特定配置）
        appeal_channel_id = self.get_guild_config("appeal_channel_id", guild.id, 0)
        pending_ban_role_id = self.get_guild_config("pending_ban_role_id", guild.id, 0)

        if not appeal_channel_id or not pending_ban_role_id:
            await interaction.followup.send("❌ 辩诉频道 或 永封审查身份组 未配置。", ephemeral=True)
            return

        appeal_channel = guild.get_channel(int(appeal_channel_id))
        pending_ban_role = guild.get_role(int(pending_ban_role_id))

        if not appeal_channel or not pending_ban_role:
            await interaction.followup.send("❌ 无法在服务器中找到配置的申诉频道或审查身份组。", ephemeral=True)
            return

        # 保存用户当前身份组
        original_roles = [role.id for role in member.roles if not role.is_default() and not role.managed]
        expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=check_days)

        appeal_thread = None
        record_id = None
        record_path = None

        try:

            # 1. 创建记录文件
            record_id = self._save_pending_ban_record(guild.id, {
                "user_id": member.id,
                "moderator_id": interaction.user.id,
                "reason": reason,
                "original_roles": original_roles,
                "expires_at": expires_at.isoformat(),
                "appeal_thread_id": None,
            })
            
            # 获取记录文件路径，用于可能的回滚
            record, record_path = self._get_pending_ban_record(guild.id, record_id)

            # 2. 创建申诉帖
            thread_name = f"永封审查 - {member.display_name}"
            thread_message = (
                f"成员: {member.mention} ({member.id})\n"
                f"发起人: {interaction.user.mention}\n\n"
                f"到期时间: <t:{int(expires_at.timestamp())}:F>\n\n"
                f"原因: \n{reason}\n\n"
                f"请在此帖内陈述您的申诉。\n\n"
                f"-# 审查ID: `{record_id}`"
            )

            if attachment:
                thread_file = await attachment.to_file()
                thread_message += f"\n\n**附件**\n\n"
                thread_with_message = await appeal_channel.create_thread(
                    name=thread_name,
                    content=thread_message,
                    file=thread_file
                )
            else:
                thread_with_message = await appeal_channel.create_thread(
                    name=thread_name,
                    content=thread_message
                )
            appeal_thread = thread_with_message.thread

            # 更新记录文件，加入帖子ID
            record["appeal_thread_id"] = appeal_thread.id
            with open(record_path, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)

            # 3. 移除所有非托管身份组并添加审查身份组
            roles_to_set = [pending_ban_role]
            roles_to_set.extend([role for role in member.roles if role.managed])
            await member.edit(roles=roles_to_set, reason=f"{interaction.user} 发起了永封审查")

        except Exception as e:
            # --- 回滚机制 ---
            if self.logger:
                self.logger.error(f"启动永封审查失败: {e}，开始回滚...")
            
            # 尝试删除记录文件
            if record_path and record_path.exists():
                record_path.unlink(missing_ok=True)
                if self.logger: self.logger.info(f"回滚：已删除审查记录 {record_id}")
            
            # 尝试删除申诉帖
            if appeal_thread:
                try:
                    await appeal_thread.delete()
                    if self.logger: self.logger.info(f"回滚：已删除申诉帖 {appeal_thread.id}")
                except Exception as thread_del_e:
                    if self.logger: self.logger.error(f"回滚失败：无法删除申诉帖 {appeal_thread.id}: {thread_del_e}")
            
            await interaction.followup.send(f"❌ 操作失败，已自动回滚。错误: {e}", ephemeral=True)
            return

        # 4. 预读附件字节（避免 embed 内嵌 URL 过期）
        att_bytes = None
        att_filename = None
        att_is_image = False
        if attachment:
            try:
                att_bytes = await attachment.read()
                att_filename = attachment.filename
                att_is_image = bool(attachment.content_type and attachment.content_type.startswith("image/"))
            except Exception:
                pass

        # 5. 私信通知
        dm_failed = False
        try:
            embed = discord.Embed(title="⚠️ 永封审查通知", color=discord.Color.dark_red())
            embed.description = (
                f"您在 **{guild.name}** 因 **{reason or '未提供原因'}** 被置于为期 {check_days} 天的永封审查流程中。\n\n"
                f"请在专属申诉帖 {appeal_thread.mention} 中发言以进行申诉。\n"
                f"如果 {check_days} 天后此审查未被撤销，系统将自动对您执行永久封禁。"
            )
            embed.add_field(name="审查到期时间", value=f"<t:{int(expires_at.timestamp())}:F>", inline=False)
            embed.set_footer(text=f"审查ID: {record_id} | 来自服务器: {guild.name}")
            dm_file = None
            if att_bytes and att_is_image:
                embed.add_field(name="附件", value="", inline=False)
                embed.set_image(url=f"attachment://{att_filename}")
                dm_file = discord.File(io.BytesIO(att_bytes), filename=att_filename)
            elif att_bytes and att_filename:
                embed.add_field(name="附件", value=att_filename, inline=False)
                dm_file = discord.File(io.BytesIO(att_bytes), filename=att_filename)
            await member.send(embed=embed, file=dm_file)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"发送撤销审查私信失败: {e}")
            dm_failed = True

        # 6. 公示（使用服务器特定配置）
        announce_channel_id = self.get_guild_config("punish_announce_channel_id", guild.id, 0)
        announce_channel = guild.get_channel(int(announce_channel_id)) if announce_channel_id else None
        moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", guild.id, 0)
        moderation_log_channel = guild.get_channel_or_thread(int(moderation_log_channel_id)) if moderation_log_channel_id else None
        if announce_channel or moderation_log_channel:
            embed = discord.Embed(title="⚖️ 永封审查启动", color=discord.Color.dark_orange())
            embed.add_field(name="成员", value=f"{member.mention} ({member.id})")
            embed.add_field(name="发起人", value=interaction.user.mention)
            embed.add_field(name="审查期限", value=f"{check_days}天", inline=False)
            embed.add_field(name="到期时间", value=f"<t:{int(expires_at.timestamp())}:F>", inline=False)
            embed.add_field(name="原因", value=reason or "未提供", inline=False)
            embed.add_field(name="申诉帖", value=f"{appeal_thread.mention}", inline=False)
            if att_bytes and att_is_image:
                embed.add_field(name="附件", value="", inline=False)
                embed.set_image(url=f"attachment://{att_filename}")
            elif att_bytes and att_filename:
                embed.add_field(name="附件", value=att_filename, inline=False)
            embed.set_footer(text=f"审查ID: {record_id}")
            if announce_channel:
                await announce_channel.send(
                    embed=embed,
                    file=discord.File(io.BytesIO(att_bytes), filename=att_filename) if att_bytes else None,
                )
            if moderation_log_channel:
                await moderation_log_channel.send(
                    embed=embed,
                    file=discord.File(io.BytesIO(att_bytes), filename=att_filename) if att_bytes else None,
                )

        # 6. 发送给管理员的消息
        success_message = f"✅ 已启动对 {member.mention} 的永封审查。审查ID: `{record_id}`"
        if dm_failed:
            success_message += "\n(⚠️ 发送私信失败，用户可能已关闭私信)"
        await interaction.followup.send(success_message, ephemeral=True)

    # ---- 撤销处罚 ----
    @admin.command(name="撤销处罚", description="按ID撤销处罚")
    @app_commands.describe(punish_id="处罚ID", reason="原因（可选）")
    @is_senior_admin()
    @guild_only()
    async def revoke_punish(self, interaction, punish_id: str, reason: str = None):
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)
        
        try:
            record, path = self._get_punish_record(guild.id, punish_id)
            if record is None:
                await interaction.followup.send("❌ 未找到对应处罚记录", ephemeral=True)
                return

            user_id = int(record["user_id"])
            user_obj = None
            user_mention = f"<@{user_id}>"  # 默认mention，防止获取用户失败
            punishment_type_label = "处罚"
            
            if record["type"] == "mute":
                punishment_type_label = "禁言/警告"
                # 对于禁言，需要获取用户对象
                try:
                    user_obj = guild.get_member(user_id) or await guild.fetch_member(user_id)
                    user_mention = user_obj.mention
                except discord.NotFound:
                    await interaction.followup.send("❌ 未找到对应用户", ephemeral=True)
                    return
                
                try:
                    await user_obj.timeout(None, reason="撤销处罚")
                    if record.get("warn", 0) > 0:
                        # 使用服务器特定配置
                        warned_role_id = self.get_guild_config("warned_role_id", guild.id, 0)
                        warned_role = guild.get_role(int(warned_role_id)) if warned_role_id else None
                        if warned_role:
                            sync_cog_ref = self.bot.get_cog("ServerSyncCommands")
                            if sync_cog_ref:
                                sync_cog_ref._mark_guard(guild.id, user_obj.id, warned_role.id, "remove")
                            await user_obj.remove_roles(warned_role, reason=f"撤销处罚附加警告 {record['warn']} 天")
                except discord.Forbidden:
                    await interaction.followup.send("❌ 无权限解除禁言", ephemeral=True)
                    return
                    
            elif record["type"] == "ban":
                punishment_type_label = "永久封禁"
                # 对于封禁，直接使用user_id进行解封
                try:
                    await guild.unban(discord.Object(id=user_id), reason="撤销处罚")
                    # 尝试获取用户信息用于公示（如果失败则使用默认mention）
                    try:
                        user_obj = await self.bot.fetch_user(user_id)
                        user_mention = user_obj.mention
                    except Exception:
                        # 如果获取用户失败，继续使用默认mention
                        pass
                except discord.Forbidden:
                    await interaction.followup.send("❌ 无权限解除封禁", ephemeral=True)
                    return
                except discord.NotFound:
                    await interaction.followup.send("❌ 未找到对应封禁记录", ephemeral=True)
                    return
            else:
                await interaction.followup.send("❌ 未知处罚类型", ephemeral=True)
                return

            # 删除记录文件
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

            await interaction.followup.send(
                f"✅ 已撤销 {user_mention} 的{punishment_type_label}。处罚ID: `{punish_id}`",
                ephemeral=True,
            )

            # 同步撤销处罚到其他服务器
            sync_cog = self.bot.get_cog("ServerSyncCommands")
            if sync_cog:
                try:
                    await sync_cog.sync_revoke_punishment(guild, punish_id, interaction.user, reason)
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"同步撤销处罚失败: {e}")
                    await interaction.followup.send(
                        "⚠️ 本服务器处罚已撤销，但同步撤销到其他服务器时失败，请检查同步日志。",
                        ephemeral=True,
                    )

            # 公示（使用服务器特定配置）
            channel_id = self.get_guild_config("punish_announce_channel_id", guild.id, 0)
            announce_channel = guild.get_channel(int(channel_id)) if channel_id else None
            moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", guild.id, 0)
            moderation_log_channel = guild.get_channel_or_thread(int(moderation_log_channel_id)) if moderation_log_channel_id else None
            if announce_channel or moderation_log_channel:
                embed = discord.Embed(title="🔓 撤销处罚", color=discord.Color.green())
                embed.add_field(name="处罚ID", value=punish_id)
                embed.add_field(name="成员", value=user_mention)
                embed.add_field(name="原因", value=reason or "未提供", inline=False)
                try:
                    if announce_channel:
                        await announce_channel.send(embed=embed)
                    if moderation_log_channel:
                        await moderation_log_channel.send(embed=embed)
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"发送撤销处罚公示失败: {e}")
                
        except Exception as e:
            # 捕获所有未预期的异常，防止交互卡死
            if self.logger:
                self.logger.error(f"撤销处罚时发生错误: {e}")
            await interaction.followup.send("❌ 撤销处罚时发生错误，请检查处罚ID是否正确", ephemeral=True)

    # ---- 撤销永封审查 ----
    @admin.command(name="撤销永封审查", description="按审查ID撤销一个正在进行的永封审查")
    @app_commands.describe(punish_id="审查ID", reason="撤销原因", attachment="附件（可选）")
    @app_commands.rename(punish_id="审查id", reason="撤销原因", attachment="附件")
    @is_admin()
    @guild_only()
    async def revoke_pending_ban(self, interaction: discord.Interaction, punish_id: str, reason: str, attachment: discord.Attachment = None):
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)

        record, path = self._get_pending_ban_record(guild.id, punish_id)
        if record is None:
            await interaction.followup.send("❌ 未找到对应的永封审查记录", ephemeral=True)
            return

        user_id = record["user_id"]
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        if not member:
            await interaction.followup.send("❌ 成员已不在服务器中，无法恢复身份组。记录已清除。", ephemeral=True)
            path.unlink(missing_ok=True)
            return

        # 预读附件字节
        att_bytes = None
        att_filename = None
        att_is_image = False
        if attachment:
            try:
                att_bytes = await attachment.read()
                att_filename = attachment.filename
                att_is_image = bool(attachment.content_type and attachment.content_type.startswith("image/"))
            except Exception:
                pass

        # 私信通知
        dm_failed = False
        try:
            embed = discord.Embed(title="✅ 永封审查已撤销", color=discord.Color.green())
            appeal_thread_id = record.get("appeal_thread_id")
            appeal_thread_mention = f"<#{appeal_thread_id}>" if appeal_thread_id else ""
            embed.description = f"您好，您在 **{guild.name}** 的永封审查已被撤销。\n\n**撤销原因** :\n\n{reason}\n\n申诉帖 : {appeal_thread_mention}"
            embed.set_footer(text=f"审查ID: {punish_id} | 来自服务器: {guild.name}")
            dm_file = None
            if att_bytes and att_is_image:
                embed.add_field(name="附件", value="", inline=False)
                embed.set_image(url=f"attachment://{att_filename}")
                dm_file = discord.File(io.BytesIO(att_bytes), filename=att_filename)
            elif att_bytes and att_filename:
                embed.add_field(name="附件", value=att_filename, inline=False)
                dm_file = discord.File(io.BytesIO(att_bytes), filename=att_filename)
            await member.send(embed=embed, file=dm_file)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"发送撤销审查私信失败: {e}")
            dm_failed = True

        # 恢复身份组
        original_role_ids = record.get("original_roles", [])
        roles_to_restore = [guild.get_role(role_id) for role_id in original_role_ids if guild.get_role(role_id)]
        
        managed_roles = [role for role in member.roles if role.managed]
        for role in managed_roles:
            if role not in roles_to_restore:
                roles_to_restore.append(role)
        
        try:
            await member.edit(roles=roles_to_restore, reason=f"撤销永封审查 by {interaction.user}")
        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限修改该成员的身份组。", ephemeral=True)
            return
        
        # 锁定帖子
        appeal_thread_id = record.get("appeal_thread_id")
        if appeal_thread_id:
            try:
                thread = await self.bot.fetch_channel(appeal_thread_id)
                self.logger.info(f"永封审查已撤销，自动关闭申诉帖 {appeal_thread_id}")
                await thread.edit(locked=True, archived=True, reason="永封审查已撤销，自动关闭申诉帖")
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"无法自动锁定申诉帖 {appeal_thread_id}: {e}")

        # 删除记录文件
        path.unlink(missing_ok=True)

        success_message = f"✅ 已撤销对 {member.mention} 的永封审查。"
        if dm_failed:
            success_message += "\n(⚠️ 发送私信失败，用户可能已关闭私信)"
        await interaction.followup.send(success_message, ephemeral=True)

        # 公示（使用服务器特定配置）
        announce_channel_id = self.get_guild_config("punish_announce_channel_id", guild.id, 0)
        announce_channel = guild.get_channel(int(announce_channel_id)) if announce_channel_id else None
        if announce_channel and isinstance(announce_channel, discord.abc.Messageable):
            embed = discord.Embed(title="✅ 撤销永封审查", color=discord.Color.green())
            embed.add_field(name="成员", value=member.mention)
            embed.add_field(name="撤销人", value=interaction.user.mention)
            embed.add_field(name="原因", value=reason or "未提供", inline=False)
            appeal_thread_id = record.get("appeal_thread_id")
            if appeal_thread_id:
                embed.add_field(name="申诉帖", value=f"<#{appeal_thread_id}>", inline=False)
            embed.set_footer(text=f"审查ID: {punish_id}")
            if att_bytes and att_is_image:
                embed.add_field(name="附件", value="", inline=False)
                embed.set_image(url=f"attachment://{att_filename}")
            elif att_bytes and att_filename:
                embed.add_field(name="附件", value=att_filename, inline=False)
            await announce_channel.send(
                embed=embed,
                file=discord.File(io.BytesIO(att_bytes), filename=att_filename) if att_bytes else None,
            )

    # ---- 频道管理 ----
    @admin.command(name="频道管理", description="编辑频道属性")
    @app_commands.describe(
        channel="要编辑的频道",
        new_name="新名称(可选)",
        slowmode="慢速模式(可选)",
        nsfw="是否NSFW(可选)",
        auto_archive="自动归档 (仅限论坛/子区)"
    )
    @app_commands.choices(
        slowmode=[
            app_commands.Choice(name="关闭", value=0),
            app_commands.Choice(name="5秒", value=5),
            app_commands.Choice(name="10秒", value=10),
            app_commands.Choice(name="15秒", value=15),
            app_commands.Choice(name="30秒", value=30),
            app_commands.Choice(name="1分钟", value=60),
        ],
        auto_archive=[
            app_commands.Choice(name="1小时", value=3600),
            app_commands.Choice(name="1天", value=86400),
            app_commands.Choice(name="1周", value=604800),
            app_commands.Choice(name="1个月", value=2592000),
        ]
    )
    @is_admin()
    async def manage_channel(
        self,
        interaction,  # type: discord.Interaction
        channel: "discord.TextChannel",
        new_name: str = None,
        slowmode: app_commands.Choice[int] = None,
        nsfw: bool = None,
        auto_archive: app_commands.Choice[int] = None,
    ):
        await interaction.response.defer(ephemeral=True)
        params = {}
        if new_name:
            params["name"] = new_name[:100]
        if slowmode is not None:
            params["slowmode_delay"] = max(0, slowmode.value)
        if nsfw is not None:
            params["nsfw"] = nsfw
        if auto_archive is not None and hasattr(channel, "auto_archive_duration"):
            params["auto_archive_duration"] = auto_archive.value

        if not params:
            await interaction.followup.send("❌ 未提供任何修改参数", ephemeral=True)
            return
        try:
            # 记录日志（使用服务器特定配置）
            moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", interaction.guild.id, 0)
            moderation_log_channel = interaction.guild.get_channel_or_thread(int(moderation_log_channel_id)) if moderation_log_channel_id else None
            if moderation_log_channel:
                embed = discord.Embed(
                    title="🔴 频道管理",
                    description=f"管理员 {interaction.user.mention} 在 {channel.mention} 进行了频道管理。",
                    color=discord.Color.blue()
                )
                embed.add_field(name="频道", value=f"{channel.mention}")
                if 'name' in params and params['name'] != channel.name:
                    embed.add_field(name="频道名", value=f"原名: {channel.name}\n新名: {params['name']}")
                if 'slowmode_delay' in params and params['slowmode_delay'] != channel.slowmode_delay:
                    embed.add_field(name="慢速模式", value=f"原慢速模式: {channel.slowmode_delay}\n新慢速模式: {params['slowmode_delay']}")
                if 'nsfw' in params and params['nsfw'] != channel.nsfw:
                    embed.add_field(name="NSFW", value=f"原NSFW: {channel.nsfw}\n新NSFW: {params['nsfw']}")
                if 'auto_archive_duration' in params and params['auto_archive_duration'] != channel.auto_archive_duration:
                    embed.add_field(name="自动归档", value=f"{params['auto_archive_duration']}")

                embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
                
                await moderation_log_channel.send(embed=embed)
                
            await channel.edit(**params, reason=f"频道管理 by {interaction.user}")
            await interaction.followup.send("✅ 频道已更新", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ 无修改权限", ephemeral=True)

    # ---- 移动频道 ----
    @admin.command(name="移动频道", description="移动频道到指定分类或相对位置")
    @app_commands.describe(
        channel="要移动的频道",
        category="目标分类（可选）",
        reference_channel="参考频道（可选）",
        direction="相对于参考频道的位置"
    )
    @app_commands.choices(
        direction=[
            app_commands.Choice(name="移动到上方", value="above"),
            app_commands.Choice(name="移动到下方", value="below"),
        ]
    )
    @app_commands.rename(channel="频道", category="分类", reference_channel="参考频道", direction="位置")
    @is_admin()
    async def move_channel(
        self,
        interaction,  # type: discord.Interaction
        channel: "discord.TextChannel",
        category: "discord.CategoryChannel" = None,
        reference_channel: "discord.TextChannel" = None,
        direction: app_commands.Choice[str] = None,
    ):
        await interaction.response.defer(ephemeral=True)
        
        if category is None and reference_channel is None:
            await interaction.followup.send("❌ 请至少指定分类或参考频道参数", ephemeral=True)
            return
        
        # 如果指定了参考频道但没有指定方向，默认移动到下方
        if reference_channel is not None and direction is None:
            direction_value = "below"
        elif direction is not None:
            direction_value = direction.value
        else:
            direction_value = None
        
        # 检查是否试图移动到自己
        if reference_channel and reference_channel.id == channel.id:
            await interaction.followup.send("❌ 不能以自己作为参考频道", ephemeral=True)
            return
        
        # 记录移动前的状态
        old_category = channel.category
        old_position = channel.position
        
        try:
            # 准备编辑参数
            edit_kwargs = {}
            
            # 设置分类
            if category is not None:
                edit_kwargs["category"] = category
            
            # 设置位置
            if reference_channel is not None:
                target_position = reference_channel.position
                if direction_value == "above":
                    # 移动到参考频道上方
                    edit_kwargs["position"] = max(0, target_position)
                else:  # below
                    # 移动到参考频道下方
                    edit_kwargs["position"] = target_position + 1
            
            # 执行移动
            await channel.edit(**edit_kwargs, reason=f"频道移动 by {interaction.user}")
            
            # 构造移动结果描述
            move_description = []
            if category is not None:
                old_cat_name = old_category.name if old_category else "无分类"
                move_description.append(f"分类: {old_cat_name} → {category.name}")
            if reference_channel is not None:
                direction_text = "上方" if direction_value == "above" else "下方"
                move_description.append(f"位置: 移动到 {reference_channel.mention} 的{direction_text}")
                move_description.append(f"位置变化: {old_position} → {channel.position}")
            
            move_info = "\n".join(move_description)
            
            # 记录日志
            moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", interaction.guild.id, 0)
            moderation_log_channel = interaction.guild.get_channel_or_thread(int(moderation_log_channel_id)) if moderation_log_channel_id else None
            if moderation_log_channel:
                embed = discord.Embed(
                    title="🔄 频道移动",
                    description=f"管理员 {interaction.user.mention} 移动了频道 {channel.mention}",
                    color=discord.Color.blue()
                )
                embed.add_field(name="移动详情", value=move_info, inline=False)
                embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
                await moderation_log_channel.send(embed=embed)
            
            await interaction.followup.send(
                f"✅ 已成功移动频道 {channel.mention}\n{move_info}", 
                ephemeral=True
            )
            
        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限移动该频道", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ 移动频道失败: {str(e)}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 移动频道时发生错误: {str(e)}", ephemeral=True)
            if self.logger:
                self.logger.error(f"移动频道错误: {e}")

    # ---- 一键删帖 ----
    @admin.command(name="一键删帖", description="一键删除某成员发布的全部帖子")
    @app_commands.describe(member="要删除帖子的成员ID", channel="要删除帖子的频道")
    @app_commands.rename(member="成员id", channel="频道")
    @is_senior_admin()
    async def delete_all_threads(self, interaction: discord.Interaction, member: str, channel: "discord.ForumChannel"):
        await interaction.response.defer(ephemeral=True)
        
        # 验证成员ID格式
        try:
            member_id = int(member)
        except ValueError:
            await interaction.followup.send("❌ 请提供有效的成员ID（纯数字）", ephemeral=True)
            return
        
        # confirm view
        confirmed = await confirm_view(
            interaction,
            title="确认删除",
            description=f"确定要删除用户ID {member_id} 发布的全部帖子吗？",
            colour=discord.Color.red(),
            timeout=60
        )

        if not confirmed:
            return
            
        deleted = []
        
        # 获取频道内当前活跃的线程
        for thread in channel.threads:
            if thread.owner_id == member_id:
                try:
                    deleted.append(thread.name)
                    await thread.delete()
                    if self.logger:
                        self.logger.info(f"删除活跃线程: {thread.name} (ID: {thread.id}) by {member_id}")
                except discord.Forbidden:
                    if self.logger:
                        self.logger.warning(f"没有删除线程权限: {thread.name}")
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"删除线程失败: {thread.name}, 错误: {e}")
        
        # 获取归档的线程
        before = None
        page_count = 0
        max_pages = 50  # 防止无限循环，最多检查50页
        
        while page_count < max_pages:
            try:
                archived_threads = []
                async for thread in channel.archived_threads(limit=100, before=before):
                    archived_threads.append(thread)
                
                if len(archived_threads) == 0:
                    break
                    
                # 处理这一页的归档线程
                for thread in archived_threads:
                    if thread.owner_id == member_id:
                        try:
                            deleted.append(thread.name)
                            await thread.delete()
                            if self.logger:
                                self.logger.info(f"删除归档线程: {thread.name} (ID: {thread.id}) by {member_id}")
                        except discord.Forbidden:
                            if self.logger:
                                self.logger.warning(f"没有删除归档线程权限: {thread.name}")
                        except Exception as e:
                            if self.logger:
                                self.logger.error(f"删除归档线程失败: {thread.name}, 错误: {e}")
                
                # 更新before为最后一个线程的归档时间
                if archived_threads:
                    before = archived_threads[-1].archive_timestamp
                    page_count += 1
                    
                    # 每处理10页更新一次进度
                    if page_count % 10 == 0:
                        await interaction.edit_original_response(content=f"正在扫描归档线程...已处理 {page_count} 页，找到 {len(deleted)} 个帖子")
                else:
                    break
                    
            except Exception as e:
                if self.logger:
                    self.logger.error(f"获取归档线程失败: {e}")
                break
        
        # 构建结果显示
        if deleted:
            # 限制显示的帖子名称数量，避免消息过长
            display_names = deleted[:20]  # 只显示前20个
            description_parts = [f"已删除以下帖子："]
            description_parts.extend([f"• {name}" for name in display_names])
            
            if len(deleted) > 20:
                description_parts.append(f"...还有 {len(deleted) - 20} 个帖子")
            
            description_parts.append(f"\n**总计删除: {len(deleted)} 个帖子**")
            description = "\n".join(description_parts)
        else:
            description = f"未找到用户ID {member_id} 发布的帖子"
            
        embed = discord.Embed(
            title="删除结果",
            description=description,
            colour=discord.Color.green() if deleted else discord.Color.orange()
        )
        
        await interaction.followup.send(embed=embed, ephemeral=True)

        # 记录日志（使用服务器特定配置）
        moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", interaction.guild.id, 0)
        moderation_log_channel = interaction.guild.get_channel_or_thread(int(moderation_log_channel_id)) if moderation_log_channel_id else None
        if moderation_log_channel:
            embed = discord.Embed(
                title="一键删帖",
                description=description,
                colour=discord.Color.green() if deleted else discord.Color.orange()
            )
            embed.add_field(name="删除者", value=f"{interaction.user.mention}")
            embed.add_field(name="删除对象", value=f"{member_id}")
            embed.add_field(name="删除数量", value=f"{len(deleted)}")
            embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
            await moderation_log_channel.send(embed=embed)
        
        if self.logger:
            self.logger.info(f"一键删帖完成: 用户{member_id}，共删除{len(deleted)}个帖子，操作者: {interaction.user.id}")

    # ---- 子区管理 ----
    thread_manage_group = app_commands.Group(name="子区管理", description="子区线程管理", parent=admin)
    @thread_manage_group.command(name="解锁", description="解锁线程")
    @app_commands.describe(thread="要解锁的子区（留空则为当前子区）")
    @app_commands.rename(thread="子区")
    @is_admin()
    async def unlock_thread_admin(
        self,
        interaction,
        thread: "discord.Thread" = None
    ):
        await interaction.response.defer(ephemeral=True)
        if thread is None:
            thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.followup.send("❌ 请指定一个子区", ephemeral=True)
            return
        if not thread.locked:
            await interaction.followup.send("未锁定", ephemeral=True)
            return
        try:
            await thread.edit(locked=False, archived=False, reason=f"解锁 by {interaction.user}")

            # 记录日志（使用服务器特定配置）
            moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", interaction.guild.id, 0)
            moderation_log_channel = interaction.guild.get_channel_or_thread(int(moderation_log_channel_id)) if moderation_log_channel_id else None
            if moderation_log_channel:
                await moderation_log_channel.send(embed=discord.Embed(title="🔴 子区管理", description=f"管理员 {interaction.user.mention} 在 {thread.mention} 解锁了子区。"))

            await interaction.followup.send("✅ 已解锁线程", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 解锁失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="archive", description="归档线程")
    @app_commands.describe(thread="要归档的子区（留空则为当前子区）")
    @app_commands.rename(thread="子区")
    @is_admin()
    async def archive_thread_admin(
        self,
        interaction,
        thread: "discord.Thread" = None
    ):
        await interaction.response.defer(ephemeral=True)
        if thread is None:
            thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.followup.send("❌ 请指定一个子区", ephemeral=True)
            return
        if thread.archived:
            await interaction.followup.send("已归档", ephemeral=True)
            return
        try:
            await thread.edit(archived=True, reason=f"归档 by {interaction.user}")

            # 记录日志（使用服务器特定配置）
            moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", interaction.guild.id, 0)
            moderation_log_channel = interaction.guild.get_channel_or_thread(int(moderation_log_channel_id)) if moderation_log_channel_id else None
            if moderation_log_channel:
                await moderation_log_channel.send(embed=discord.Embed(title="🔴 子区管理", description=f"管理员 {interaction.user.mention} 在 {thread.mention} 归档了子区。"))

            await interaction.followup.send("✅ 已归档线程", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 归档失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="置顶", description="将论坛帖子设为置顶帖")
    @app_commands.describe(thread="要置顶的论坛帖子（留空则为当前帖子）")
    @app_commands.rename(thread="子区")
    @is_admin()
    async def pin_in_thread_admin(
        self,
        interaction,
        thread: "discord.Thread" = None
    ):
        await interaction.response.defer(ephemeral=True)
        if thread is None:
            thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.followup.send("❌ 请指定一个子区", ephemeral=True)
            return
        if not isinstance(thread.parent, discord.ForumChannel):
            await interaction.followup.send("❌ 仅论坛频道中的帖子支持设为置顶帖", ephemeral=True)
            return
        if getattr(thread.flags, "pinned", False):
            await interaction.followup.send("该帖子已经是置顶帖", ephemeral=True)
            return
        try:
            await thread.edit(pinned=True, reason=f"管理员置顶 by {interaction.user}")

            # 记录日志（使用服务器特定配置）
            moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", interaction.guild.id, 0)
            moderation_log_channel = interaction.guild.get_channel_or_thread(int(moderation_log_channel_id)) if moderation_log_channel_id else None
            if moderation_log_channel:
                await moderation_log_channel.send(embed=discord.Embed(title="🔴 子区管理", description=f"管理员 {interaction.user.mention} 将 {thread.mention} 设为了置顶帖。"))

            await interaction.followup.send("✅ 已将帖子设为置顶帖", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 置顶帖子失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="取消置顶", description="取消论坛帖子的置顶状态")
    @app_commands.describe(thread="要取消置顶的论坛帖子（留空则为当前帖子）")
    @app_commands.rename(thread="子区")
    @is_admin()
    async def unpin_in_thread_admin(
        self,
        interaction,
        thread: "discord.Thread" = None
    ):
        await interaction.response.defer(ephemeral=True)
        if thread is None:
            thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.followup.send("❌ 请指定一个子区", ephemeral=True)
            return
        if not isinstance(thread.parent, discord.ForumChannel):
            await interaction.followup.send("❌ 仅论坛频道中的帖子支持取消置顶", ephemeral=True)
            return
        if not getattr(thread.flags, "pinned", False):
            await interaction.followup.send("该帖子当前不是置顶帖", ephemeral=True)
            return
        try:
            await thread.edit(pinned=False, reason=f"管理员取消置顶 by {interaction.user}")

            # 记录日志（使用服务器特定配置）
            moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", interaction.guild.id, 0)
            moderation_log_channel = interaction.guild.get_channel_or_thread(int(moderation_log_channel_id)) if moderation_log_channel_id else None
            if moderation_log_channel:
                await moderation_log_channel.send(embed=discord.Embed(title="🔴 子区管理", description=f"管理员 {interaction.user.mention} 取消了 {thread.mention} 的置顶状态。"))

            await interaction.followup.send("✅ 已取消帖子置顶", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 取消帖子置顶失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="删帖", description="删除线程")
    @app_commands.describe(thread="要删除的子区（留空则为当前子区）")
    @app_commands.rename(thread="子区")
    @is_admin()
    async def delete_thread_admin(
        self,
        interaction,
        thread: "discord.Thread" = None
    ):
        await interaction.response.defer(ephemeral=True)
        if thread is None:
            thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.followup.send("❌ 请指定一个子区", ephemeral=True)
            return
        
        # 创建删除审批视图
        approval_view = ThreadDeleteApprovalView(cog=self, thread=thread, initiator=interaction.user)

        embed = discord.Embed(
            title="🗑️ 删除子区请求",
            description=(
                f"{interaction.user.mention} 请求删除子区 **{thread.name}**\n\n"
                "需要 **5** 位管理员点击同意才会执行删除；任意管理员点击拒绝即可一票否决。"
            ),
            colour=discord.Color.red(),
        )

        # 在当前频道发送持久视图
        message = await interaction.channel.send(embed=embed, view=approval_view)
        approval_view.message = message

        await interaction.followup.send("✅ 已发起删除请求，等待其他管理员确认", ephemeral=True)
         
        # 如果需要日志
        if self.logger:
            self.logger.info(
                f"线程删除请求已发起: {thread.name} (ID: {thread.id}) by {interaction.user.display_name}({interaction.user.id})"
            )

    # ---- 答题处罚工具函数 ----
    async def _save_quiz_punish(self, member: discord.Member, reason: str, punisher_id: int):
        """保存处罚记录到data/punish/quiz/id.json"""
        punish_record = await self._get_quiz_punish(member)
        if punish_record is None:
            punish_record = {
                "id": member.id,
                "punish_count": 0,
                "punish_list": []
            }
        punish_record["punish_count"] += 1
        punish_record["punish_list"].append({
            # 北京时间
            "punish_time": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
            "reason": reason,
            "punisher_id": punisher_id,
        })
        with open(f"data/punish/quiz/{member.id}.json", "w") as f:
            json.dump(punish_record, f)
        return punish_record

    async def _get_quiz_punish(self, member: discord.Member):
        """从data/punish/quiz/id.json获取处罚记录"""
        file_path = f"data/punish/quiz/{member.id}.json"
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                return json.load(f)
        return None

    async def _quiz_punish_init(self):
        """初始化答题处罚记录"""
        # 检测data/punish/quiz目录是否存在
        if os.path.exists("data/punish/quiz"):
           return
        # 不存在，开始回溯记录所有手动记录的处罚
        def _get_record(id: int):
            """从data/punish/quiz/id.json获取处罚记录"""
            file_path = f"data/punish/quiz/{id}.json"
            if os.path.exists(file_path):
                with open(file_path, "r") as f:
                    return json.load(f)
            return None
        def _save_record(id: int, record: dict):
            """保存处罚记录到data/punish/quiz/id.json"""
            with open(f"data/punish/quiz/{id}.json", "w") as f:
                json.dump(record, f)

        def extract_reason(message: str):
            """从消息中提取处罚原因"""
            # 格式1：因 {reason} 被
            # 格式2：理由：{reason}
            # 尝试从格式1和格式2中提取原因
            if not message:
                return ""
            reason = ""
            if "因" in message:
                reason = message.split("因")[1].split("被")[0].strip()
            elif "理由：" in message:
                reason = message.split("理由：")[1].strip()
            return reason

        os.makedirs("data/punish/quiz", exist_ok=True)
        # 注意：初始化任务没有特定的guild_id，使用全局配置
        record_channel = self.bot.get_channel(int(self.config.get("quiz_punish_log_channel_id", 0)))
        if record_channel:
            # 创建实时更新的embed进度
            embed = discord.Embed(title="答题处罚记录初始化", description="正在回溯记录所有手动记录的处罚...")
            embed.add_field(name="已回溯消息", value="0")
            embed.add_field(name="已记录处罚", value="0")
            record_message = await record_channel.send(embed=embed)
            last_message = None
            last_fetched = None
            fetched_count = 0
            record_count = 0
            # 从第一条消息开始遍历
            while True:
                try:
                    
                    fetched: List[discord.Message] = [
                        m async for m in record_channel.history(limit=100, after=last_message, oldest_first=True)
                    ]
                    if not fetched:
                        break
                    fetched_count += len(fetched)
                    for i, message in enumerate(fetched):
                        # 判断是否为纯数字消息
                        if message.content.isdigit():
                            id = int(message.content)
                            reason = ""
                            # 回溯前4条消息寻找embed，若前方消息不足4条则向last_fetched回溯
                            for j in range(4):
                                if i - j - 1 < 0:
                                    if last_fetched:
                                        reason_message = last_fetched[i - j - 1]
                                        reference = reason_message.reference
                                        if reference:
                                            try:
                                                reason_message_channel = await self.bot.get_channel(reference.channel_id)
                                                reason_message = await reason_message_channel.fetch_message(reference.message_id)
                                            except Exception as e:
                                                continue
                                        if reason_message.embeds:
                                            reason = extract_reason(reason_message.embeds[0].description)
                                            break
                                    else:
                                        continue
                                else:
                                    reason_message = fetched[i - j - 1]
                                    reference = reason_message.reference
                                    if reference:
                                        try:
                                            reason_message_channel = await self.bot.fetch_channel(reference.channel_id)
                                            reason_message = await reason_message_channel.fetch_message(reference.message_id)
                                        except Exception as e:
                                            continue
                                    if reason_message.embeds:
                                        reason = extract_reason(reason_message.embeds[0].description)
                                        break
                            reason = reason if reason else "未记录"
                            record = _get_record(id)
                            if record is None:
                                record = {
                                    "id": id,
                                    "punish_count": 0,
                                    "punish_list": []
                                }
                            record["punish_count"] += 1
                            record["punish_list"].append({
                                "punish_time": message.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                                "reason": reason,
                                "punisher_id": message.author.id,
                            })
                            _save_record(id, record)
                            record_count += 1
                    embed = discord.Embed(title="答题处罚记录初始化", description="正在回溯记录所有手动记录的处罚...")
                    embed.add_field(name="已回溯消息", value=f"{fetched_count}")
                    embed.add_field(name="已记录处罚", value=f"{record_count}")
                    await record_message.edit(embed=embed)
                    last_message = fetched[-1]
                    last_fetched = fetched
                
                except Exception as e:
                    embed = discord.Embed(title="答题处罚记录初始化", description="初始化失败")
                    embed.add_field(name="错误信息", value=f"{e}")
                    await record_channel.send(embed=embed)
                    break
            embed = discord.Embed(title="答题处罚记录初始化", description="初始化完成")
            embed.add_field(name="已回溯消息", value=f"{fetched_count}")
            embed.add_field(name="已记录处罚", value=f"{record_count}")
            await record_message.edit(embed=embed)

    # ---- 答题处罚 ----
    @app_commands.command(name="答题处罚", description="移除身份组送往答题区")
    @app_commands.describe(member="要处罚的成员", reason="原因（可选）")
    @app_commands.rename(member="成员", reason="原因")
    async def quiz_punish(self, interaction: discord.Interaction, member: "discord.Member", reason: str = None):
            
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        
        # 使用服务器特定配置
        role_id = self.get_guild_config("verified_role_id", guild.id, 0)
        buffer_role_id = self.get_guild_config("buffer_role_id", guild.id, 0)
        upper_buffer_role_id = self.get_guild_config("upper_buffer_role_id", guild.id, 0)
        whitelist = self.get_guild_config("quiz_punish_whitelist", guild.id, [])
        quiz_punish_log_channel_id = self.get_guild_config("quiz_punish_log_channel_id", guild.id, 0)
        
        role = guild.get_role(int(role_id)) if role_id else None
        buffer_role = guild.get_role(int(buffer_role_id)) if buffer_role_id else None
        upper_buffer_role = guild.get_role(int(upper_buffer_role_id)) if upper_buffer_role_id else None
        if role is None and buffer_role is None and upper_buffer_role is None:
            await interaction.followup.send("❌ 未找到已验证/缓冲区身份组", ephemeral=True)
            return
            
        try:
            has_role = False
            roles_to_remove = []
            
            if role and role in member.roles:
                has_role = True
                roles_to_remove.append(role)
            if buffer_role and buffer_role in member.roles:
                has_role = True
                roles_to_remove.append(buffer_role)
            if upper_buffer_role and upper_buffer_role in member.roles:
                has_role = True
                roles_to_remove.append(upper_buffer_role)
                
            if has_role:
                for r in member.roles:
                    # 持有白名单身份组则无权处罚
                    if r.id in whitelist:
                        await interaction.followup.send("❌ 无法处罚此用户", ephemeral=True)
                        return
                       
                await member.remove_roles(*roles_to_remove, reason=f"答题处罚 by {interaction.user}")

                # 检查是否启用同步模块
                sync_cog = self.bot.get_cog("ServerSyncCommands")
                if sync_cog:
                    await sync_cog.sync_remove_role(interaction.guild, member, role, f"答题处罚 by {interaction.user}")
                    if buffer_role:
                        await sync_cog.sync_remove_role(interaction.guild, member, buffer_role, f"答题处罚 by {interaction.user}")
                    if upper_buffer_role:
                        await sync_cog.sync_remove_role(interaction.guild, member, upper_buffer_role, f"答题处罚 by {interaction.user}")
                else:
                    await member.remove_roles(role, buffer_role, upper_buffer_role, reason=f"答题处罚 by {interaction.user}")

                # 私聊通知
                try:
                    exam_embed = discord.Embed(title="🔴 答题处罚", description=f"您在 **{guild.name}** 因 **{reason}** 被要求重新答题。请重新阅读规则并注意遵守。")
                    exam_embed.set_footer(text=f"来自服务器: {guild.name}")
                    await dm.send_dm(member.guild, member, embed=exam_embed)
                except discord.Forbidden:
                    pass
                except Exception as e:
                    self.logger.error(f"答题处罚私聊通知失败: {e}")
                    
                await interaction.followup.send(f"✅ 已移除 {member.display_name} 的身份组并要求重新阅读规则", ephemeral=True)
                
                # 当前频道公示
                embed=discord.Embed(title="🔴 答题处罚", description=f"{member.mention} 因 {reason} 被 {interaction.user.mention} 要求重新答题。请注意遵守社区规则。")
                punish_record = await self._save_quiz_punish(member, reason, interaction.user.id)
                if punish_record:
                    newline = '\n'
                    punish_list_text = newline.join([f'{p["punish_time"]} {p["reason"]}' for p in punish_record['punish_list']])
                    embed.add_field(name="处罚记录", value=f"共 {punish_record['punish_count']} 次处罚\n{punish_list_text}")
                await interaction.channel.send(embed=embed)

                # 记录处罚日志
                if quiz_punish_log_channel_id:
                    quiz_punish_log_channel = interaction.guild.get_channel_or_thread(int(quiz_punish_log_channel_id))
                    if quiz_punish_log_channel:
                        embed=discord.Embed(title="🔴 答题处罚", description=f"{member.mention} 因 {reason} 被 {interaction.user.mention} 要求重新答题。")
                        if punish_record:
                            newline = '\n'
                            punish_list_text = newline.join([f'{p["punish_time"]} {p["reason"]}' for p in punish_record['punish_list']])
                            embed.add_field(name="处罚记录", value=f"共 {punish_record['punish_count']} 次处罚\n{punish_list_text}")
                        await quiz_punish_log_channel.send(embed=embed)
                        await quiz_punish_log_channel.send(content=f"用户名: {member.name}\n用户ID: {member.id}")

                # bot对接（使用服务器特定配置）
                bot_integration_channel_id = self.get_guild_config("bot_integration_channel_id", guild.id, 0)
                if bot_integration_channel_id:
                    await interaction.guild.get_channel_or_thread(int(bot_integration_channel_id)).send(content='{"punish": '+str(member.id)+'}')
            else:
                await interaction.followup.send("成员不在已验证/缓冲区身份组", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限移除身份组", ephemeral=True)

    # ---- 答疑组禁言 ----
    @app_commands.command(name="答疑组禁言", description="答疑组专用禁言")
    @app_commands.describe(
        member="要禁言的成员",
        time="禁言时长（5m, 12h, 1d）",
        reason="原因（可选）",
        img="图片（可选）",
        warn="警告天数"
    )
    @app_commands.rename(member="成员", time="时长", reason="原因", img="图片", warn="警告天数")
    @guild_only()
    async def qa_mute(
        self,
        interaction,  # type: discord.Interaction
        member: "discord.Member",
        time: str,
        reason: str = None,
        img: discord.Attachment = None,
        warn: int = 0,
    ):
        guild = interaction.guild
        # 检查是否为答疑组（使用服务器特定配置）
        qa_role_id = self.get_guild_config("qa_role_id", guild.id, 0)
        if not qa_role_id:
            await interaction.response.send_message("❌ 当前服务器未设置答疑组", ephemeral=True)
            return
        qa_role = guild.get_role(int(qa_role_id))
        if not qa_role:
            await interaction.response.send_message("❌ 答疑组角色不存在", ephemeral=True)
            return
        if not qa_role in interaction.user.roles:
            await interaction.response.send_message("❌ 您不是答疑组成员", ephemeral=True)
            return
        # 将字符串时间转换为数字时长
        mute_time, mute_time_str = self._parse_time(time)
        if mute_time == -1:
            await interaction.response.send_message("❌ 未知时间", ephemeral=True)
            return
        duration = datetime.timedelta(seconds=mute_time)
        
        await interaction.response.defer(ephemeral=True)
        if duration.total_seconds() <= 0 and warn <= 0:
            await interaction.followup.send("❌ 时长和警告天数不能同时为0", ephemeral=True)
            return
        if duration.total_seconds() > 7 * 24 * 60 * 60:
            await interaction.followup.send("❌ 禁言时长不能超过7天", ephemeral=True)
            return
        if warn > 30:
            await interaction.followup.send("❌ 警告天数不能超过30天", ephemeral=True)
            return
        try:
            if duration.total_seconds() > 0:
                await member.timeout(duration, reason=reason or "答疑组禁言")
            # 使用服务器特定配置
            warned_role_id = self.get_guild_config("warned_role_id", guild.id, 0)
            warned_role = guild.get_role(int(warned_role_id)) if warned_role_id else None
            if warned_role and warn > 0:
                sync_cog_ref = self.bot.get_cog("ServerSyncCommands")
                if sync_cog_ref:
                    sync_cog_ref._mark_guard(guild.id, member.id, warned_role.id, "add")
                await member.add_roles(warned_role, reason=f"答疑组禁言附加警告 {warn} 天")
        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限对该成员执行禁言", ephemeral=True)
            return
        # 保存记录 & 公示
        record_id = self._save_punish_record(guild.id, {
            "type": "mute",
            "user_id": member.id,
            "moderator_id": interaction.user.id,
            "reason": reason,
            "warn": warn,
            "duration": duration.total_seconds(),
        })

        if warn > 0:
            self._save_warn_record(guild.id, {
                "type": "warn",
                "user_id": member.id,
                "moderator_id": interaction.user.id,
                "reason": reason,
                "until": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=warn)).isoformat(),
            })

        # 同步处罚到其他服务器
        sync_cog = self.bot.get_cog("ServerSyncCommands")
        if sync_cog:
            await sync_cog.sync_punishment(
                guild=guild,
                punishment_type="mute",
                member=member,
                moderator=interaction.user,
                reason=reason,
                duration=duration.total_seconds() if duration.total_seconds() > 0 else None,
                warn_days=warn,
                punishment_id=record_id,
                img=img
            )

        await interaction.followup.send(f"✅ 已禁言 {member.mention} ({mute_time_str})。处罚ID: `{record_id}`", ephemeral=True)

        # 私聊通知
        guild_name = guild.name
        if duration.total_seconds() > 0:
            try:
                dm_embed = discord.Embed(title="🔇 禁言处罚", description=f"您在 **{guild_name}** 因 **{reason}** 被禁言 {mute_time_str}。请注意遵守社区规则。")
                dm_embed.set_footer(text=f"来自服务器: {guild_name}")
                await dm.send_dm(member.guild, member, embed=dm_embed)
            except discord.Forbidden:
                pass
            except Exception as e:
                self.logger.error(f"禁言处罚私聊通知失败: {e}")
        elif warn > 0:
            try:
                dm_embed = discord.Embed(title="⚠️ 警告处罚", description=f"您在 **{guild_name}** 因 **{reason}** 被警告 {warn} 天。请注意遵守社区规则。")
                dm_embed.set_footer(text=f"来自服务器: {guild_name}")
                await dm.send_dm(member.guild, member, embed=dm_embed)
            except discord.Forbidden:
                pass
            except Exception as e:
                self.logger.error(f"警告处罚私聊通知失败: {e}")

        # 当前频道公示
        if duration.total_seconds() > 0:
            await interaction.followup.send(embed=discord.Embed(title="🔇 禁言处罚", description=f"{member.mention} 因 {reason} 被禁言 {mute_time_str}。请注意遵守社区规则。"), ephemeral=False)
        elif warn > 0:
            await interaction.followup.send(embed=discord.Embed(title="⚠️ 警告处罚", description=f"{member.mention} 因 {reason} 被警告 {warn} 天。请注意遵守社区规则。"), ephemeral=False)

        # 公示频道 + 记录日志（使用服务器特定配置）
        channel_id = self.get_guild_config("punish_announce_channel_id", guild.id, 0)
        announce_channel = guild.get_channel(int(channel_id)) if channel_id else None
        quiz_punish_log_channel_id = self.get_guild_config("quiz_punish_log_channel_id", guild.id, 0)
        quiz_punish_log_channel = guild.get_channel_or_thread(int(quiz_punish_log_channel_id)) if quiz_punish_log_channel_id else None
        if announce_channel or quiz_punish_log_channel:
            img_bytes = None
            img_filename = None
            if img:
                try:
                    img_bytes = await img.read()
                    img_filename = img.filename
                except Exception:
                    pass
            embed = discord.Embed(title="🔇 禁言处罚" if duration.total_seconds() > 0 else "⚠️ 警告处罚", color=discord.Color.orange())
            if duration.total_seconds() > 0:
                embed.add_field(name="时长", value=mute_time_str)
            embed.add_field(name="成员", value=member.mention)
            embed.add_field(name="答疑组成员", value=interaction.user.mention)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="原因", value=reason or "未提供", inline=False)
            if warn > 0:
                embed.add_field(name="警告", value=f"{warn}天", inline=False)
            if img_bytes:
                embed.set_image(url=f"attachment://{img_filename}")
            embed.set_footer(text=f"处罚ID: {record_id}")
            if announce_channel:
                await announce_channel.send(
                    embed=embed,
                    file=discord.File(io.BytesIO(img_bytes), filename=img_filename) if img_bytes else None,
                )
            if quiz_punish_log_channel:
                await quiz_punish_log_channel.send(
                    embed=embed,
                    file=discord.File(io.BytesIO(img_bytes), filename=img_filename) if img_bytes else None,
                )

    # ---- 发送公益站地址 ----
    @app_commands.command(name="发送公益站地址", description="发送公益站地址")
    @app_commands.describe(
        member="要发送的成员",
    )
    @app_commands.rename(member="成员")
    async def send_charity_site_address(self, interaction: discord.Interaction, member: "discord.Member"):
        await interaction.response.defer(ephemeral=True)
        # 使用服务器特定配置
        site = self.get_guild_config("charity_site_address", interaction.guild.id, "")
        if not site:
            await interaction.followup.send("❌ 未配置公益站地址", ephemeral=True)
            return

        preset_message = f"# 公益站审核通知\n✅ 恭喜你通过类脑DeepThink公益站审核\n站点地址：{site}\n请勿在任何地方传播此站点！"
        await dm.send_dm(interaction.guild, member, message=preset_message)

        # 记录日志（使用服务器特定配置）
        moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", interaction.guild.id, 0)
        moderation_log_channel = interaction.guild.get_channel_or_thread(int(moderation_log_channel_id)) if moderation_log_channel_id else None
        if moderation_log_channel:
            await moderation_log_channel.send(embed=discord.Embed(title="🔴 公益站地址", description=f"审核员 {interaction.user.mention} 发送了公益站地址到 {member.mention}。"))

        await interaction.followup.send(f"✅ 已发送公益站地址到 {member.mention}", ephemeral=True)

    @app_commands.command(name="解散服务器", description="解散服务器")
    async def disband_server(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        # Fake Command, but log the attempt to moderation log channel（使用服务器特定配置）
        moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", interaction.guild.id, 0)
        moderation_log_channel = interaction.guild.get_channel_or_thread(int(moderation_log_channel_id)) if moderation_log_channel_id else None
        if moderation_log_channel:
            await moderation_log_channel.send(embed=discord.Embed(title="🔴 解散服务器", description=f"用户 {interaction.user.mention} 尝试解散服务器。"))
        await interaction.followup.send("❌ 权限不足", ephemeral=True)
        return