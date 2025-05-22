import asyncio
from discord.ext import commands
from discord import app_commands
import discord
import json
import uuid
import datetime
import pathlib
from typing import Tuple

from src.utils.confirm_view import confirm_view

class AdminCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger
        self.name = "管理命令"
        self.config = None
        # 从main.py加载配置
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                self.config = json.load(f)
        except Exception as e:
            if self.logger:
                self.logger.error(f"加载配置文件失败: {e}")

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
            guild = self.bot.get_guild(int(self.config.get("guild_id", 0)))
            # 遍历警告文件，时间到则移除并删除文件
            warn_dir = pathlib.Path("data") / "warn" / str(guild.id)
            if warn_dir.exists():
                for file in warn_dir.glob("*.json"):
                    with open(file, "r", encoding="utf-8") as f:
                        warn_record = json.load(f)
                        if warn_record.get("until", None) and datetime.datetime.now(datetime.UTC) > datetime.datetime.fromisoformat(warn_record["until"]):
                            await guild.remove_roles(warn_record["user_id"], reason=f"警告移除 by {self.bot.user}")
                            file.unlink(missing_ok=True)

    def is_admin():
        async def predicate(ctx):
            # 在运行时重新加载配置以获取最新的管理员列表
            try:
                with open('config.json', 'r', encoding='utf-8') as f:
                    config = json.load(f)
                return ctx.author.id in config.get('admins', [])
            except Exception:
                return False
        return commands.check(predicate)
    
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
        record["timestamp"] = datetime.datetime.utcnow().isoformat()

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
        record["timestamp"] = datetime.datetime.utcnow().isoformat()

        warn_dir = pathlib.Path("data") / "warn" / str(guild_id)
        warn_dir.mkdir(parents=True, exist_ok=True)
        with open(warn_dir / f"{record_id}.json", "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        return record_id

    # ---- 添加/移除身份组 ----
    @admin.command(name="身份组", description="添加/移除身份组")
    @is_admin()
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
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("此命令只能在服务器中使用", ephemeral=True)
            return
        
        # 阻止用户操作比自己权限高的身份组
        if role.position >= interaction.user.top_role.position:
            await interaction.followup.send("❌ 无法操作比自己权限高的身份组", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        if action == "添加":
            await member.add_roles(role, reason=reason)
        elif action == "移除":
            await member.remove_roles(role, reason=reason)
        
        await interaction.followup.send(f"✅ 已{action}身份组 {role.mention} {member.mention}", ephemeral=True)

    # ---- 批量删除消息 ----
    @admin.command(name="批量删除消息", description="在当前频道，从指定消息开始到指定消息结束，删除全部消息")
    @is_admin()
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
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = interaction.channel
        if channel is None:
            await interaction.followup.send("此命令只能在频道中使用", ephemeral=True)
            return
        start_message = await channel.fetch_message(int(start_message.split("/")[-1]))
        end_message = await channel.fetch_message(int(end_message.split("/")[-1]))
        if start_message.channel.id != channel.id or end_message.channel.id != channel.id:
            await interaction.followup.send("消息必须在当前频道", ephemeral=True)
            return
        if start_message.created_at > end_message.created_at:
            await interaction.followup.send("开始消息必须在结束消息之前", ephemeral=True)
            return
        
                # 调用统一的确认视图
        confirmed = await confirm_view(
            interaction,
            title="批量删除消息",
            description="\n".join(
                [
                    f"确定要删除从 {start_message.created_at} 到 {end_message.created_at} 的消息吗？",
                ]
            ),
            colour=discord.Colour(0x808080),
            timeout=60,
        )

        if not confirmed:
            return

        deleted = 0
        # 一次100条，分批删除，从start_message开始，到end_message结束
        while True:
            fetched = await channel.history(limit=100, after=start_message, before=end_message)
            if len(fetched) == 0:
                break
            await channel.delete_messages(fetched)
            start_message = fetched[-1]
            deleted += len(fetched)
            await interaction.response.edit_message(f"已删除 {deleted} 条消息", ephemeral=True)
        

    # ---- 批量转移身份组 ----
    @admin.command(name="批量转移身份组", description="给具有指定身份组的成员添加新身份组，可选是否移除原身份组")
    @is_admin()
    @app_commands.describe(
        source_role="需要转移的原身份组",
        target_role="要添加的新身份组",
        remove_source="是否移除原身份组"
    )
    @app_commands.rename(source_role="原身份组", target_role="新身份组", remove_source="移除原身份组")
    async def bulk_move_role(
        self,
        interaction,  # type: discord.Interaction
        source_role: "discord.Role",
        target_role: "discord.Role",
        remove_source: bool = False,
    ):
        guild: discord.Guild = interaction.guild

        await interaction.response.defer(ephemeral=True, thinking=True)

        affected = 0
        for member in guild.members:
            if source_role in member.roles and target_role not in member.roles:
                try:
                    await member.add_roles(target_role, reason=f"批量转移身份组 by {interaction.user}")
                    if remove_source:
                        await member.remove_roles(source_role, reason=f"批量转移身份组 remove source by {interaction.user}")
                    affected += 1
                except discord.Forbidden:
                    continue
        await interaction.followup.send(f"✅ 已对 {affected} 名成员完成身份组转移", ephemeral=True)

    # ---- 禁言 ----
    @admin.command(name="禁言", description="将成员禁言（最长28天）并公示")
    @is_admin()
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
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("此命令只能在服务器中使用", ephemeral=True)
            return

        # 将字符串时间转换为数字时长
        mute_time, mute_time_str = self._parse_time(time)
        if mute_time == -1:
            await interaction.followup.send("❌ 未知时间", ephemeral=True)
            return
        
        duration = datetime.timedelta(seconds=mute_time)

        await interaction.response.defer(ephemeral=True)
        try:
            await member.timeout(duration, reason=reason or "管理员禁言")
            warned_role = guild.get_role(int(self.config.get("warned_role_id", 0)))
            if warned_role and warn > 0:
                await member.add_roles(warned_role, reason=f"禁言附加警告 {warn} 天")
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
                "until": datetime.datetime.utcnow() + datetime.timedelta(days=warn),
            })


        await interaction.followup.send(f"✅ 已禁言 {member.mention} ({mute_time_str})。处罚ID: `{record_id}`", ephemeral=True)

        # 私聊通知
        try:
            await member.send(embed=discord.Embed(title="🔇 禁言处罚", description=f"您因 {reason} 被禁言 {mute_time_str}。请注意遵守社区规则。"))
        except discord.Forbidden:
            pass

        # 当前频道公示
        await interaction.followup.send(embed=discord.Embed(title="🔇 禁言处罚", description=f"{member.mention} 因 {reason} 被禁言 {mute_time_str}。请注意遵守社区规则。"), ephemeral=False)

        # 公示频道
        channel_id = int(self.config.get("punish_announce_channel_id", 0))
        announce_channel = guild.get_channel(channel_id)
        if announce_channel:
            embed = discord.Embed(title="🔇 禁言处罚", color=discord.Color.orange())
            embed.add_field(name="成员", value=member.mention)
            embed.add_field(name="时长", value=mute_time_str)
            embed.add_field(name="原因", value=reason or "未提供", inline=False)
            embed.add_field(name="警告", value=f"{warn}天", inline=False)
            if img:
                embed.set_image(url=img.url)
            embed.set_footer(text=f"处罚ID: {record_id}")
            await announce_channel.send(embed=embed)

    # ---- 永封 ----
    @admin.command(name="永封", description="永久封禁成员并公示")
    @is_admin()
    @app_commands.describe(member="要封禁的成员", reason="原因（可选）", img="图片（可选）", delete_message_days="删除消息天数（0-7）")
    @app_commands.rename(member="成员", reason="原因", img="图片", delete_message_days="删除消息天数")
    async def ban_member(
        self,
        interaction,  # type: discord.Interaction
        member: "discord.Member",
        reason: str = None,
        img: discord.Attachment = None,
        delete_message_days: int = 0,
    ):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("此命令只能在服务器中使用", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        # 私聊通知
        try:
            await member.send(embed=discord.Embed(title="⛔ 永久封禁", description=f"您因 {reason} 被永久封禁。如有异议，请联系管理组成员。"))
        except discord.Forbidden:
            pass
        try:
            await guild.ban(member, reason=reason, delete_message_days=delete_message_days)
        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限封禁该成员", ephemeral=True)
            return

        # 保存记录 & 公示
        record_id = self._save_punish_record(guild.id, {
            "type": "ban",
            "user_id": member.id,
            "moderator_id": interaction.user.id,
            "reason": reason,
        })

        await interaction.followup.send(f"✅ 已永久封禁 {member.name}。处罚ID: `{record_id}`", ephemeral=True)


        # 当前频道公示
        await interaction.followup.send(embed=discord.Embed(title="⛔ 永久封禁", description=f"{member.mention} 因 {reason} 被永久封禁。请注意遵守社区规则。"), ephemeral=False)

        # 公示频道
        channel_id = int(self.config.get("punish_announce_channel_id", 0))
        announce_channel = guild.get_channel(channel_id)
        if announce_channel:
            embed = discord.Embed(title="⛔ 永久封禁", color=discord.Color.red())
            embed.add_field(name="成员", value=f"{member} ({member.id})")
            embed.add_field(name="原因", value=reason or "未提供", inline=False)
            if img:
                embed.set_image(url=img.url)
            embed.set_footer(text=f"处罚ID: {record_id}")
            await announce_channel.send(embed=embed)

    # ---- 撤销处罚 ----
    @admin.command(name="撤销处罚", description="按ID撤销处罚")
    @is_admin()
    @app_commands.describe(punish_id="处罚ID", reason="原因（可选）")
    async def revoke_punish(self, interaction, punish_id: str, reason: str = None):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("此命令只能在服务器中使用", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        record, path = self._get_punish_record(guild.id, punish_id)
        if record is None:
            await interaction.followup.send("❌ 未找到对应处罚记录", ephemeral=True)
            return

        user_id = int(record["user_id"])
        user_obj = guild.get_member(user_id) or await guild.fetch_member(user_id)

        if record["type"] == "mute":
            try:
                await user_obj.timeout(None, reason="撤销处罚")
                if record.get("warn", 0) > 0:
                    warned_role = guild.get_role(int(self.config.get("warned_role_id", 0)))
                    await user_obj.remove_roles(warned_role, reason=f"撤销处罚附加警告 {record['warn']} 天")
            except discord.Forbidden:
                await interaction.followup.send("❌ 无权限解除禁言", ephemeral=True)
                return
        elif record["type"] == "ban":
            try:
                await guild.unban(discord.Object(id=user_id), reason="撤销处罚")
            except discord.Forbidden:
                await interaction.followup.send("❌ 无权限解除封禁", ephemeral=True)
                return
        else:
            await interaction.followup.send("❌ 未知处罚类型", ephemeral=True)
            return

        # 删除记录文件
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass

        # 公示
        channel_id = int(self.config.get("punish_announce_channel_id", 0))
        announce_channel = guild.get_channel(channel_id)
        if announce_channel:
            embed = discord.Embed(title="🔓 撤销处罚", color=discord.Color.green())
            embed.add_field(name="处罚ID", value=punish_id)
            embed.add_field(name="成员", value=user_obj.mention)
            embed.add_field(name="原因", value=reason or "未提供", inline=False)
            await announce_channel.send(embed=embed)

        await interaction.followup.send(f"✅ 已撤销处罚 {punish_id}", ephemeral=True)

    # ---- 频道管理 ----
    @admin.command(name="频道管理", description="编辑频道属性")
    @is_admin()
    @app_commands.describe(
        channel="要编辑的频道",
        new_name="新名称(可选)",
        slowmode="慢速模式(可选)",
        nsfw="是否NSFW(可选)",
        auto_archive="自动归档 (仅限论坛/子区)"
    )
    @app_commands.choices(
        slowmode=[
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

    # ---- 子区管理 ----
    thread_manage_group = app_commands.Group(name="子区管理", description="子区线程管理", parent=admin)

    @thread_manage_group.command(name="锁定", description="锁定线程")
    @is_admin()
    async def lock_thread_admin(self, interaction, thread: "discord.Thread"):
        await interaction.response.defer(ephemeral=True)
        if thread.locked:
            await interaction.followup.send("已锁定", ephemeral=True)
            return
        try:
            await thread.edit(locked=True, archived=False, reason=f"锁定 by {interaction.user}")
            await interaction.followup.send("✅ 已锁定线程", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 锁定失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="解锁", description="解锁线程")
    @is_admin()
    async def unlock_thread_admin(self, interaction, thread: "discord.Thread"):
        await interaction.response.defer(ephemeral=True)
        if not thread.locked:
            await interaction.followup.send("未锁定", ephemeral=True)
            return
        try:
            await thread.edit(locked=False, archived=False, reason=f"解锁 by {interaction.user}")
            await interaction.followup.send("✅ 已解锁线程", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 解锁失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="archive", description="归档线程")
    @is_admin()
    async def archive_thread_admin(self, interaction, thread: "discord.Thread"):
        await interaction.response.defer(ephemeral=True)
        if thread.archived:
            await interaction.followup.send("已归档", ephemeral=True)
            return
        try:
            await thread.edit(archived=True, reason=f"归档 by {interaction.user}")
            await interaction.followup.send("✅ 已归档线程", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 归档失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="unarchive", description="取消归档线程")
    @is_admin()
    async def unarchive_thread_admin(self, interaction, thread: "discord.Thread"):
        await interaction.response.defer(ephemeral=True)
        if not thread.archived:
            await interaction.followup.send("未归档", ephemeral=True)
            return
        try:
            await thread.edit(archived=False, locked=False, reason=f"取消归档 by {interaction.user}")
            await interaction.followup.send("✅ 已取消归档", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 取消归档失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="pin", description="置顶线程")
    @is_admin()
    async def pin_in_thread_admin(
        self,
        interaction,
        thread: "discord.Thread",
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            await thread.pin(reason=f"管理员置顶 by {interaction.user}")
            await interaction.followup.send("✅ 已置顶线程", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 置顶失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="unpin", description="取消置顶")
    @is_admin()
    async def unpin_in_thread_admin(
        self,
        interaction,
        thread: "discord.Thread"
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            await thread.unpin(reason=f"管理员取消置顶 by {interaction.user}")
            await interaction.followup.send("✅ 已取消置顶", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 取消置顶失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="删帖", description="删除线程")
    @is_admin()
    async def delete_thread_admin(self, interaction, thread: "discord.Thread"):
        await interaction.response.defer(ephemeral=True)
        try:
            await thread.delete(reason=f"管理员删帖 by {interaction.user}")
        except Exception as e:
            await interaction.followup.send(f"❌ 删除失败: {e}", ephemeral=True)

    # ---- 答题处罚 ----
    @app_commands.command(name="答题处罚", description="移除身份组送往答题区")
    @is_admin()
    @app_commands.describe(member="要处罚的成员", reason="原因（可选）")
    @app_commands.rename(member="成员", reason="原因")
    async def quiz_punish(self, interaction, member: "discord.Member", reason: str = None):
        await interaction.response.defer(ephemeral=True)
        # TODO: 移至独立配置文件
        role_id = int(self.config.get("quiz_role_id", 0))
        role = interaction.guild.get_role(role_id)
        if role is None:
            await interaction.followup.send("❌ 未找到答题区身份组", ephemeral=True)
            return
        try:
            if role in member.roles:
                await member.remove_roles(role, reason=f"答题处罚 by {interaction.user}")
                # 私聊通知
                try:    
                    await member.send(embed=discord.Embed(title="🔴 答题处罚", description=f"您因 {reason} 被移送答题区。请重新阅读规则并遵守。"))
                except discord.Forbidden:
                    pass
                await interaction.followup.send(f"✅ 已移除 {member.display_name} 的身份组并要求重新阅读规则", ephemeral=True)
                # 当前频道公示
                await interaction.followup.send(embed=discord.Embed(title="🔴 答题处罚", description=f"{member.mention} 因 {reason} 被移送答题区。请注意遵守社区规则。"), ephemeral=False)
            else:
                await interaction.followup.send("成员不包含该身份组", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限移除身份组", ephemeral=True)