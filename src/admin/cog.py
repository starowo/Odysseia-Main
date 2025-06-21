import asyncio
from discord.ext import commands
from discord import app_commands
import discord
import json
import uuid
import datetime
import pathlib
from typing import List, Tuple

from src.utils.confirm_view import confirm_view

class AdminCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
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
        asyncio.create_task(self._auto_remove_warn())
        if self.logger:
            self.logger.info("警告自动移除任务已启动")
    
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
                        for file in guild_dir.glob("*.json"):
                            try:
                                with open(file, "r", encoding="utf-8") as f:
                                    warn_record = json.load(f)
                                    
                                if warn_record.get("until", None):
                                    until_time = datetime.datetime.fromisoformat(warn_record["until"])
                                    if datetime.datetime.now(datetime.timezone.utc) > until_time:
                                        # 获取用户对象并移除警告身份组
                                        user_id = warn_record.get("user_id")
                                        if user_id:
                                            try:
                                                member = guild.get_member(user_id)
                                                if member:
                                                    warned_role_id = self.config.get("warned_role_id", 0)
                                                    warned_role = guild.get_role(int(warned_role_id)) if warned_role_id else None
                                                    if warned_role and warned_role in member.roles:
                                                        await member.remove_roles(warned_role, reason=f"警告到期自动移除 by {self.bot.user}")
                                                        if self.logger:
                                                            self.logger.info(f"自动移除警告: 用户 {member} (ID: {user_id}) 在服务器 {guild.name}")
                                                # 删除警告记录文件
                                                file.unlink(missing_ok=True)
                                            except Exception as e:
                                                if self.logger:
                                                    self.logger.error(f"移除警告身份组失败: 用户ID {user_id}, 错误: {e}")
                                                # 即使移除身份组失败，也删除过期的记录文件
                                                file.unlink(missing_ok=True)
                            except Exception as e:
                                if self.logger:
                                    self.logger.error(f"处理警告文件失败: {file}, 错误: {e}")
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"处理服务器警告目录失败: {guild_dir}, 错误: {e}")
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
    
    async def is_admin(self, interaction: discord.Interaction) -> bool:
        """检查用户是否为管理员（配置中的管理员身份组或服务器管理员）"""
        try:
            # 检查是否是高级管理员
            if await self.is_senior_admin(interaction):
                return True
                
            # 检查是否拥有配置中的管理员身份组
            config = self.config
            for admin_role_id in config.get('admins', []):
                role = interaction.guild.get_role(admin_role_id)
                if role and role in interaction.user.roles:
                    return True
            
            return False
        except Exception:
            return False
    
    async def is_senior_admin(self, interaction: discord.Interaction) -> bool:
        """检查用户是否为高级管理员（配置中的高级管理员身份组或服务器管理员）"""
        try:
            # 检查是否是服务器管理员
            if interaction.user.guild_permissions.administrator:
                return True
                
            # 检查是否拥有配置中的高级管理员身份组
            config = self.config
            for senior_admin_role_id in config.get('senior_admins', []):
                role = interaction.guild.get_role(senior_admin_role_id)
                if role and role in interaction.user.roles:
                    return True
            return False
        except Exception:
            return False
    
    # ---- 工具函数：将字符串时间转换为数字时长 ----
    def _parse_time(self, time_str: str) -> tuple[int, str]:
        """将字符串时间转换为数字时长"""
        if time_str.endswith("m"):
            return int(time_str[:-1]) * 60, time_str[:-1] + "分钟"
        elif time_str.endswith("h"):
            return int(time_str[:-1]) * 3600, time_str[:-1] + "小时"
        elif time_str.endswith("d"):
            return int(time_str[:-1]) * 86400, time_str[:-1] + "天"
        else:
            return -1, "未知时间"
    
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
    async def add_role(
        self,
        interaction, # type: discord.Interaction
        member: "discord.Member",
        action: str,
        role: "discord.Role",
        reason: str = None,
    ):
        # 检查管理员权限
        if not await self.is_admin(interaction):
            await interaction.response.send_message("❌ 您没有权限使用此命令", ephemeral=True)
            return
            
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("此命令只能在服务器中使用", ephemeral=True)
            return

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
    async def bulk_delete_messages(
        self,
        interaction, # type: discord.Interaction
        start_message: str,
        end_message: str,
    ):
        # 检查高级管理员权限
        if not await self.is_senior_admin(interaction):
            await interaction.response.send_message("❌ 您没有权限使用此命令", ephemeral=True)
            return
            
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
            async for message in channel.history(limit=100, after=current_after, before=end_message_obj.created_at + datetime.timedelta(seconds=1)):
                # 确保消息在时间范围内
                if start_message_obj.created_at <= message.created_at <= end_message_obj.created_at:
                    fetched.append(message)
                    
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
    async def bulk_move_role(
        self,
        interaction,  # type: discord.Interaction
        source_role: "discord.Role",
        target_role: "discord.Role",
        remove_source: bool = False,
        limit: int = 100
    ):
        # 检查高级管理员权限
        if not await self.is_senior_admin(interaction):
            await interaction.response.send_message("❌ 您没有权限使用此命令", ephemeral=True)
            return
            
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
                if affected >= limit:
                    break
            except discord.Forbidden:
                continue
        await interaction.edit_original_response(content=f"✅ 已对 {affected} 名成员完成身份组转移")

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
    async def mute_member(
        self,
        interaction,  # type: discord.Interaction
        member: "discord.Member",
        time: str,
        reason: str = None,
        img: discord.Attachment = None,
        warn: int = 0,
    ):
        # 检查管理员权限
        if not await self.is_admin(interaction):
            await interaction.response.send_message("❌ 您没有权限使用此命令", ephemeral=True)
            return
            
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("此命令只能在服务器中使用", ephemeral=True)
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
        try:
            if duration.total_seconds() > 0:
                await member.timeout(duration, reason=reason or "管理员禁言")
            warned_role_id = self.config.get("warned_role_id", 0)
            warned_role = guild.get_role(int(warned_role_id))
            if warned_role and warn > 0:
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

        # 检查是否启用处罚同步
        sync_cog = self.bot.get_cog("ServerSyncCommands")
        if sync_cog:
            await sync_cog.sync_punishment(
                guild=guild,
                punishment_type="mute",
                member=member,
                moderator=interaction.user,
                reason=reason,
                duration=int(duration.total_seconds()) if duration.total_seconds() > 0 else None,
                warn_days=warn,
                punishment_id=record_id,
                img=img
            )

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
        if duration.total_seconds() > 0:
            try:
                await member.send(embed=discord.Embed(title="🔇 禁言处罚", description=f"您因 {reason} 被禁言 {mute_time_str}。请注意遵守社区规则。"))
            except discord.Forbidden:
                pass
        elif warn > 0:
            try:
                await member.send(embed=discord.Embed(title="⚠️ 警告处罚", description=f"您因 {reason} 被警告 {warn} 天。请注意遵守社区规则。"))
            except discord.Forbidden:
                pass

        # 当前频道公示
        if duration.total_seconds() > 0:
            await interaction.followup.send(embed=discord.Embed(title="🔇 禁言处罚", description=f"{member.mention} 因 {reason} 被禁言 {mute_time_str}。请注意遵守社区规则。"), ephemeral=False)
        elif warn > 0:
            await interaction.followup.send(embed=discord.Embed(title="⚠️ 警告处罚", description=f"{member.mention} 因 {reason} 被警告 {warn} 天。请注意遵守社区规则。"), ephemeral=False)

        # 公示频道
        channel_id = self.config.get("punish_announce_channel_id", 0)
        announce_channel = guild.get_channel(int(channel_id))
        if announce_channel:
            embed = discord.Embed(title="🔇 禁言处罚" if duration.total_seconds() > 0 else "⚠️ 警告处罚", color=discord.Color.orange())
            if duration.total_seconds() > 0:
                embed.add_field(name="时长", value=mute_time_str)
            embed.add_field(name="成员", value=member.mention)
            embed.add_field(name="管理员", value=interaction.user.mention)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="原因", value=reason or "未提供", inline=False)
            if warn > 0:
                embed.add_field(name="警告", value=f"{warn}天", inline=False)
            if img:
                embed.set_image(url=img.url)
            embed.set_footer(text=f"处罚ID: {record_id}")
            await announce_channel.send(embed=embed)

    # ---- 永封 ----
    @admin.command(name="永封", description="永久封禁成员并公示")
    @app_commands.describe(member="要封禁的成员", user_id="用户ID（可直接封禁不在服务器的用户）", reason="原因（可选）", img="图片（可选）", delete_message_days="删除消息天数（0-7）")
    @app_commands.rename(member="成员", user_id="用户id", reason="原因", img="图片", delete_message_days="删除消息天数")
    async def ban_member(
        self,
        interaction,  # type: discord.Interaction
        member: "discord.Member" = None,
        user_id: str = None,
        reason: str = None,
        img: discord.Attachment = None,
        delete_message_days: int = 0,
    ):
        # 检查高级管理员权限
        if not await self.is_senior_admin(interaction):
            await interaction.response.send_message("❌ 您没有权限使用此命令", ephemeral=True)
            return
            
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("此命令只能在服务器中使用", ephemeral=True)
            return

        # 验证至少提供了一个参数
        if not member and not user_id:
            await interaction.response.send_message("❌ 请提供要封禁的成员或用户ID", ephemeral=True)
            return
            
        # 验证不能同时提供两个参数
        if member and user_id:
            await interaction.response.send_message("❌ 请只提供成员或用户ID中的一个", ephemeral=True)
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

        # 私聊通知（仅当能获取到用户对象时）
        if target_user is not None:
            try:
                await target_user.send(embed=discord.Embed(title="⛔ 永久封禁", description=f"您因 {reason} 被永久封禁。如有异议，请联系管理组成员。"))
            except discord.Forbidden:
                pass
            except Exception:
                # 发送私聊失败，继续执行
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

        # 公示频道
        channel_id = self.config.get("punish_announce_channel_id", 0)
        announce_channel = guild.get_channel(int(channel_id))
        if announce_channel:
            embed = discord.Embed(title="⛔ 永久封禁", color=discord.Color.red())
            embed.add_field(name="成员", value=f"{target_user_name} ({target_user_id})")
            embed.add_field(name="管理员", value=interaction.user.mention)
            if target_user_avatar:
                embed.set_thumbnail(url=target_user_avatar)
            embed.add_field(name="原因", value=reason or "未提供", inline=False)
            if img:
                embed.set_image(url=img.url)
            embed.set_footer(text=f"处罚ID: {record_id}")
            await announce_channel.send(embed=embed)

    # ---- 撤销处罚 ----
    @admin.command(name="撤销处罚", description="按ID撤销处罚")
    @app_commands.describe(punish_id="处罚ID", reason="原因（可选）")
    async def revoke_punish(self, interaction, punish_id: str, reason: str = None):
        # 检查高级管理员权限
        if not await self.is_senior_admin(interaction):
            await interaction.response.send_message("❌ 您没有权限使用此命令", ephemeral=True)
            return
            
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("此命令只能在服务器中使用", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        
        try:
            record, path = self._get_punish_record(guild.id, punish_id)
            if record is None:
                await interaction.followup.send("❌ 未找到对应处罚记录", ephemeral=True)
                return

            user_id = int(record["user_id"])
            user_obj = None
            user_mention = f"<@{user_id}>"  # 默认mention，防止获取用户失败
            
            if record["type"] == "mute":
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
                        warned_role_id = self.config.get("warned_role_id", 0)
                        warned_role = guild.get_role(int(warned_role_id))
                        if warned_role:
                            await user_obj.remove_roles(warned_role, reason=f"撤销处罚附加警告 {record['warn']} 天")
                except discord.Forbidden:
                    await interaction.followup.send("❌ 无权限解除禁言", ephemeral=True)
                    return
                    
            elif record["type"] == "ban":
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

            # 同步撤销处罚到其他服务器
            sync_cog = self.bot.get_cog("ServerSyncCommands")
            if sync_cog:
                await sync_cog.sync_revoke_punishment(guild, punish_id, interaction.user, reason)

            # 公示
            channel_id = self.config.get("punish_announce_channel_id", 0)
            announce_channel = guild.get_channel(int(channel_id))
            if announce_channel:
                embed = discord.Embed(title="🔓 撤销处罚", color=discord.Color.green())
                embed.add_field(name="处罚ID", value=punish_id)
                embed.add_field(name="成员", value=user_mention)
                embed.add_field(name="原因", value=reason or "未提供", inline=False)
                try:
                    await announce_channel.send(embed=embed)
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"发送撤销处罚公示失败: {e}")
                
        except Exception as e:
            # 捕获所有未预期的异常，防止交互卡死
            if self.logger:
                self.logger.error(f"撤销处罚时发生错误: {e}")
            await interaction.followup.send("❌ 撤销处罚时发生错误，请检查处罚ID是否正确", ephemeral=True)

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
    async def manage_channel(
        self,
        interaction,  # type: discord.Interaction
        channel: "discord.TextChannel",
        new_name: str = None,
        slowmode: app_commands.Choice[int] = None,
        nsfw: bool = None,
        auto_archive: app_commands.Choice[int] = None,
    ):
        # 检查管理员权限
        if not await self.is_admin(interaction):
            await interaction.response.send_message("❌ 您没有权限使用此命令", ephemeral=True)
            return
            
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
            await channel.edit(**params, reason=f"频道管理 by {interaction.user}")
            await interaction.followup.send("✅ 频道已更新", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ 无修改权限", ephemeral=True)

    # ---- 一键删帖 ----
    @admin.command(name="一键删帖", description="一键删除某成员发布的全部帖子")
    @app_commands.describe(member="要删除帖子的成员ID", channel="要删除帖子的频道")
    @app_commands.rename(member="成员id", channel="频道")
    async def delete_all_threads(self, interaction: discord.Interaction, member: str, channel: "discord.ForumChannel"):
        # 检查高级管理员权限
        if not await self.is_senior_admin(interaction):
            await interaction.response.send_message("❌ 您没有权限使用此命令", ephemeral=True)
            return
            
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
        
        if self.logger:
            self.logger.info(f"一键删帖完成: 用户{member_id}，共删除{len(deleted)}个帖子，操作者: {interaction.user.id}")

    # ---- 子区管理 ----
    thread_manage_group = app_commands.Group(name="子区管理", description="子区线程管理", parent=admin)

    @thread_manage_group.command(name="锁定", description="锁定线程")
    @app_commands.describe(thread="要锁定的子区（留空则为当前子区）")
    @app_commands.rename(thread="子区")
    async def lock_thread_admin(
        self, 
        interaction, 
        thread: "discord.Thread" = None
    ):
        # 检查管理员权限
        if not await self.is_admin(interaction):
            await interaction.response.send_message("❌ 您没有权限使用此命令", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        if thread is None:
            thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.followup.send("❌ 请指定一个子区", ephemeral=True)
            return
            
        if thread.locked:
            await interaction.followup.send("已锁定", ephemeral=True)
            return
        try:
            await thread.edit(locked=True, archived=False, reason=f"锁定 by {interaction.user}")
            await interaction.followup.send("✅ 已锁定线程", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 锁定失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="解锁", description="解锁线程")
    @app_commands.describe(thread="要解锁的子区（留空则为当前子区）")
    @app_commands.rename(thread="子区")
    async def unlock_thread_admin(
        self, 
        interaction, 
        thread: "discord.Thread" = None
    ):
        # 检查管理员权限
        if not await self.is_admin(interaction):
            await interaction.response.send_message("❌ 您没有权限使用此命令", ephemeral=True)
            return
            
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
            await interaction.followup.send("✅ 已解锁线程", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 解锁失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="archive", description="归档线程")
    @app_commands.describe(thread="要归档的子区（留空则为当前子区）")
    @app_commands.rename(thread="子区")
    async def archive_thread_admin(
        self, 
        interaction, 
        thread: "discord.Thread" = None
    ):
        # 检查管理员权限
        if not await self.is_admin(interaction):
            await interaction.response.send_message("❌ 您没有权限使用此命令", ephemeral=True)
            return
            
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
            await interaction.followup.send("✅ 已归档线程", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 归档失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="unarchive", description="取消归档线程")
    @app_commands.describe(thread="要取消归档的子区（留空则为当前子区）")
    @app_commands.rename(thread="子区")
    async def unarchive_thread_admin(
        self, 
        interaction, 
        thread: "discord.Thread" = None
    ):
        # 检查管理员权限
        if not await self.is_admin(interaction):
            await interaction.response.send_message("❌ 您没有权限使用此命令", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        if thread is None:
            thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.followup.send("❌ 请指定一个子区", ephemeral=True)
            return
        if not thread.archived:
            await interaction.followup.send("未归档", ephemeral=True)
            return
        try:
            await thread.edit(archived=False, locked=False, reason=f"取消归档 by {interaction.user}")
            await interaction.followup.send("✅ 已取消归档", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 取消归档失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="pin", description="置顶")
    @app_commands.describe(thread="要置顶的子区（留空则为当前子区）")
    @app_commands.rename(thread="子区")
    async def pin_in_thread_admin(
        self,
        interaction,
        thread: "discord.Thread" = None
    ):
        # 检查管理员权限
        if not await self.is_admin(interaction):
            await interaction.response.send_message("❌ 您没有权限使用此命令", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        if thread is None:
            thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.followup.send("❌ 请指定一个子区", ephemeral=True)
            return
        try:
            await thread.pin(reason=f"管理员置顶 by {interaction.user}")
            await interaction.followup.send("✅ 已置顶线程", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 置顶失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="unpin", description="取消置顶")
    async def unpin_in_thread_admin(
        self,
        interaction,
        thread: "discord.Thread" = None
    ):
        # 检查管理员权限
        if not await self.is_admin(interaction):
            await interaction.response.send_message("❌ 您没有权限使用此命令", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        if thread is None:
            thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.followup.send("❌ 请指定一个子区", ephemeral=True)
            return
        try:
            await thread.unpin(reason=f"管理员取消置顶 by {interaction.user}")
            await interaction.followup.send("✅ 已取消置顶", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 取消置顶失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="删帖", description="删除线程")
    @app_commands.describe(thread="要删除的子区（留空则为当前子区）")
    @app_commands.rename(thread="子区")
    async def delete_thread_admin(
        self,
        interaction,
        thread: "discord.Thread" = None
    ):  
        # 检查管理员权限
        if not await self.is_admin(interaction):
            await interaction.response.send_message("❌ 您没有权限使用此命令", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        if thread is None:
            thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.followup.send("❌ 请指定一个子区", ephemeral=True)
            return
        
        confirmed = await confirm_view(
            interaction,
            title="🔴 删除子区",
            description=f"确定要删除 【{thread.name}】 吗？"
        )

        if not confirmed:
            await interaction.followup.send("❌ 已取消", ephemeral=True)
            return
        
        try:
            await thread.delete(reason=f"管理员删帖 by {interaction.user}")
        except Exception as e:
            await interaction.followup.send(f"❌ 删除失败: {e}", ephemeral=True)

    # ---- 答题处罚 ----
    @app_commands.command(name="答题处罚", description="移除身份组送往答题区")
    @app_commands.describe(member="要处罚的成员", reason="原因（可选）")
    @app_commands.rename(member="成员", reason="原因")
    async def quiz_punish(self, interaction, member: "discord.Member", reason: str = None):
            
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        
        # 使用服务器特定配置而不是全局配置
        role_id = self.config.get("verified_role_id", 0)
        buffer_role_id = self.config.get("buffer_role_id", 0)
        whitelist = self.config.get("quiz_punish_whitelist", [])
        
        role = guild.get_role(int(role_id)) if role_id else None
        buffer_role = guild.get_role(int(buffer_role_id)) if buffer_role_id else None
        
        if role is None and buffer_role is None:
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
                else:
                    await member.remove_roles(role, buffer_role, reason=f"答题处罚 by {interaction.user}")

                # 私聊通知
                try:    
                    await member.send(embed=discord.Embed(title="🔴 答题处罚", description=f"您因 {reason} 被移送答题区。请重新阅读规则并遵守。"))
                except discord.Forbidden:
                    pass
                    
                await interaction.followup.send(f"✅ 已移除 {member.display_name} 的身份组并要求重新阅读规则", ephemeral=True)
                
                # 当前频道公示
                await interaction.channel.send(embed=discord.Embed(title="🔴 答题处罚", description=f"{member.mention} 因 {reason} 被 {interaction.user.mention} 移送答题区。请注意遵守社区规则。"))
            else:
                await interaction.followup.send("成员不在已验证/缓冲区身份组", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限移除身份组", ephemeral=True)