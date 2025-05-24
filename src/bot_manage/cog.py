import asyncio
import discord
from discord.ext import commands
from discord import app_commands
import json
import uuid
import datetime
import pathlib
from typing import List, Tuple
import traceback
import discord.utils

from src.utils.confirm_view import confirm_view

class AdminCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger
        self.name = "管理命令"
        self.config = self.bot.config 

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
            
            main_guild_id = self.config.get('logging', {}).get('guild_id', 0)
            guild = self.bot.get_guild(int(main_guild_id)) # 确保它是一个整数

            if not guild: # 如果 guild 不存在，跳过
                if self.logger:
                    self.logger.warning("无法获取配置中的主服务器ID（logging.guild_id），跳过自动移除警告任务。请检查 config.json。")
                continue

            # 遍历警告文件，时间到则移除并删除文件
            warn_dir = pathlib.Path("data") / "warn" / str(guild.id)
            if warn_dir.exists():
                for file in warn_dir.glob("*.json"):
                    try:
                        with open(file, "r", encoding="utf-8") as f:
                            warn_record = json.load(f)
                            # 检查 'user_id' 是否存在且有效
                            if "user_id" not in warn_record:
                                if self.logger:
                                    self.logger.warning(f"警告记录文件 {file} 缺少 'user_id' 字段，跳过。")
                                continue
                            
                            user_id_to_remove = warn_record["user_id"]
                            member_to_remove = guild.get_member(user_id_to_remove)
                            
                            if warn_record.get("until", None) and datetime.datetime.now(datetime.timezone.utc) > datetime.datetime.fromisoformat(warn_record["until"]):
                                if member_to_remove: # 确保成员仍在服务器中
                                    warned_role_id = int(self.config.get("warned_role_id", 0))
                                    warned_role = guild.get_role(warned_role_id)
                                    if warned_role and warned_role in member_to_remove.roles:
                                        await member_to_remove.remove_roles(warned_role, reason=f"警告移除 by {self.bot.user}")
                                        if self.logger:
                                            self.logger.info(f"已移除成员 {member_to_remove.display_name} 的警告身份组。")
                                    else:
                                        if self.logger:
                                            self.logger.info(f"成员 {member_to_remove.display_name} 已无警告身份组或身份组配置错误，跳过移除。")
                                else:
                                    if self.logger:
                                        self.logger.info(f"被警告成员 {user_id_to_remove} 不在服务器中，直接删除警告记录。")
                                file.unlink(missing_ok=True)
                                if self.logger:
                                    self.logger.info(f"已删除警告记录文件: {file.name}")
                            else:
                                if self.logger:
                                    self.logger.debug(f"警告记录 {file.name} 仍在有效期内。")
                    except json.JSONDecodeError:
                        if self.logger:
                            self.logger.error(f"警告记录文件 {file} 格式错误，无法解析。")
                    except Exception as e:
                        if self.logger:
                            self.logger.error(f"处理警告记录文件 {file} 时发生未知错误: {e}")

    def is_admin():
        async def predicate(interaction: discord.Interaction): # predicate 接收 interaction
            config = interaction.client.config 
            admin_ids = config.get('admins', [])
            
            print(f"DEBUG (Admin Check): 用户 ID (int): {interaction.user.id}, 类型: {type(interaction.user.id)}")
            print(f"DEBUG (Admin Check): config.json 中的管理员 ID 列表 (list of str): {admin_ids}, 列表中元素类型: {type(admin_ids[0]) if admin_ids else 'N/A'}")
            
            if str(interaction.user.id) in admin_ids:
                print(f"DEBUG (Admin Check): 用户 {interaction.user.id} IS an admin. 检查通过。")
                return True
            else:
                print(f"DEBUG (Admin Check): 用户 {interaction.user.id} IS NOT an admin. 检查失败。")
                return False
        return app_commands.check(predicate) # 使用 app_commands.check

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
        interaction: discord.Interaction, # type: discord.Interaction
        member: "discord.Member",
        action: str,
        role: "discord.Role",
        reason: str = None,
    ):
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
        interaction: discord.Interaction,
        start_message: str,
        end_message: str,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = interaction.channel
        if channel is None:
            await interaction.followup.send("此命令只能在频道中使用", ephemeral=True)
            return
        # 尝试从消息链接中提取消息ID
        try:
            start_message_id = int(start_message.strip().split("/")[-1])
            end_message_id = int(end_message.strip().split("/")[-1])
        except ValueError:
            await interaction.followup.send("❌ 消息链接格式不正确，请提供有效的消息链接。", ephemeral=True)
            return

        try:
            start_message_obj = await channel.fetch_message(start_message_id)
            end_message_obj = await channel.fetch_message(end_message_id)
        except discord.NotFound:
            await interaction.followup.send("❌ 找不到指定的消息，请确认消息链接是否正确。", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ 获取消息失败: {e}", ephemeral=True)
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
                    f"确定要删除从 {start_message_obj.created_at.strftime('%Y-%m-%d %H:%M:%S')} 到 {end_message_obj.created_at.strftime('%Y-%m-%d %H:%M:%S')} 的消息吗？",
                ]
            ),
            colour=discord.Colour(0x808080),
            timeout=60,
        )

        if not confirmed:
            return

        deleted = 0
        # 一次100条，分批删除，从start_message开始，到end_message结束
        try:

            messages_to_delete = []
        # Discord API 的 history 方法在处理大量消息时可能存在性能考量，这里通过 after/before 确保范围。
        # 获取指定范围内的所有消息，如果消息量非常大，获取到内存可能消耗较多资源。
            async for msg in channel.history(
                limit=None, # 获取所有消息直到达到 after/before 限制
                after=start_message_obj.created_at - datetime.timedelta(seconds=1),# 确保包含开始消息
                before=end_message_obj.created_at + datetime.timedelta(seconds=1)# 确保包含结束消息
            ):
                if start_message_id <= msg.id <= end_message_id:
                    messages_to_delete.append(msg)
            
            messages_to_delete.sort(key=lambda m: m.created_at)

            for i in range(0, len(messages_to_delete), 100):
                chunk = messages_to_delete[i:i+100]
                # Discord API 限制每次批量删除最多100条消息，且有速率限制。
                await channel.delete_messages(chunk)
                deleted += len(chunk)
                await interaction.edit_original_response(content=f"已删除 {deleted} 条消息")
                await asyncio.sleep(1)# 增加延迟以避免触及 Discord API 速率限制
            
            await interaction.followup.send(f"✅ 已删除 {deleted} 条消息", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ 批量删除消息失败: {e}", ephemeral=True)


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
        interaction: discord.Interaction, 
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
                     # TODO: 在处理大量成员（例如，大型服务器）时，批量操作身份组可能触及 Discord API 速率限制。
                     # 建议在此处或外部循环中增加 asyncio.sleep 延迟，以更平滑地处理请求。
                    await member.add_roles(target_role, reason=f"批量转移身份组 by {interaction.user}")
                    if remove_source:
                        await member.remove_roles(source_role, reason=f"批量转移身份组 remove source by {interaction.user}")
                    affected += 1
                except discord.Forbidden:
                    self.logger.warning(f"无法对成员 {member.display_name} 批量转移身份组：权限不足")
                    continue
                except Exception as e:
                    self.logger.error(f"批量转移身份组时出错，成员 {member.display_name}: {e}")
                    continue
        await interaction.followup.send(f"✅ 已对 {affected} 名成员完成身份组转移", ephemeral=True)

    # ---- 禁言 ----
    @admin.command(name="禁言", description="将成员禁言（最长28天）并公示")
    @is_admin()
    @app_commands.describe(
        member="要禁言的成员",
        time="禁言时长（5m, 12h, 3d, 最大28天）",
        reason="原因（可选）",
        img="图片（可选）",
        warn="警告天数"
    )
    @app_commands.rename(member="成员", time="时长", reason="原因", img="图片", warn="警告天数")
    async def mute_member(
        self,
        interaction: discord.Interaction,  
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
            await interaction.response.send_message("❌ 未知或无效的时间格式。请使用例如 5m, 12h, 3d。", ephemeral=True)
            return
        
        duration = datetime.timedelta(seconds=mute_time)

        await interaction.response.defer(ephemeral=True)
        if duration.total_seconds() <= 0 and warn <= 0:
            await interaction.followup.send("❌ 时长和警告天数不能同时为0", ephemeral=True)
            return
        
        # 检查是否能对该成员执行操作
        if member.top_role.position >= interaction.guild.me.top_role.position:
            await interaction.followup.send("❌ 机器人权限不足，无法操作此成员。", ephemeral=True)
            return
        if member.id == interaction.user.id:
            await interaction.followup.send("❌ 你不能对自己执行禁言操作。", ephemeral=True)
            return
        if member.bot:
            await interaction.followup.send("❌ 你不能对机器人执行禁言操作。", ephemeral=True)
            return

        try:
            if duration.total_seconds() > 0:
                # Discord timeout 最大 28 天
                if duration.total_seconds() > 28 * 86400:
                    await interaction.followup.send("❌ 禁言时长不能超过28天。", ephemeral=True)
                    return
                await member.timeout(duration, reason=reason or "管理员禁言")
            
            warned_role = guild.get_role(int(self.config.get("warned_role_id", 0)))
            if warned_role and warn > 0:
                # 检查机器人是否有权限赋予这个角色
                if warned_role.position >= interaction.guild.me.top_role.position:
                    await interaction.followup.send(f"⚠️ 机器人权限不足，无法赋予警告身份组 {warned_role.mention}。", ephemeral=True)
                else:
                    await member.add_roles(warned_role, reason=f"处罚附加警告 {warn} 天")
                    self.logger.info(f"已赋予 {member.display_name} 警告身份组。")
            elif warn > 0:
                await interaction.followup.send("⚠️ 警告天数已设置，但 'warned_role_id' 未配置或无效，无法赋予警告身份组。", ephemeral=True)

        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限对该成员执行禁言", ephemeral=True)
            return
        except Exception as e:
            self.logger.error(f"执行禁言时出错: {traceback.format_exc()}")
            await interaction.followup.send(f"❌ 禁言操作失败: {str(e)}", ephemeral=True)
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

        await interaction.followup.send(f"✅ 已禁言 {member.mention} ({mute_time_str})。处罚ID: `{record_id}`", ephemeral=True)

        # 私聊通知
        try:
            embed_title = ""
            embed_description = ""
            if duration.total_seconds() > 0:
                embed_title = "🔇 禁言处罚"
                embed_description = f"您因 {reason or '未提供原因'} 被禁言 {mute_time_str}。请注意遵守社区规则。"
            if warn > 0:
                if embed_title: embed_title += " & "
                embed_title += "⚠️ 警告处罚"
                embed_description += f"\n您因 {reason or '未提供原因'} 被警告 {warn} 天。"
            
            if embed_title:
                await member.send(embed=discord.Embed(title=embed_title, description=embed_description))
        except discord.Forbidden:
            self.logger.warning(f"无法私聊通知 {member.display_name} 禁言/警告处罚，可能TA关闭了私信。")
            pass

        # 公示频道
        channel_id = int(self.config.get("punish_announce_channel_id", 0))
        announce_channel = guild.get_channel(channel_id)
        if announce_channel:
            embed = discord.Embed(title="处罚公告", color=discord.Color.orange())
            if duration.total_seconds() > 0:
                embed.add_field(name="类型", value="禁言", inline=True)
                embed.add_field(name="时长", value=mute_time_str, inline=True)
            else:
                embed.add_field(name="类型", value="警告", inline=True)
            
            embed.add_field(name="成员", value=f"{member.mention} ({member.id})", inline=False)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="原因", value=reason or "未提供", inline=False)
            if warn > 0:
                embed.add_field(name="警告天数", value=f"{warn}天", inline=False)
            if img:
                embed.set_image(url=img.url)
            embed.set_footer(text=f"处罚ID: {record_id} | 操作者: {interaction.user.display_name}")
            try:
                await announce_channel.send(embed=embed)
            except discord.Forbidden:
                self.logger.error(f"无法在公告频道 {announce_channel.name} 发送处罚公告，权限不足。")
            except Exception as e:
                self.logger.error(f"发送处罚公告时出错: {e}")
        else:
            self.logger.warning("未配置处罚公告频道或频道ID无效。")


    # ---- 永封 ----
    @admin.command(name="永封", description="永久封禁成员并公示")
    @is_admin()
    @app_commands.describe(member="要封禁的成员", reason="原因（可选）", img="图片（可选）", delete_message_days="删除消息天数（0-7）")
    @app_commands.rename(member="成员", reason="原因", img="图片", delete_message_days="删除消息天数")
    async def ban_member(
        self,
        interaction: discord.Interaction,
        member: "discord.Member",
        reason: str = None,
        img: discord.Attachment = None,
        delete_message_days: int = 0, # 删除消息天数，Discord API 限制为 0-7 天
    ):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("此命令只能在服务器中使用", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # 检查是否能对该成员执行操作
        if member.top_role.position >= interaction.guild.me.top_role.position:
            await interaction.followup.send("❌ 机器人权限不足，无法操作此成员。", ephemeral=True)
            return
        if member.id == interaction.user.id:
            await interaction.followup.send("❌ 你不能对自己执行封禁操作。", ephemeral=True)
            return
        if member.bot:
            await interaction.followup.send("❌ 你不能对机器人执行封禁操作。", ephemeral=True)
            return

        # 私聊通知
        try:
            await member.send(embed=discord.Embed(title="⛔ 永久封禁", description=f"您因 {reason or '未提供原因'} 被永久封禁。如有异议，请联系管理组成员。"))
        except discord.Forbidden:
            self.logger.warning(f"无法私聊通知 {member.display_name} 永久封禁处罚，可能TA关闭了私信。")
            pass
        except Exception as e:
            self.logger.error(f"私聊通知时出错: {e}")
            pass

        try:
            # delete_message_days 参数范围为 0 到 7 天。
            await guild.ban(member, reason=reason, delete_message_days=delete_message_days)
        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限封禁该成员", ephemeral=True)
            return
        except Exception as e:
            self.logger.error(f"执行封禁时出错: {traceback.format_exc()}")
            await interaction.followup.send(f"❌ 封禁操作失败: {str(e)}", ephemeral=True)
            return

        # 保存记录 & 公示
        record_id = self._save_punish_record(guild.id, {
            "type": "ban",
            "user_id": member.id,
            "moderator_id": interaction.user.id,
            "reason": reason,
            "delete_message_days": delete_message_days
        })

        await interaction.followup.send(f"✅ 已永久封禁 {member.name}。处罚ID: `{record_id}`", ephemeral=True)

        # 公示频道
        channel_id = int(self.config.get("punish_announce_channel_id", 0))
        announce_channel = guild.get_channel(channel_id)
        if announce_channel:
            embed = discord.Embed(title="⛔ 永久封禁公告", color=discord.Color.red())
            embed.add_field(name="类型", value="永久封禁", inline=True)
            embed.add_field(name="成员", value=f"{member.mention} ({member.id})", inline=False)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="原因", value=reason or "未提供", inline=False)
            if delete_message_days > 0:
                embed.add_field(name="删除消息", value=f"最近 {delete_message_days} 天的消息", inline=False)
            if img:
                embed.set_image(url=img.url)
            embed.set_footer(text=f"处罚ID: {record_id} | 操作者: {interaction.user.display_name}")
            try:
                await announce_channel.send(embed=embed)
            except discord.Forbidden:
                self.logger.error(f"无法在公告频道 {announce_channel.name} 发送封禁公告，权限不足。")
            except Exception as e:
                self.logger.error(f"发送封禁公告时出错: {e}")
        else:
            self.logger.warning("未配置处罚公告频道或频道ID无效。")


    # ---- 撤销处罚 ----
    @admin.command(name="撤销处罚", description="按ID撤销处罚")
    @is_admin()
    @app_commands.describe(punish_id="处罚ID", reason="原因（可选）")
    async def revoke_punish(self, interaction: discord.Interaction, punish_id: str, reason: str = None):
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
        # 尝试获取成员，如果不在服务器中，fetch_member 可能会抛出 NotFound
        user_obj = guild.get_member(user_id)
        if user_obj is None:
            try:
                user_obj = await guild.fetch_member(user_id)
            except discord.NotFound:
                await interaction.followup.send("❌ 目标用户不在当前服务器中，无法撤销其处罚。", ephemeral=True)
                # 即使用户不在服务器，如果记录文件存在，仍可以删除
                try:
                    path.unlink(missing_ok=True)
                    await interaction.followup.send(f"✅ 已删除处罚记录 {punish_id} (用户不在服务器中)。", ephemeral=True)
                except Exception as e:
                    self.logger.error(f"删除处罚记录文件 {path} 失败: {e}")
                    await interaction.followup.send(f"⚠️ 无法删除处罚记录文件 {path}，但用户不在服务器中。", ephemeral=True)
                return
            except Exception as e:
                await interaction.followup.send(f"❌ 获取目标用户失败: {e}", ephemeral=True)
                return

        if record["type"] == "mute":
            try:
                is_timed_out = (
                    getattr(user_obj, 'communication_disabled_until', None) and 
                    user_obj.communication_disabled_until > discord.utils.utcnow()
                )
                
                if is_timed_out: # 检查是否处于禁言状态
                    await user_obj.timeout(None, reason=reason or "撤销处罚")
                    self.logger.info(f"已解除 {user_obj.display_name} 的禁言。")
                else:
                    self.logger.info(f"{user_obj.display_name} 未处于禁言状态，跳过解除。")
                
                if record.get("warn", 0) > 0:
                    warned_role = guild.get_role(int(self.config.get("warned_role_id", 0)))
                    if warned_role and warned_role in user_obj.roles:
                        await user_obj.remove_roles(warned_role, reason=f"撤销处罚附加警告 {record['warn']} 天")
                        self.logger.info(f"已移除 {user_obj.display_name} 的警告身份组。")
                    else:
                        self.logger.info(f"{user_obj.display_name} 未拥有警告身份组或身份组配置错误，跳过移除。")
            except discord.Forbidden:
                await interaction.followup.send("❌ 无权限解除禁言或移除身份组", ephemeral=True)
                return
            except Exception as e:
                self.logger.error(f"撤销禁言时出错: {traceback.format_exc()}")
                await interaction.followup.send(f"❌ 撤销禁言失败: {str(e)}", ephemeral=True)
                return

        elif record["type"] == "ban":
            try:
                # guild.unban 接收用户对象或用户ID
                await guild.unban(discord.Object(id=user_id), reason=reason or "撤销处罚")
                self.logger.info(f"已解除 {user_obj.display_name} 的封禁。")
            except discord.NotFound:
                await interaction.followup.send("❌ 目标用户未被封禁，无法撤销封禁。", ephemeral=True)
                # 即使未被封禁，如果记录文件存在，仍可以删除
                try:
                    path.unlink(missing_ok=True)
                    await interaction.followup.send(f"✅ 已删除处罚记录 {punish_id} (用户未被封禁)。", ephemeral=True)
                except Exception as e:
                    self.logger.error(f"删除处罚记录文件 {path} 失败: {e}")
                    await interaction.followup.send(f"⚠️ 无法删除处罚记录文件 {path}，但用户未被封禁。", ephemeral=True)
                return
            except discord.Forbidden:
                await interaction.followup.send("❌ 无权限解除封禁", ephemeral=True)
                return
            except Exception as e:
                self.logger.error(f"撤销封禁时出错: {traceback.format_exc()}")
                await interaction.followup.send(f"❌ 撤销封禁失败: {str(e)}", ephemeral=True)
                return
        else:
            await interaction.followup.send("❌ 未知处罚类型，无法撤销。", ephemeral=True)
            return

        # 删除记录文件
        try:
            path.unlink(missing_ok=True)
            self.logger.info(f"已删除处罚记录文件: {path.name}")
        except Exception as e:
            self.logger.error(f"删除处罚记录文件 {path} 失败: {e}")
            await interaction.followup.send(f"⚠️ 无法删除处罚记录文件 {path}。", ephemeral=True)
            pass

        # 公示
        channel_id = int(self.config.get("punish_announce_channel_id", 0))
        announce_channel = guild.get_channel(channel_id)
        if announce_channel:
            embed = discord.Embed(title="🔓 撤销处罚公告", color=discord.Color.green())
            embed.add_field(name="处罚ID", value=punish_id, inline=True)
            embed.add_field(name="类型", value=record["type"].capitalize(), inline=True)
            embed.add_field(name="成员", value=f"{user_obj.mention} ({user_obj.id})", inline=False)
            embed.add_field(name="撤销原因", value=reason or "未提供", inline=False)
            embed.set_footer(text=f"操作者: {interaction.user.display_name}")
            try:
                await announce_channel.send(embed=embed)
            except discord.Forbidden:
                self.logger.error(f"无法在公告频道 {announce_channel.name} 发送撤销处罚公告，权限不足。")
            except Exception as e:
                self.logger.error(f"发送撤销处罚公告时出错: {e}")
        else:
            self.logger.warning("未配置处罚公告频道或频道ID无效。")

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
            app_commands.Choice(name="无", value=0),
            app_commands.Choice(name="5秒", value=5),
            app_commands.Choice(name="10秒", value=10),
            app_commands.Choice(name="15秒", value=15),
            app_commands.Choice(name="30秒", value=30),
            app_commands.Choice(name="1分钟", value=60),
            app_commands.Choice(name="5分钟", value=300), # 新增
            app_commands.Choice(name="10分钟", value=600), # 新增
            app_commands.Choice(name="15分钟", value=900), # 新增
            app_commands.Choice(name="30分钟", value=1800), # 新增
            app_commands.Choice(name="1小时", value=3600), # 新增
            app_commands.Choice(name="2小时", value=7200), # 新增
            app_commands.Choice(name="6小时", value=21600), # 新增
        ],
        auto_archive=[
            app_commands.Choice(name="1小时", value=60), 
            app_commands.Choice(name="24小时", value=1440),
            app_commands.Choice(name="3天", value=4320),
            app_commands.Choice(name="1周", value=10080),
        ]
    )
    async def manage_channel(
        self,
        interaction: discord.Interaction, 
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
            params["slowmode_delay"] = slowmode.value
        if nsfw is not None:
            params["nsfw"] = nsfw
        
        if auto_archive is not None and isinstance(channel, (discord.ForumChannel, discord.TextChannel)):
            params["auto_archive_duration"] = auto_archive.value 
        elif auto_archive is not None:
            await interaction.followup.send("⚠️ 自动归档设置仅适用于支持线程的频道 (如文本频道或论坛频道)。", ephemeral=True)


        if not params:
            await interaction.followup.send("❌ 未提供任何修改参数", ephemeral=True)
            return
        try:
            await channel.edit(**params, reason=f"频道管理 by {interaction.user}")
            await interaction.followup.send("✅ 频道已更新", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ 无修改权限，请检查机器人角色权限。", ephemeral=True)
        except Exception as e:
            self.logger.error(f"频道管理时出错: {traceback.format_exc()}")
            await interaction.followup.send(f"❌ 频道更新失败: {str(e)}", ephemeral=True)


    # ---- 子区管理 ----
    thread_manage_group = app_commands.Group(name="子区管理", description="子区线程管理", parent=admin)

    @thread_manage_group.command(name="锁定", description="锁定线程")
    @is_admin()
    async def lock_thread_admin(self, interaction: discord.Interaction, thread: "discord.Thread"):
        await interaction.response.defer(ephemeral=True)
        if thread.locked:
            await interaction.followup.send("已锁定", ephemeral=True)
            return
        try:
            await thread.edit(locked=True, archived=False, reason=f"锁定 by {interaction.user}")
            await interaction.followup.send("✅ 已锁定线程", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限锁定该线程。", ephemeral=True)
        except Exception as e:
            self.logger.error(f"锁定线程时出错: {traceback.format_exc()}")
            await interaction.followup.send(f"❌ 锁定失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="解锁", description="解锁线程")
    @is_admin()
    async def unlock_thread_admin(self, interaction: discord.Interaction, thread: "discord.Thread"):
        await interaction.response.defer(ephemeral=True)
        if not thread.locked:
            await interaction.followup.send("未锁定", ephemeral=True)
            return
        try:
            await thread.edit(locked=False, archived=False, reason=f"解锁 by {interaction.user}")
            await interaction.followup.send("✅ 已解锁线程", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限解锁该线程。", ephemeral=True)
        except Exception as e:
            self.logger.error(f"解锁线程时出错: {traceback.format_exc()}")
            await interaction.followup.send(f"❌ 解锁失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="archive", description="归档线程")
    @is_admin()
    async def archive_thread_admin(self, interaction: discord.Interaction, thread: "discord.Thread"):
        await interaction.response.defer(ephemeral=True)
        if thread.archived:
            await interaction.followup.send("已归档", ephemeral=True)
            return
        try:
            await thread.edit(archived=True, reason=f"归档 by {interaction.user}")
            await interaction.followup.send("✅ 已归档线程", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限归档该线程。", ephemeral=True)
        except Exception as e:
            self.logger.error(f"归档线程时出错: {traceback.format_exc()}")
            await interaction.followup.send(f"❌ 归档失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="unarchive", description="取消归档线程")
    @is_admin()
    async def unarchive_thread_admin(self, interaction: discord.Interaction, thread: "discord.Thread"):
        await interaction.response.defer(ephemeral=True)
        if not thread.archived:
            await interaction.followup.send("未归档", ephemeral=True)
            return
        try:
            await thread.edit(archived=False, locked=False, reason=f"取消归档 by {interaction.user}")
            await interaction.followup.send("✅ 已取消归档", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限取消归档该线程。", ephemeral=True)
        except Exception as e:
            self.logger.error(f"取消归档线程时出错: {traceback.format_exc()}")
            await interaction.followup.send(f"❌ 取消归档失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="pin", description="置顶线程")
    @is_admin()
    async def pin_in_thread_admin(
        self,
        interaction: discord.Interaction,
        thread: "discord.Thread",
    ):
        await interaction.response.defer(ephemeral=True)
        # 检查线程是否支持置顶消息 (只有部分线程类型支持)
        if not thread.parent.permissions_for(interaction.guild.me).manage_messages:
            await interaction.followup.send("❌ 机器人在此父频道无 '管理消息' 权限，无法置顶。", ephemeral=True)
            return

        try:
            first_message = None
            async for msg in thread.history(oldest_first=True, limit=1):
                first_message = msg
            
            if first_message:
                if first_message.pinned:
                    await interaction.followup.send("该线程的首条消息已置顶。", ephemeral=True)
                    return
                await first_message.pin(reason=f"管理员置顶 by {interaction.user}")
                await interaction.followup.send("✅ 已置顶线程的首条消息", ephemeral=True)
            else:
                await interaction.followup.send("❌ 无法找到线程的首条消息进行置顶。", ephemeral=True)

        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限置顶该线程中的消息。", ephemeral=True)
        except Exception as e:
            self.logger.error(f"置顶线程时出错: {traceback.format_exc()}")
            await interaction.followup.send(f"❌ 置顶失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="unpin", description="取消置顶")
    @is_admin()
    async def unpin_in_thread_admin(
        self,
        interaction: discord.Interaction,
        thread: "discord.Thread"
    ):
        await interaction.response.defer(ephemeral=True)
        if not thread.parent.permissions_for(interaction.guild.me).manage_messages:
            await interaction.followup.send("❌ 机器人在此父频道无 '管理消息' 权限，无法取消置顶。", ephemeral=True)
            return

        try:
            pinned_messages = await thread.pins()
            first_pinned_message = next((msg for msg in pinned_messages if msg.channel.id == thread.id), None)
            
            if first_pinned_message:
                await first_pinned_message.unpin(reason=f"管理员取消置顶 by {interaction.user}")
                await interaction.followup.send("✅ 已取消置顶线程的首条消息", ephemeral=True)
            else:
                await interaction.followup.send("该线程中没有置顶消息。", ephemeral=True)

        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限取消置顶该线程中的消息。", ephemeral=True)
        except Exception as e:
            self.logger.error(f"取消置顶线程时出错: {traceback.format_exc()}")
            await interaction.followup.send(f"❌ 取消置顶失败: {e}", ephemeral=True)

    @thread_manage_group.command(name="删帖", description="删除线程")
    @is_admin()
    async def delete_thread_admin(self, interaction: discord.Interaction, thread: "discord.Thread"):
        await interaction.response.defer(ephemeral=True)
        try:
            # 确认删除
            confirmed = await confirm_view(
                interaction,
                title="删除子区",
                description=f"⚠️ **危险操作** ⚠️\n\n确定要删除子区 **{thread.name}** 吗？\n\n**此操作不可逆，将删除所有消息和历史记录！**",
                colour=discord.Colour.red(),
                timeout=30
            )

            if not confirmed:
                await interaction.delete_original_response() # Remove the confirmation message
                return
            
            await thread.delete(reason=f"管理员删帖 by {interaction.user}")
            await interaction.followup.send(f"✅ 已删除线程: {thread.name}", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限删除该线程。", ephemeral=True)
        except Exception as e:
            self.logger.error(f"删除线程时出错: {traceback.format_exc()}")
            await interaction.followup.send(f"❌ 删除失败: {e}", ephemeral=True)

    # ---- 答题处罚 ----
    @app_commands.command(name="答题处罚", description="移除身份组送往答题区")
    @is_admin()
    @app_commands.describe(member="要处罚的成员", reason="原因（可选）")
    @app_commands.rename(member="成员", reason="原因")
    async def quiz_punish(self, interaction: discord.Interaction, member: "discord.Member", reason: str = None):
        await interaction.response.defer(ephemeral=True)
        # 从 self.config 获取 quiz_role_id
        role_id = int(self.config.get("quiz_role_id", 0))
        role = interaction.guild.get_role(role_id)
        if role is None:
            await interaction.followup.send("❌ 未找到答题区身份组，请检查 'quiz_role_id' 配置。", ephemeral=True)
            return
        
        # 检查是否能对该成员执行操作
        if member.top_role.position >= interaction.guild.me.top_role.position:
            await interaction.followup.send("❌ 机器人权限不足，无法操作此成员。", ephemeral=True)
            return
        if member.id == interaction.user.id:
            await interaction.followup.send("❌ 你不能对自己执行答题处罚操作。", ephemeral=True)
            return
        if member.bot:
            await interaction.followup.send("❌ 你不能对机器人执行答题处罚操作。", ephemeral=True)
            return

        try:
            if role in member.roles:
                await member.remove_roles(role, reason=f"答题处罚 by {interaction.user} - {reason or '未提供原因'}")
                # 私聊通知
                try:    
                    await member.send(embed=discord.Embed(title="🔴 答题处罚", description=f"您因 {reason or '未提供原因'} 被移送答题区。请重新阅读规则并遵守。"))
                except discord.Forbidden:
                    self.logger.warning(f"无法私聊通知 {member.display_name} 答题处罚，可能TA关闭了私信。")
                    pass
                await interaction.followup.send(f"✅ 已移除 {member.display_name} 的身份组并要求重新阅读规则", ephemeral=True)
                
                # 公示频道 (如果需要，可以添加一个处罚公告)
                channel_id = int(self.config.get("punish_announce_channel_id", 0))
                announce_channel = interaction.guild.get_channel(channel_id)
                if announce_channel:
                    embed = discord.Embed(title="🔴 答题处罚公告", color=discord.Color.red())
                    embed.add_field(name="类型", value="答题处罚", inline=True)
                    embed.add_field(name="成员", value=f"{member.mention} ({member.id})", inline=False)
                    embed.set_thumbnail(url=member.display_avatar.url)
                    embed.add_field(name="原因", value=reason or "未提供", inline=False)
                    embed.set_footer(text=f"操作者: {interaction.user.display_name}")
                    try:
                        await announce_channel.send(embed=embed)
                    except discord.Forbidden:
                        self.logger.error(f"无法在公告频道 {announce_channel.name} 发送答题处罚公告，权限不足。")
                    except Exception as e:
                        self.logger.error(f"发送答题处罚公告时出错: {e}")
                else:
                    self.logger.warning("未配置处罚公告频道或频道ID无效。")

            else:
                await interaction.followup.send("成员不包含该答题身份组，无需移除。", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ 无权限移除身份组，请检查机器人角色权限。", ephemeral=True)
        except Exception as e:
            self.logger.error(f"执行答题处罚时出错: {traceback.format_exc()}")
            await interaction.followup.send(f"❌ 答题处罚失败: {str(e)}", ephemeral=True)


# 每个 Cog 模块都需要一个 setup 函数，供 discord.py 加载扩展时调用
async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCommands(bot))
