import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
from pathlib import Path
import datetime
import uuid

import logging

module_logger = logging.getLogger(__name__)

# --- 配置与常量 ---
CONFIG_FILE = 'config.json'
VOTE_DATA_DIR = Path("data") / "votes"
VOTE_DATA_DIR.mkdir(parents=True, exist_ok=True)  # 确保目录存在


# 辅助函数，用于加载主机器人配置
def load_bot_config():
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        module_logger.error(f"为 投票模块 加载 {CONFIG_FILE} 时出错: {e}")
        return {}


BOT_CONFIG = load_bot_config()  # 全局加载一次配置


# --- 投票数据管理 ---

def save_vote_data(message_id: int, data: dict):
    """保存投票数据到 JSON 文件"""
    filepath = VOTE_DATA_DIR / f"{message_id}.json"
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        module_logger.error(f"保存投票数据 {message_id} (UUID: {data.get('uuid', 'N/A')}) 时出错: {e}")  # 添加UUID到日志
        raise


def load_vote_data(message_id: int) -> dict | None:
    """从 JSON 文件加载投票数据"""
    filepath = VOTE_DATA_DIR / f"{message_id}.json"
    if filepath.exists():
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                # 旧数据可能没有uuid, 确保返回的字典有此键，即使是None
                data.setdefault('uuid', None)
                return data
        except Exception as e:
            module_logger.error(f"加载投票数据 {message_id} 时出错: {e}")
            return None
    return None


def delete_vote_data(message_id: int):
    """删除投票数据的 JSON 文件"""
    # 在删除前可以先加载一下，获取UUID用于日志
    vote_data = load_vote_data(message_id)
    uuid_val = vote_data.get('uuid', 'N/A') if vote_data else 'N/A'

    filepath = VOTE_DATA_DIR / f"{message_id}.json"
    if filepath.exists():
        try:
            filepath.unlink(missing_ok=True)
            module_logger.info(f"已删除投票数据文件: {filepath} (UUID: {uuid_val})")
        except Exception as e:
            module_logger.error(f"删除投票数据 {message_id} (UUID: {uuid_val}) 时出错: {e}")
            raise


# --- 用于投票的持久化视图 ---
class VoteButton(discord.ui.Button):
    """投票按钮类"""

    def __init__(self, team_id: str, team_name: str, style: discord.ButtonStyle, cog_logger: logging.Logger,vote_initiator_role_id: int ):
        super().__init__(
            label=f"支持{team_name}",
            style=style,
            custom_id=f"vote_button_persistent_{team_id}",


        )
        self.team_id = team_id
        self.team_name = team_name
        self.logger = cog_logger
        self.vote_initiator_role_id = vote_initiator_role_id

    async def callback(self, interaction: discord.Interaction):
        """按钮点击回调"""
        initiator_role = interaction.guild.get_role(self.vote_initiator_role_id)
        if not initiator_role in interaction.user.roles and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 你没有权限投票 (需要议员身份组或管理员权限)。",
                                                    ephemeral=True)
            return


        await interaction.response.defer(ephemeral=True, thinking=True)

        vote_data = load_vote_data(interaction.message.id)
        if not vote_data or not vote_data.get("active", False):
            await interaction.followup.send("真该死，这个投票已结束或数据没了", ephemeral=True)
            view = discord.ui.View.from_message(interaction.message)
            if view:
                for item in view.children:
                    if isinstance(item, discord.ui.Button):
                        item.disabled = True
                try:
                    await interaction.message.edit(view=view)
                except discord.HTTPException:
                    self.logger.warning(f"尝试禁用旧投票 {interaction.message.id} 的按钮时失败。")
            return

        user_id_str = str(interaction.user.id)
        current_team_votes_key = f"{self.team_id}_votes_users"
        current_team_count_key = f"{self.team_id}_count"
        other_team_id = "blue" if self.team_id == "red" else "red"
        other_team_votes_key = f"{other_team_id}_votes_users"
        other_team_count_key = f"{other_team_id}_count"
        response_message = ""

        if user_id_str in vote_data[other_team_votes_key]:
            vote_data[other_team_votes_key].remove(user_id_str)
            vote_data[other_team_count_key] = max(0, vote_data[other_team_count_key] - 1)

        if user_id_str in vote_data[current_team_votes_key]:
            vote_data[current_team_votes_key].remove(user_id_str)
            vote_data[current_team_count_key] = max(0, vote_data[current_team_count_key] - 1)
            response_message = f"您已取消对{self.team_name}的支持。"
        else:
            vote_data[current_team_votes_key].append(user_id_str)
            vote_data[current_team_count_key] += 1
            response_message = f"您已成功投票支持{self.team_name}！"

        try:
            save_vote_data(interaction.message.id, vote_data)
        except Exception as e:
            await interaction.followup.send("处理您的投票时发生内部错误，请稍后再试。", ephemeral=True)
            return

        try:
            embed = interaction.message.embeds[0]
            embed.set_field_at(0, name=f"🔴 红方支持: {vote_data['red_count']}（通过提案/上诉）", value="\u200b",
                               inline=True)
            embed.set_field_at(1, name=f"🔵 蓝方支持: {vote_data['blue_count']}（驳回提案/上诉）", value="\u200b",
                               inline=True)
            embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
            await interaction.message.edit(embed=embed)
        except discord.HTTPException as e:
            self.logger.error(f"编辑投票消息 {interaction.message.id} (UUID: {vote_data.get('uuid', 'N/A')}) 失败: {e}")
        except IndexError:
            self.logger.error(
                f"投票消息 {interaction.message.id} (UUID: {vote_data.get('uuid', 'N/A')}) 的 Embed 结构异常。")

        await interaction.followup.send(response_message, ephemeral=True)


class DebateVoteView(discord.ui.View):
    """辩论投票视图类"""

    def __init__(self, cog_logger: logging.Logger,vote_initiator_role_id: int ):
        super().__init__(timeout=None)  # 持久化视图
        self.add_item(
            VoteButton(team_id="red", team_name="红方", vote_initiator_role_id=vote_initiator_role_id,style=discord.ButtonStyle.danger, cog_logger=cog_logger))
        self.add_item(
            VoteButton(team_id="blue", team_name="蓝方",vote_initiator_role_id=vote_initiator_role_id, style=discord.ButtonStyle.primary, cog_logger=cog_logger))


class VotingManageCommands(commands.Cog):
    """投票辩诉功能的 Cog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = bot.logger
        self.name = "投票辩诉命令"

        self.vote_initiator_role_id = None
        raw_initiator_id = BOT_CONFIG.get("vote_role_id")
        if raw_initiator_id:
            try:
                self.vote_initiator_role_id = int(raw_initiator_id)
            except ValueError:
                self.logger.warning(
                    f"投票模块: config.json 中的 'vote_role_id' ('{raw_initiator_id}') 不是一个有效的整数。角色检查可能会失败。"
                )
        if not self.vote_initiator_role_id:
            self.logger.warning(
                "投票模块: 未在 config.json 中找到 'vote_role_id'。默认不允许非管理员发起/进行投票。"
            )

        self.voting_channel_id = None
        raw_channel_id = BOT_CONFIG.get("voting_channel_id")
        if raw_channel_id:
            try:
                self.voting_channel_id = int(raw_channel_id)
            except ValueError:
                self.logger.error(
                    f"投票模块: config.json 中的 'voting_channel_id' ('{raw_channel_id}') 不是一个有效的整数。投票将无法发送到指定频道。"
                )
        if not self.voting_channel_id:
            self.logger.error(
                "投票模块: 未在 config.json 中找到 'voting_channel_id'。投票功能可能无法正常工作。"
            )

        self._timed_task_started = False

    def cog_unload(self):
        """Cog 卸载时调用，取消定时任务"""
        self.check_timed_votes.cancel()

    def is_vote_initiator(self, user: discord.Member | discord.User) -> bool:
        if not isinstance(user, discord.Member):  
            return False
        if not self.vote_initiator_role_id:
            self.logger.debug("投票模块: 未配置投票发起人角色 (vote_role_id)，默认不允许非管理员发起/进行投票。")
            return False

        initiator_role = user.guild.get_role(self.vote_initiator_role_id)
        if not initiator_role:
            self.logger.error(
                f"投票模块: 在服务器 {user.guild.id} 中未找到配置的发起人角色 ID {self.vote_initiator_role_id}。")
            return False
        return initiator_role in user.roles

    @commands.Cog.listener()
    async def on_ready(self):
        """Cog 准备就绪时调用"""
        if not hasattr(self.bot, '_vote_view_added_flag'):
            self.bot.add_view(DebateVoteView(cog_logger=self.logger,vote_initiator_role_id=self.vote_initiator_role_id))
            self.bot._vote_view_added_flag = True
            self.logger.info(f"{self.name} cog 的持久化 DebateVoteView 已注册到机器人。")

        if not self._timed_task_started:
            if not self.check_timed_votes.is_running():
                try:
                    self.check_timed_votes.start()
                    self._timed_task_started = True
                    self.logger.info(f"{self.name}: 定时投票检查任务已启动。")
                except RuntimeError as e:
                    self.logger.error(f"{self.name}: 尝试启动定时投票检查任务失败: {e}")
            else:
                self._timed_task_started = True
                self.logger.info(f"{self.name}: 定时投票检查任务已经启动。")
        self.logger.info(f"{self.name} cog 已准备就绪。")

    vote_admin = app_commands.Group(name="vote", description="投票辩诉相关命令")

    @vote_admin.command(name="start", description="发起一个投票辩诉")
    @app_commands.describe(
        topic="投票主题",
        description="对主题的简要描述 (可选)",
        duration_hours="投票持续小时数 (例如: 24, 0或不填为无限期)",
        thread_slowmode_seconds="子区消息发送冷却时间(秒, 0为无限制, 最大21600)",
        thread_restricted_role="限定可以参与子区讨论的身份组 (可选)"
    )
    async def start_vote(self, interaction: discord.Interaction,
                         topic: str,
                         description: str = None,
                         duration_hours: float = 0.0,
                         thread_slowmode_seconds: app_commands.Range[int, 0, 21600] = 0,
                         thread_restricted_role: discord.Role = None):
        """发起投票的命令"""
        if not self.is_vote_initiator(
                interaction.user) and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 你没有权限发起投票 (需要议员身份组或管理员权限)。",
                                                    ephemeral=True)
            return

        if not self.voting_channel_id:
            self.logger.error("投票模块: 未配置投票频道 (voting_channel_id)，无法发起投票。")
            await interaction.response.send_message("❌ 投票系统未正确配置投票频道，请联系管理员。", ephemeral=True)
            return

        voting_channel = self.bot.get_channel(self.voting_channel_id)
        if not voting_channel:
            voting_channel = await self.bot.fetch_channel(self.voting_channel_id)  # 尝试fetch
            if not voting_channel:
                self.logger.error(f"投票模块: 未找到配置的投票频道 ID: {self.voting_channel_id}。")
                await interaction.response.send_message(
                    f"❌ 未找到指定的投票频道 (ID: {self.voting_channel_id})，请联系管理员。", ephemeral=True)
                return
        if not isinstance(voting_channel, discord.TextChannel):
            self.logger.error(f"投票模块: 配置的投票频道 ID: {self.voting_channel_id} 不是一个文本频道。")
            await interaction.response.send_message(f"❌ 配置的投票频道不是一个有效的文本频道，请联系管理员。",
                                                    ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)

        vote_uuid = str(uuid.uuid4())  # <--- 生成UUID

        embed = discord.Embed(title=f"🗳️ 投票辩诉: {topic}", color=discord.Color.gold())
        if description:
            embed.description = description
        embed.add_field(name="🔴 红方支持: 0（通过提案/上诉）", value="\u200b", inline=True)
        embed.add_field(name="🔵 蓝方支持: 0（驳回提案/上诉）", value="\u200b", inline=True)

        footer_text = f"投票发起人: {interaction.user.display_name} ({interaction.user.id}) | UUID: {vote_uuid}"
        end_time = None
        if duration_hours and duration_hours > 0:
            end_time_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=duration_hours)
            end_time = end_time_dt.isoformat()
            footer_text += f" | 结束于: <t:{int(end_time_dt.timestamp())}:R>"
        else:
            footer_text += " | 投票无固定结束时间"
        if thread_restricted_role:
            footer_text += f"| 此投票讨论区为私有子区，仅供{thread_restricted_role.mention}讨论"

        embed.set_footer(text=footer_text)
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
        vote_view = DebateVoteView(cog_logger=self.logger,vote_initiator_role_id=self.vote_initiator_role_id)

        try:
            vote_message = await voting_channel.send(embed=embed, view=vote_view)
        except discord.Forbidden:
            self.logger.error(f"投票发起失败: 机器人无权限在指定投票频道 {self.voting_channel_id} 发送消息。")
            await interaction.followup.send(f"❌ 机器人没有权限在投票频道发送消息，请联系管理员。", ephemeral=True)
            return
        except Exception as e:
            self.logger.error(f"在投票频道 {self.voting_channel_id} 创建投票时发生未知错误: {e}", exc_info=True)
            await interaction.followup.send(f"❌ 创建投票时发生未知错误: {e}", ephemeral=True)
            return
        # --- 子区创建和配置 ---
        created_thread = None
        thread_creation_log = ""
        is_private_thread_flag = False  # 用于保存到 vote_data

        try:
            thread_name = f"汴京大战区 - {topic[:80]}"
            thread_welcome_message_content = f"🗳️ 这是关于投票 **'{topic}'** 的专属汴京大战区。\n"

            if thread_restricted_role:

                is_private_thread_flag = True

                created_thread = await voting_channel.create_thread(
                    name=thread_name,
                    type=discord.ChannelType.private_thread,
                    auto_archive_duration=10080,
                    invitable=True
                )
                thread_creation_log = f"私有子区 {created_thread.mention} (ID: {created_thread.id}) 已创建。"
                thread_creation_log += f" 限定身份组: {thread_restricted_role.name}。"

                thread_welcome_message_content += f"原始投票信息: {vote_message.jump_url}\n\n"  # 指向原投票
                thread_welcome_message_content += f"这是一个私有讨论区，仅限身份组 {thread_restricted_role.mention} 的成员及投票发起人参与。(白字滚）\n"

                await created_thread.send(thread_welcome_message_content + "请在此理性发表你的看法。")
                added_users_to_thread = set()

                # 1. 添加发起命令的用户
                try:
                    await created_thread.add_user(interaction.user)
                    added_users_to_thread.add(interaction.user.id)
                    self.logger.info(
                        f"已将投票发起人 {interaction.user.name} (ID: {interaction.user.id}) 加入私有子区 {created_thread.id} (投票UUID: {vote_uuid})。")
                except discord.HTTPException as e:
                    self.logger.warning(
                        f"无法将投票发起人 {interaction.user.name} 加入私有子区 {created_thread.id} (投票UUID: {vote_uuid}): {e}")
                    thread_creation_log += " (警告: 添加发起人失败)"

                # 2. 添加所有拥有指定身份组的成员
                members_with_role_added_count = 0

                async for member in interaction.guild.fetch_members(limit=None):
                    if member.bot or member.id in added_users_to_thread:  # 跳过机器人和其他已添加的用户
                        continue
                    if thread_restricted_role in member.roles:
                        try:
                            await created_thread.add_user(member)
                            added_users_to_thread.add(member.id)
                            members_with_role_added_count += 1
                        except discord.HTTPException as e:
                            self.logger.warning(
                                f"无法将成员 {member.name} (ID: {member.id}) 加入私有子区 {created_thread.id} (投票UUID: {vote_uuid}): {e}")
                thread_creation_log += f" 已尝试邀请 {members_with_role_added_count} 位拥有该身份组的成员。"

            else:
                # --- 创建公共子区 ---
                is_private_thread_flag = False

                created_thread = await vote_message.create_thread(
                    name=thread_name,
                    auto_archive_duration=10080
                )
                thread_creation_log = f"公共子区 {created_thread.mention} (ID: {created_thread.id}) 已创建。"
                thread_welcome_message_content += "这是一个公共讨论区，欢迎大家参与！\n"
                await created_thread.send(thread_welcome_message_content + "请在此理性发表你的看法。")

            # 设置慢速模式
            if thread_slowmode_seconds > 0 and created_thread:
                await created_thread.edit(slowmode_delay=thread_slowmode_seconds)
                thread_creation_log += f" 已设置 {thread_slowmode_seconds}秒 慢速模式。"

        except discord.HTTPException as e:
            self.logger.error(f"为投票 {vote_uuid} 创建或配置子区时发生HTTP错误: {e}")
            thread_creation_log = f"创建或配置子区时出错: {e}"
        except Exception as e:
            self.logger.error(f"为投票 {vote_uuid} 创建或配置子区时发生未知错误: {e}", exc_info=True)
            thread_creation_log = f"创建或配置子区时发生未知错误: {e}"



        vote_data = {
            "uuid": vote_uuid,
            "topic": topic, "description": description, "initiator_id": interaction.user.id,
            "initiator_name": interaction.user.name, "guild_id": interaction.guild.id,
            "channel_id": vote_message.channel.id, "message_id": vote_message.id,
            "red_votes_users": [], "blue_votes_users": [], "red_count": 0, "blue_count": 0,
            "active": True, "start_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "end_time": end_time,
            "thread_id": created_thread.id if created_thread else None,
            "thread_slowmode_seconds": thread_slowmode_seconds if created_thread else 0,
            "thread_restricted_role_id": thread_restricted_role.id if created_thread and thread_restricted_role else None,
            "is_private_thread": is_private_thread_flag if created_thread else False

        }
        try:
            save_vote_data(vote_message.id, vote_data)
        except Exception as e:
            # 日志已在save_vote_data中记录
            try:
                await vote_message.delete()
                self.logger.info(f"由于保存失败，已删除投票消息 {vote_message.id} (UUID: {vote_uuid})")
                if created_thread:
                    await created_thread.delete()
                    self.logger.info(f"由于保存失败，已删除关联子区 {created_thread.id} (UUID: {vote_uuid})")
            except Exception as del_e:
                self.logger.error(f"尝试删除保存失败的投票消息/子区 (UUID: {vote_uuid}) 时出错: {del_e}")
            await interaction.followup.send("❌ 发起投票失败：无法保存投票数据。请联系管理员。", ephemeral=True)
            return

        response_msg = f"✅ 投票 '{topic}' 已在 {voting_channel.mention} 发起！ (消息ID: `{vote_message.id}`, UUID: `{vote_uuid}`)\n"
        if created_thread:
            response_msg += f"讨论子区: {created_thread.mention}\n"
        if thread_creation_log and "失败" in thread_creation_log or "错误" in thread_creation_log:  # 如果子区配置有警告
            response_msg += f"⚠️ 子区提示: {thread_creation_log}"

        await interaction.followup.send(response_msg, ephemeral=True)
        self.logger.info(
            f"投票 '{topic}' (ID: {vote_message.id}, UUID: {vote_uuid}) 由 {interaction.user.name} 在 G:{interaction.guild_id}/C:{voting_channel.id} 发起。{thread_creation_log}")

    async def _conclude_vote(self, message_id: int, ended_by_user_id: int | None = None):
        """内部辅助函数，用于结束投票并宣布结果。"""
        vote_data = load_vote_data(message_id)
        if not vote_data or not vote_data.get("active", False):
            self.logger.info(
                f"尝试结束一个已经不活跃或缺失的投票 {message_id} (UUID: {vote_data.get('uuid', 'N/A') if vote_data else 'N/A'})")
            return False  # 返回布尔值指示成功与否

        vote_uuid = vote_data.get('uuid', 'N/A')  # 获取UUID用于日志

        vote_data["active"] = False
        # 如果是手动结束，并且原定结束时间晚于现在，或者没有原定结束时间，则用现在时间
        if "end_time" not in vote_data or vote_data["end_time"] is None or \
                (ended_by_user_id and vote_data["end_time"] and datetime.datetime.fromisoformat(
                    vote_data["end_time"]) > datetime.datetime.now(datetime.timezone.utc)):
            vote_data["actual_end_time"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        elif vote_data["end_time"]:  # 定时结束
            vote_data["actual_end_time"] = vote_data["end_time"]
        else:  # 无结束时间，但被手动结束
            vote_data["actual_end_time"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

        try:
            save_vote_data(message_id, vote_data)
        except Exception as e:
            # 日志已在save_vote_data中记录
            return False

        channel = self.bot.get_channel(vote_data["channel_id"])
        if not channel:
            try:
                channel = await self.bot.fetch_channel(vote_data["channel_id"])
            except (discord.NotFound, discord.Forbidden):
                self.logger.error(
                    f"结束投票 {message_id} (UUID: {vote_uuid}) 时，无法找到或访问频道 {vote_data['channel_id']}")
                return False

        vote_message = None
        try:
            vote_message = await channel.fetch_message(message_id)
            original_embed = vote_message.embeds[0]

            ended_title = f"🚫 已结束 - {original_embed.title.replace('🗳️ 投票辩诉: ', '')}"  # 避免重复前缀
            if not original_embed.title.startswith("🚫 已结束 - "):
                original_embed.title = ended_title

            original_embed.color = discord.Color.dark_grey()

            footer_text_parts = []
            if original_embed.footer.text:
                # "投票发起人: User (ID) | UUID: XXXXX | 结束于: YYYY"
                # "投票发起人: User (ID) | UUID: XXXXX | 投票无固定结束时间"
                parts = original_embed.footer.text.split(" | ")
                for part in parts:
                    if not part.startswith("结束于:") and not part.startswith("投票无固定结束时间"):
                        footer_text_parts.append(part)

            actual_end_timestamp = int(datetime.datetime.fromisoformat(vote_data["actual_end_time"]).timestamp())
            footer_text_parts.append(f"投票已于 <t:{actual_end_timestamp}:F> 结束")
            original_embed.set_footer(text=" | ".join(footer_text_parts))

            # 禁用按钮
            disabled_view = DebateVoteView(cog_logger=self.logger,vote_initiator_role_id=self.vote_initiator_role_id)
            for item in disabled_view.children:
                item.disabled = True
            await vote_message.edit(embed=original_embed, view=disabled_view)

        except discord.NotFound:
            self.logger.warning(f"结束投票时，未找到原始投票消息 {message_id} (UUID: {vote_uuid})。可能已被删除。")
        except (discord.Forbidden, discord.HTTPException) as e:
            self.logger.error(f"结束投票 {message_id} (UUID: {vote_uuid}) 时，编辑投票消息失败: {e}")
        except IndexError:  # 如果 embeds 为空
            self.logger.error(f"结束投票时，投票消息 {message_id} (UUID: {vote_uuid}) 的 Embed 结构异常。")

        result_message_content = f"投票辩诉 **'{vote_data['topic']}'** (UUID: `{vote_uuid}`) 已结束！\n"
        result_message_content += f"🔴 红方: {vote_data['red_count']}票\n"
        result_message_content += f"🔵 蓝方: {vote_data['blue_count']}票\n\n"

        winner = "平票"
        if vote_data['red_count'] > vote_data['blue_count']:
            winner = "🔴 红方"
        elif vote_data['blue_count'] > vote_data['red_count']:
            winner = "🔵 蓝方"
        result_message_content += f"**结果: {winner}胜出！** 🎉"
        #查成分时间
        result_message_content += f"\n投🔵 蓝方的议员有：\n\n"
        for str1 in vote_data["blue_votes_users"]:
            result_message_content += f"<@{str1}>"
        result_message_content += f"投🔴 红方的议员有：\n\n"
        for str2 in vote_data["red_votes_users"]:
            result_message_content += f"<@{str2}>"

        try:
            await channel.send(result_message_content, reference=vote_message if vote_message else None,
                               allowed_mentions=discord.AllowedMentions.none())
            self.logger.info(
                f"投票 '{vote_data['topic']}' (ID: {message_id}, UUID: {vote_uuid}) 已结束。获胜方: {winner}。")
        except (discord.Forbidden, discord.HTTPException) as e:
            self.logger.error(f"为投票 {message_id} (UUID: {vote_uuid}) 在频道 C:{channel.id} 发送结束消息失败: {e}")


        # --- 处理关联子区 ---
        if vote_data.get("thread_id"):
            try:
                thread = self.bot.get_channel(vote_data["thread_id"])
                if not thread:  # 如果缓存中没有，尝试获取
                    thread = await self.bot.fetch_channel(vote_data["thread_id"])

                if thread and isinstance(thread, discord.Thread):
                    await thread.send(
                        f"**投票 '{vote_data['topic']}' (UUID: `{vote_uuid}`) 已结束。**\n结果: {winner}胜出！\n此讨论区将存档并锁定。")
                    await thread.edit(archived=True, locked=True)  # 存档并锁定子区
                    self.logger.info(f"投票 {message_id} (UUID: {vote_uuid}) 的关联子区 {thread.id} 已存档并锁定。")
            except discord.NotFound:
                self.logger.warning(
                    f"结束投票 {message_id} (UUID: {vote_uuid}) 时，未找到关联的子区 {vote_data['thread_id']}。")
            except discord.Forbidden:
                self.logger.warning(
                    f"结束投票 {message_id} (UUID: {vote_uuid}) 时，无权限操作子区 {vote_data['thread_id']} (存档/锁定/发送消息)。")
            except discord.HTTPException as e:  # 更广泛的HTTP错误
                self.logger.error(
                    f"结束投票 {message_id} (UUID: {vote_uuid}) 时，操作子区 {vote_data['thread_id']} 发生HTTP错误: {e}")
            except Exception as e:
                self.logger.error(
                    f"结束投票 {message_id} (UUID: {vote_uuid}) 时，处理子区 {vote_data['thread_id']} 出错: {e}",
                    exc_info=True)
        return True  # 表示投票结束流程（大部分）成功

    @vote_admin.command(name="end", description="手动结束一个投票辩诉")
    @app_commands.describe(vote_identifier="投票消息的ID、链接或其UUID")  # <--- 接受UUID
    async def end_vote_command(self, interaction: discord.Interaction, vote_identifier: str):
        """手动结束投票的命令"""
        is_admin = interaction.user.id in BOT_CONFIG.get('admins', [])

        msg_id_to_process = None
        target_vote_data = None

        # 尝试将 vote_identifier 解析为消息 ID
        try:
            if '/' in vote_identifier:  # 可能是消息链接
                msg_id_to_process = int(vote_identifier.split('/')[-1])
            else:  # 可能是纯数字消息ID
                msg_id_to_process = int(vote_identifier)

            if msg_id_to_process:
                target_vote_data = load_vote_data(msg_id_to_process)

        except ValueError:  # 不是纯数字，也不是链接格式，可能是UUID
            pass

            # 如果通过消息ID没找到，或者输入不是消息ID格式，尝试通过UUID查找
        if not target_vote_data:
            found_by_uuid = False
            for vote_file_path in VOTE_DATA_DIR.glob("*.json"):
                try:
                    temp_msg_id = int(vote_file_path.stem)
                    data = load_vote_data(temp_msg_id)
                    if data and data.get("uuid") == vote_identifier:
                        target_vote_data = data
                        msg_id_to_process = temp_msg_id  # 获取对应的message_id
                        found_by_uuid = True
                        break
                except ValueError:  # 文件名不是纯数字
                    continue
                except Exception as e:  # 加载文件出错
                    self.logger.warning(f"尝试通过UUID查找投票时，加载文件 {vote_file_path.name} 失败: {e}")
                    continue
            if not found_by_uuid:
                await interaction.response.send_message("❌ 无效的投票标识符，或未找到此ID/UUID的投票数据。",
                                                        ephemeral=True)
                return

        if not target_vote_data:  # 双重检查
            await interaction.response.send_message("❌ 未找到此投票数据。", ephemeral=True)
            return

        # 权限检查：发起人或管理员
        is_initiator = target_vote_data.get("initiator_id") == interaction.user.id
        can_moderate_vote =  interaction.user.guild_permissions.administrator

        if not (is_initiator or can_moderate_vote or is_admin):
            await interaction.response.send_message(
                "❌ 你没有权限结束此投票 (需要发起人/服务器管理员权限)。",
                ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        if not target_vote_data.get("active", True):
            await interaction.followup.send("ℹ️ 这个投票已经结束了。", ephemeral=True)
            return

        success = await self._conclude_vote(msg_id_to_process, ended_by_user_id=interaction.user.id)
        vote_topic = target_vote_data.get('topic', '未知主题')
        vote_uuid_val = target_vote_data.get('uuid', 'N/A')

        if success:
            await interaction.followup.send(
                f"✅ 投票 '{vote_topic}' (ID: `{msg_id_to_process}`, UUID: `{vote_uuid_val}`) 已被你手动结束。",
                ephemeral=True)
        else:
            await interaction.followup.send(
                f"⚠️ 尝试结束投票 '{vote_topic}' (ID: `{msg_id_to_process}`, UUID: `{vote_uuid_val}`), 但过程中可能出现问题。请检查日志。",
                ephemeral=True)

    @tasks.loop(minutes=1)
    async def check_timed_votes(self):
        """后台任务，定时检查并结束到期的投票"""
        await self.bot.wait_until_ready()  # 确保机器人完全就绪
        now = datetime.datetime.now(datetime.timezone.utc)

        for vote_file_path in VOTE_DATA_DIR.glob("*.json"):
            try:
                msg_id = int(vote_file_path.stem)  # 文件名是 message_id
                vote_data = load_vote_data(msg_id)

                if vote_data and vote_data.get("active") and vote_data.get("end_time"):
                    end_time_dt = datetime.datetime.fromisoformat(vote_data["end_time"])
                    if now >= end_time_dt:
                        vote_uuid = vote_data.get('uuid', 'N/A')
                        self.logger.info(
                            f"定时投票 '{vote_data['topic']}' (ID: {msg_id}, UUID: {vote_uuid}) 已到期。正在结束...")
                        await self._conclude_vote(msg_id)
            except ValueError:  # 文件名不是整数
                self.logger.warning(f"check_timed_votes: 跳过非整数的投票文件名: {vote_file_path.name}")
            except Exception as e:
                self.logger.error(f"check_timed_votes 处理文件 {vote_file_path.name} 时出错: {e}", exc_info=True)


async def setup(bot: commands.Bot):
    """Cog 的标准入口函数"""
    if not hasattr(bot, 'logger'):

        bot.logger = module_logger
        module_logger.info("Bot对象未找到logger属性，已将模块logger赋给bot.logger")

    vote_cog_instance = VotingManageCommands(bot)
    await bot.add_cog(vote_cog_instance)
    if hasattr(bot, 'logger') and bot.logger:
        bot.logger.info("投票模块 已通过 setup 函数加载并添加。")
    else:
        print("投票模块 已通过 setup 函数加载并添加 (未找到 bot.logger，使用 print)。")