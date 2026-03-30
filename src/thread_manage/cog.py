import asyncio
import json
import pathlib
import discord
from discord.abc import Snowflake
from discord.ext import commands
from discord import app_commands
from src.utils import dm
from src.utils.confirm_view import confirm_view
from src.thread_manage.thread_clear import clear_thread_members
from src.thread_manage.auto_clear import AutoClearManager
from src.thread_manage.self_manage_ui import (
    ForumWelcomeView,
    SLOWMODE_OPTIONS,
    SelfManageMainMenuView,
    SlowModeSubView,
    TagEditView,
    ThreadMuteModal,
    forum_user_opted_out,
    schedule_delete_message,
    wait_menu_confirm_on_message,
)
from typing import Optional
import re
from datetime import datetime, timedelta

class ThreadSelfManage(commands.Cog):
    def __init__(self, bot):
        self.bot : commands.Bot = bot
        self.logger = bot.logger
        self.name = "自助管理"
        # 线程禁言记录缓存目录: data/thread_mute/<guild_id>/<thread_id>/<user_id>.json
        # 内存缓存：键为 (guild_id, thread_id, user_id)
        self._mute_cache: dict[tuple[int,int,int], dict] = {}
        # 禁言记录将在 on_ready 时加载到内存缓存

        # 初始化配置缓存
        self._config_cache = {}
        self._config_cache_mtime = None
        # 自动清理管理器
        self.auto_clear_manager = AutoClearManager(bot)

    self_manage = app_commands.Group(name="自助管理", description="在贴内进行权限操作，仅限贴主、协管或管理员")

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
            # 检查是否是服务器管理员
            if interaction.user.guild_permissions.administrator:
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

    def _get_delegate_file_path(self, guild_id: int, thread_id: int) -> pathlib.Path:
        data_dir = pathlib.Path("data") / "thread_delegates" / str(guild_id)
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / f"{thread_id}.json"

    def _load_thread_delegates(self, guild_id: int, thread_id: int) -> set[int]:
        file_path = self._get_delegate_file_path(guild_id, thread_id)
        if not file_path.exists():
            return set()

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return {int(user_id) for user_id in data.get("delegates", [])}
        except Exception as e:
            if self.logger:
                self.logger.error(f"读取子区协管配置失败: {file_path} - {e}")
            return set()

    def _save_thread_delegates(self, guild_id: int, thread_id: int, delegates: set[int]):
        file_path = self._get_delegate_file_path(guild_id, thread_id)

        try:
            if not delegates:
                if file_path.exists():
                    file_path.unlink()
                return

            data = {"delegates": sorted(delegates)}
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            if self.logger:
                self.logger.error(f"保存子区协管配置失败: {file_path} - {e}")
            raise

    def can_manage_as_owner(self, user_id: int, channel: discord.Thread) -> bool:
        """检查用户是否拥有与贴主等效的自助管理权限（贴主或协管）"""
        if user_id == channel.owner_id:
            return True
        return user_id in self._load_thread_delegates(channel.guild.id, channel.id)

    async def can_manage_thread(self, interaction: discord.Interaction, channel: discord.Thread) -> bool:
        """检查用户是否可以管理该子区（子区所有者、协管或管理员）"""
        if self.can_manage_as_owner(interaction.user.id, channel):
            return True
        return await self.is_admin(interaction)

    async def can_manage_delegate_settings(self, interaction: discord.Interaction, channel: discord.Thread) -> bool:
        """检查用户是否可以管理协管权限（仅子区所有者或管理员）"""
        if interaction.user.id == channel.owner_id:
            return True
        return await self.is_admin(interaction)

    def _load_mute_cache(self):
        """加载所有禁言记录到内存缓存"""
        base = pathlib.Path("data") / "thread_mute"
        if not base.exists():
            return
        for guild_dir in base.iterdir():
            if not guild_dir.is_dir():
                continue
            for thread_dir in guild_dir.iterdir():
                if not thread_dir.is_dir():
                    continue
                for file in thread_dir.glob("*.json"):
                    try:
                        user_id = int(file.stem)
                        with open(file, 'r', encoding='utf-8') as f:
                            record = json.load(f)
                        key = (int(guild_dir.name), int(thread_dir.name), user_id)
                        self._mute_cache[key] = record
                    except Exception as e:
                        if self.logger:
                            self.logger.error(f"加载禁言缓存出错: {file} - {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        if self.logger:
            self.logger.info("自助管理指令加载成功")
        # 预加载禁言缓存
        self._load_mute_cache()
        if self.logger:
            self.logger.info(f"已加载禁言缓存: 共 {len(self._mute_cache)} 条记录")
        # 初始化自动清理管理器
        if self.logger:
            disabled_count = len(self.auto_clear_manager.disabled_threads)
            self.logger.info(f"自动清理管理器已初始化，共 {disabled_count} 个子区被禁用自动清理")

        self._register_context_menus()

    def _register_context_menus(self):
        """注册右键菜单命令，若已存在则先移除再添加（支持热重载）"""
        self.bot.tree.remove_command("删除消息", type=discord.AppCommandType.message)
        self.bot.tree.remove_command("标注/取消标注", type=discord.AppCommandType.message)
        self.bot.tree.remove_command("子区禁言", type=discord.AppCommandType.user)
        self.bot.tree.remove_command("子区解除禁言", type=discord.AppCommandType.user)

        self.ctx_delete_message = app_commands.ContextMenu(name="删除消息", callback=self.delete_message_context_menu)
        self.bot.tree.add_command(self.ctx_delete_message)
        self.ctx_pin_operations = app_commands.ContextMenu(name="标注/取消标注", callback=self.pin_operations_context_menu)
        self.bot.tree.add_command(self.ctx_pin_operations)
        self.ctx_thread_mute = app_commands.ContextMenu(name="子区禁言", callback=self.thread_mute_user_context_menu)
        self.bot.tree.add_command(self.ctx_thread_mute)
        self.ctx_thread_unmute = app_commands.ContextMenu(name="子区解除禁言", callback=self.thread_unmute_user_context_menu)
        self.bot.tree.add_command(self.ctx_thread_unmute)

    async def cog_unload(self):
        self.bot.tree.remove_command("删除消息", type=discord.AppCommandType.message)
        self.bot.tree.remove_command("标注/取消标注", type=discord.AppCommandType.message)
        self.bot.tree.remove_command("子区禁言", type=discord.AppCommandType.user)
        self.bot.tree.remove_command("子区解除禁言", type=discord.AppCommandType.user)

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        """论坛新帖创建时发送自助管理提示（公开消息，10 分钟后删除）。"""
        try:
            if thread.owner_id is None:
                return
            parent = thread.parent
            if not isinstance(parent, discord.ForumChannel):
                return
            if forum_user_opted_out(thread.owner_id):
                return
            owner = thread.guild.get_member(thread.owner_id)
            if owner and owner.bot:
                return
            embed = discord.Embed(
                description=(
                    "欢迎使用自助管理系统，您可以随时在帖内输入`/自助管理 菜单`打开功能菜单辅助管理您的帖子"
                ),
                colour=discord.Colour.blue(),
            )
            embed.set_footer(text="本消息将在10分钟后自动删除")
            view = ForumWelcomeView()
            msg = await thread.send(embed=embed, view=view)
            asyncio.create_task(schedule_delete_message(msg, 600))
        except Exception as e:
            if self.logger:
                self.logger.error(f"论坛欢迎消息发送失败: {e}")

    @self_manage.command(name="菜单", description="打开自助管理图形菜单（下拉选择功能）")
    async def self_manage_menu(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        if not await self.can_manage_thread(interaction, channel):
            await interaction.response.send_message("不能在他人子区内使用此指令", ephemeral=True)
            return
        embed = discord.Embed(
            title="自助管理菜单",
            description="请在下拉列表中选择要执行的操作。",
            colour=discord.Colour.blue(),
        )
        await interaction.response.send_message(
            embed=embed,
            view=SelfManageMainMenuView(self, channel),
            ephemeral=True,
        )

    async def menu_run_lock(self, interaction: discord.Interaction, channel: discord.Thread):
        if channel.locked:
            await interaction.edit_original_response(content="此子区已经被锁定。", embed=None, view=None)
            return
        msg = await interaction.original_response()
        lock_msg = f"确定要锁定子区 **{channel.name}** 吗？锁定后其他人将无法发言。"
        ok1 = await wait_menu_confirm_on_message(
            msg,
            interaction.user.id,
            title="锁定子区",
            description=lock_msg,
            colour=discord.Colour.orange(),
        )
        if not ok1:
            return
        try:
            lock_notice = (
                f"🔒 **子区已锁定**\n\n"
                f"由 {interaction.user.mention} 锁定于 {discord.utils.format_dt(datetime.now())}"
            )
            await channel.send(lock_notice)
            await interaction.followup.send(
                f"可使用 `/自助管理 解锁子区 {channel.id}` 解锁子区。",
                ephemeral=True,
            )
            await channel.edit(locked=True, archived=True)
            await interaction.followup.send("✅ 子区已锁定", ephemeral=True)
            await msg.edit(
                embed=discord.Embed(title="完成", description="已锁定并归档。", colour=discord.Colour.green()),
                view=None,
            )
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ 锁定失败: {str(e)}", ephemeral=True)
            try:
                await msg.edit(content=f"❌ 锁定失败: {str(e)}", embed=None, view=None)
            except Exception:
                pass

    async def menu_run_delete_thread(self, interaction: discord.Interaction, channel: discord.Thread):
        if interaction.user.id != channel.owner_id:
            await interaction.edit_original_response(content="只有子区所有者可以删除子区。", embed=None, view=None)
            return
        msg = await interaction.original_response()
        ok1 = await wait_menu_confirm_on_message(
            msg,
            interaction.user.id,
            title="删除子区（1/2）",
            description=(
                f"⚠️ **危险操作** ⚠️\n\n确定要删除子区 **{channel.name}** 吗？\n\n"
                "**此操作不可逆，将删除所有消息和历史记录！**"
            ),
            colour=discord.Colour.red(),
        )
        if not ok1:
            return
        ok2 = await wait_menu_confirm_on_message(
            msg,
            interaction.user.id,
            title="删除子区（2/2）",
            description=(
                f"⚠️ **再次确认** ⚠️\n\n真的确定要删除子区 **{channel.name}** 吗？\n\n"
                "**此操作不可逆！**"
            ),
            colour=discord.Colour.red(),
        )
        if not ok2:
            return
        await asyncio.sleep(0.5)
        try:
            await interaction.followup.send("✅ 子区已删除", ephemeral=True)
        except Exception:
            pass
        try:
            await channel.delete()
        except discord.HTTPException as e:
            try:
                await interaction.followup.send(f"❌ 删除失败: {str(e)}", ephemeral=True)
            except Exception:
                pass

    async def apply_slowmode_from_menu(self, interaction: discord.Interaction, channel: discord.Thread, seconds: int):
        label = next((n for n, s in SLOWMODE_OPTIONS if s == seconds), str(seconds))
        try:
            await channel.edit(slowmode_delay=seconds)
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ 设置失败: {str(e)}", ephemeral=True)
            return
        try:
            if seconds == 0:
                await channel.send(
                    f"⏱️ **慢速模式已关闭**\n\n由 {interaction.user.mention} 设置于 {discord.utils.format_dt(datetime.now())}"
                )
            else:
                await channel.send(
                    f"⏱️ **慢速模式已设置为 {label}**\n\n由 {interaction.user.mention} 设置于 {discord.utils.format_dt(datetime.now())}"
                )
        except discord.HTTPException:
            pass

        embed = discord.Embed(
            title="慢速模式",
            description=f"✅ 已应用：**{label}**",
            colour=discord.Colour.green(),
        )
        await interaction.response.edit_message(embed=embed, view=SlowModeSubView(self, channel))

    async def apply_announce_from_modal(self, interaction: discord.Interaction, channel: discord.Thread, text: str):
        await interaction.response.defer(ephemeral=True)
        await channel.send(
            f"@everyone \n{text}\n> -# 这是一条由贴主发送的子区内通知\n> -# 如果不想再收到此类通知，取消关注本贴即可"
        )
        await interaction.followup.send("已通知所有在本贴内的成员", ephemeral=True)

    async def apply_title_from_modal(self, interaction: discord.Interaction, channel: discord.Thread, new_title: str):
        new_title = new_title.strip()
        if len(new_title) > 100:
            await interaction.response.send_message("❌ 标题长度不能超过100字符", ephemeral=True)
            return
        if not new_title:
            await interaction.response.send_message("❌ 标题不能为空", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        old_title = channel.name
        try:
            await channel.edit(name=new_title)
            await interaction.followup.send(f"✅ 子区标题已更新为：**{new_title}**", ephemeral=True)
            title_notice = (
                f"📝 **子区标题已更新**\n\n"
                f"**旧标题：** {old_title}\n"
                f"**新标题：** {new_title}\n\n"
                f"由 {interaction.user.mention} 更新于 {discord.utils.format_dt(datetime.now())}"
            )
            await channel.send(title_notice)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ 编辑标题失败: {str(e)}", ephemeral=True)

    async def toggle_forum_tag(
        self,
        interaction: discord.Interaction,
        thread: discord.Thread,
        tag_id: int,
        tag_name: str,
    ):
        parent = thread.parent
        if not isinstance(parent, discord.ForumChannel):
            await interaction.response.send_message("此功能仅在论坛频道的子区内有效", ephemeral=True)
            return
        if not await self.can_manage_thread(interaction, thread):
            await interaction.response.send_message("不能在他人子区内使用此指令", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            fresh = await interaction.guild.fetch_channel(thread.id)
        except Exception:
            fresh = thread
        if not isinstance(fresh, discord.Thread):
            await interaction.followup.send("无法获取子区。", ephemeral=True)
            return
        target_tag = discord.utils.get(parent.available_tags, id=tag_id)
        if not target_tag:
            await interaction.followup.send(f"❌ 找不到标签 **{tag_name}**", ephemeral=True)
            return
        current_tags = list(fresh.applied_tags)
        if target_tag in current_tags:
            new_tags = [t for t in current_tags if t.id != tag_id]
            action_word = "移除"
        else:
            if len(current_tags) >= 5:
                await interaction.followup.send("❌ 子区最多只能有5个标签", ephemeral=True)
                return
            new_tags = current_tags + [target_tag]
            action_word = "添加"
        try:
            await fresh.edit(applied_tags=new_tags)
            await interaction.followup.send(f"✅ 已{action_word}标签：**{target_tag.name}**", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ 操作失败: {str(e)}", ephemeral=True)
            return
        try:
            fresh = await interaction.guild.fetch_channel(fresh.id)
        except Exception:
            pass
        if not isinstance(fresh, discord.Thread):
            return
        view = TagEditView(self, fresh)
        view._build_buttons(parent, fresh)
        applied = ", ".join(t.name for t in fresh.applied_tags) or "（无）"
        embed = discord.Embed(
            title="编辑标签",
            description=(
                "点击下方标签可添加或移除（每帖最多 5 个标签）。\n"
                f"**当前标签：** {applied}"
            ),
            colour=discord.Colour.green(),
        )
        try:
            await interaction.edit_original_response(embed=embed, view=view)
        except Exception:
            pass

    async def thread_mute_user_context_menu(self, interaction: discord.Interaction, member: discord.Member):
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        if not await self.can_manage_thread(interaction, channel):
            await interaction.response.send_message("不能在他人子区内使用此指令", ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message("❌ 不能禁言机器人", ephemeral=True)
            return
        if member.id == interaction.user.id:
            await interaction.response.send_message("无法禁言自己", ephemeral=True)
            return
        try:
            config = getattr(self.bot, "config", {})
            for admin_role_id in config.get("admins", []):
                role = interaction.guild.get_role(int(admin_role_id))
                if role and role in member.roles:
                    await interaction.response.send_message("无法禁言管理组成员", ephemeral=True)
                    return
        except Exception:
            pass
        await interaction.response.send_modal(ThreadMuteModal(self, channel, member))

    async def apply_ctx_mute_from_modal(
        self,
        interaction: discord.Interaction,
        channel: discord.Thread,
        member: discord.Member,
        duration: str,
        reason: str,
    ):
        await interaction.response.defer(ephemeral=True)
        duration = (duration or "").strip()
        reason = (reason or "").strip() or None
        if duration:
            sec, human = self._parse_time(duration)
            if sec < 0:
                await interaction.followup.send("❌ 无效时长，请使用 m/h/d 结尾", ephemeral=True)
                return
            until = datetime.now() + timedelta(seconds=sec)
            muted_until = until.isoformat()
        else:
            muted_until = -1
            human = "永久"
        embed = discord.Embed(
            title="🔒 子区禁言",
            description=f"👤 {member.mention} 已被禁言",
            color=discord.Color.red(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="原因", value=reason if reason else "无", inline=True)
        embed.add_field(name="时长", value=duration if duration else "永久", inline=True)
        embed.add_field(name="执行者", value=interaction.user.mention, inline=True)
        await channel.send(embed=embed)
        rec = self._get_mute_record(channel.guild.id, channel.id, member.id)
        rec["muted_until"] = muted_until
        rec["violations"] = 0
        self._save_mute_record(channel.guild.id, channel.id, member.id, rec)
        msg = f"✅ 已在子区禁言 {member.mention}"
        if duration:
            msg += f" 持续 {human}"
        await interaction.followup.send(msg, ephemeral=True)

    async def thread_unmute_user_context_menu(self, interaction: discord.Interaction, member: discord.Member):
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        if not await self.can_manage_thread(interaction, channel):
            await interaction.response.send_message("只有子区所有者或管理员可执行此操作", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        data_dir = pathlib.Path("data") / "thread_mute" / str(channel.guild.id) / str(channel.id)
        file_path = data_dir / f"{member.id}.json"
        if file_path.exists():
            file_path.unlink()
            key = (channel.guild.id, channel.id, member.id)
            self._mute_cache.pop(key, None)
            self._save_mute_record(channel.guild.id, channel.id, member.id, None)
            embed = discord.Embed(
                title="🔒 子区禁言",
                description=f"👤 {member.mention} 已被解除禁言",
                color=discord.Color.green(),
                timestamp=datetime.now(),
            )
            await channel.send(embed=embed)
            await interaction.followup.send(f"✅ 已解除 {member.mention} 的子区禁言", ephemeral=True)
        else:
            await interaction.followup.send("该成员未被禁言", ephemeral=True)

    @self_manage.command(name="清理子区", description="清理子区内不活跃成员")
    @app_commands.describe(threshold="阈值(默认900，最低800)")
    @app_commands.rename(threshold="阈值")
    async def clear_thread(self, interaction: discord.Interaction, threshold: app_commands.Range[int, 800, 1000]=900):
        # 获取当前子区
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        
        if not await self.can_manage_thread(interaction, channel):
            await interaction.response.send_message("不能在他人子区内使用此指令", ephemeral=True)
            return

        # 检查是否有正在进行的自动清理任务
        if self.auto_clear_manager.is_clearing_active(channel.id):
            await interaction.response.send_message(
                "❌ 该子区已经在清理中，请等待清理完成", 
                ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)

        # 获取子区内的成员
        members = await channel.fetch_members()
        # 计数
        count = len(members)

        if count <= threshold:
            # embed
            embed = discord.Embed(title="清理子区", description=f"当前子区内有{count}名成员，低于阈值{threshold}，无需清理", color=0x808080)
            await interaction.edit_original_response(embed=embed)
            return
        
        # 调用统一的确认视图
        confirmed = await confirm_view(
            interaction,
            title="清理子区",
            description="\n".join(
                [
                    f"确定要清理 【{channel.name}】 中的不活跃成员吗？",
                    "",
                    f"**将至少清理 {count - threshold} 名成员**",
                    "优先清理未发言成员，不足则移除发言最少的成员",
                    "被移除的成员可以重新加入子区",
                ]
            ),
            colour=discord.Colour(0x808080),
            timeout=60,
        )

        if not confirmed:
            return
        
        # 再次检测是否正在清理（此时 interaction 已 defer，需用 edit_original_response）
        if self.auto_clear_manager.is_clearing_active(channel.id):
            await interaction.edit_original_response(
                content="❌ 该子区已经在清理中，请等待清理完成"
            )
            return

        # 标记手动清理开始
        self.auto_clear_manager.mark_manual_clearing(channel.id, True)

        # 进行清理，实时更新进度

        # 先发一个初始 embed
        progress_embed = discord.Embed(
            title="准备开始…",
            colour=discord.Colour.orange(),
        )

        # 立即更新一次消息，显示准备状态
        try:
            await interaction.edit_original_response(embed=progress_embed)
        except discord.HTTPException:
            pass

        # 定义进度回调
        async def progress_hook(done: int, total: int, member: discord.Member, stage: str):
            nonlocal progress_embed

            # 统计阶段
            if stage == "stat_start":
                progress_embed.title = "正在统计消息…"
                if len(progress_embed.fields) == 0:
                    progress_embed.add_field(name="统计", value="开始统计…", inline=False)
                else:
                    progress_embed.set_field_at(0, name="统计", value="开始统计…", inline=False)

            elif stage == "stat_progress":
                # 更新统计字段
                value = f"已读取 **{done}** 条消息…"
                if len(progress_embed.fields) == 0:
                    progress_embed.add_field(name="统计", value=value, inline=False)
                else:
                    progress_embed.set_field_at(0, name="统计", value=value, inline=False)

            elif stage == "stat_done":
                value = f"统计完成，共 **{done}** 条消息。"
                if len(progress_embed.fields) == 0:
                    progress_embed.add_field(name="统计", value=value, inline=False)
                else:
                    progress_embed.set_field_at(0, name="统计", value=value, inline=False)

                # 为清理阶段预留字段
                progress_embed.add_field(name="清理", value="等待开始…", inline=False)
                progress_embed.title = "正在清理子区…"

                await interaction.edit_original_response(embed=progress_embed)

            # 清理阶段
            elif stage == "start":
                # 初始化清理字段（index 1）
                if len(progress_embed.fields) < 2:
                    progress_embed.add_field(name="清理", value="0/0 (0%)", inline=False)
                # total 为清理目标总数
                pct = 0 if total == 0 else int(done / total * 100)
                progress_embed.set_field_at(1, name="清理", value=f"{done}/{total} （{pct}%）", inline=False)

            elif stage == "progress":
                # 更新清理进度
                pct = 0 if total == 0 else int(done / total * 100)
                if len(progress_embed.fields) < 2:
                    progress_embed.add_field(name="清理", value=f"{done}/{total} （{pct}%）", inline=False)
                else:
                    progress_embed.set_field_at(1, name="清理", value=f"{done}/{total} （{pct}%）", inline=False)

            elif stage == "done":
                progress_embed.colour = discord.Colour.green()
                progress_embed.title = "清理完成"
                if len(progress_embed.fields) >= 2:
                    progress_embed.set_field_at(1, name="清理", value="完成！", inline=False)

            try:
                await interaction.edit_original_response(embed=progress_embed)
            except discord.HTTPException:
                pass  # 轻忽编辑失败（可能被频率限制）

        try:
            # 调用清理函数
            result = await clear_thread_members(
                channel,
                threshold,
                self.bot,
                logger=self.logger,
                progress_cb=progress_hook,
            )

            # 最终结果 embed
            final_embed = discord.Embed(
                title="清理完成 ✅",
                colour=discord.Colour.green(),
                description=(
                    f"🔸 已移除未发言成员：**{result['removed_inactive']}** 人\n"
                    f"🔸 已移除低活跃成员：**{result['removed_active']}** 人\n"
                    f"现在子区成员约为 **{result['final_count']}** 人"
                ),
            )

            await interaction.edit_original_response(embed=final_embed)
            await interaction.followup.send("✅ 子区清理完成", embed=final_embed, ephemeral=False)
            
        except Exception as e:
            error_embed = discord.Embed(
                title="❌ 清理失败",
                description=f"执行清理时发生错误：\n```{str(e)}```",
                color=discord.Color.red()
            )
            await interaction.edit_original_response(embed=error_embed)
            if self.logger:
                self.logger.error(f"手动清理失败: {channel.name} (ID: {channel.id}) - {e}")
        finally:
            # 标记手动清理结束
            self.auto_clear_manager.mark_manual_clearing(channel.id, False)

    # ---- 子区@全体 ----
    @self_manage.command(name="全体通知", description="@所有在本贴内的成员")
    @app_commands.describe(message="要通知的消息")
    @app_commands.rename(message="消息")
    async def announce_all(self, interaction: discord.Interaction, message: str):
        # 验证是否在子区内
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        
        # 验证是否是子区所有者或管理员
        if not await self.can_manage_thread(interaction, channel):
            await interaction.response.send_message("不能在他人子区内使用此指令", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        await channel.send(f"@everyone \n{message}\n> -# 这是一条由贴主发送的子区内通知\n> -# 如果不想再收到此类通知，取消关注本贴即可")
        await interaction.edit_original_response(content="已通知所有在本贴内的成员")

    # ---- 删除消息反应 ----
    @self_manage.command(name="删除消息反应", description="删除指定消息的反应")
    @app_commands.describe(message_link="要删除反应的消息链接", reaction="要删除的反应")
    @app_commands.rename(message_link="消息链接", reaction="反应")
    async def delete_reaction(self, interaction: discord.Interaction, message_link: str, reaction: str = None):
        # 验证是否在子区内
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        
        # 验证是否是子区所有者或管理员
        if not await self.can_manage_thread(interaction, channel):
            await interaction.response.send_message("不能在他人子区内使用此指令", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # 尝试获取消息
        try:
            message_id_int = int(message_link.strip().split("/")[-1])
            message = await channel.fetch_message(message_id_int)
        except (ValueError, discord.NotFound, discord.HTTPException):
            await interaction.edit_original_response(content="找不到指定的消息，请确认消息ID是否正确")
            return

        # 如果反应为空，则删除消息的所有反应
        if not reaction:
            # 获取反应数量，若太多则二次确认
            reaction_count = 0
            for single_reaction in message.reactions:
                reaction_count += single_reaction.count
            if reaction_count > 20:
                # 二次确认
                confirmed = await confirm_view(
                    interaction,
                    title="删除消息反应",
                    description=f"确定要删除消息的 {reaction_count} 个反应吗？",
                    colour=discord.Colour.red(),
                )
                if not confirmed:
                    return

            await message.clear_reactions()
            await interaction.edit_original_response(content="已删除消息的所有反应")
            return
        
        # 删除指定反应
        try:
            # 获取反应对象
            # 1. 精确匹配：str(emoji) == reaction（支持 <:name:id> 和 Unicode emoji）
            reaction_obj = discord.utils.find(
                lambda r: str(r.emoji) == reaction, message.reactions
            )
            # 2. 名称匹配（大小写不敏感）：支持 :name: 或直接输入 name
            if reaction_obj is None:
                clean_name = reaction.strip(':')
                matched = [
                    r for r in message.reactions
                    if hasattr(r.emoji, 'name') and r.emoji.name.lower() == clean_name.lower()
                ]
                if len(matched) == 1:
                    reaction_obj = matched[0]
                elif len(matched) > 1:
                    # 多个同名 emoji，用下拉菜单让用户选择
                    options = []
                    for i, m in enumerate(matched):
                        emoji_id = m.emoji.id if hasattr(m.emoji, 'id') else '?'
                        options.append(discord.SelectOption(
                            label=f"{m.emoji.name}（{m.count} 个反应）",
                            description=f"ID: {emoji_id}",
                            value=str(i),
                            emoji=m.emoji if not (hasattr(m.emoji, 'id') and m.emoji.id) else None,
                        ))
                    options.append(discord.SelectOption(
                        label="全部删除",
                        description=f"删除所有 {len(matched)} 个同名反应",
                        value="all",
                        emoji="🗑️",
                    ))

                    select = discord.ui.Select(
                        placeholder=f"找到 {len(matched)} 个同名反应，请选择要删除的…",
                        options=options,
                        min_values=1,
                        max_values=1,
                    )
                    result_indices = []

                    async def select_callback(select_interaction: discord.Interaction):
                        await select_interaction.response.defer()
                        result_indices.append(select.values[0])
                        view.stop()

                    select.callback = select_callback
                    view = discord.ui.View(timeout=60)
                    view.add_item(select)

                    embed = discord.Embed(
                        title="删除消息反应",
                        description="\n".join(
                            f"{i+1}. {str(m.emoji)}（{m.count} 个反应）"
                            for i, m in enumerate(matched)
                        ),
                        colour=discord.Colour.orange(),
                    )
                    embed.set_footer(text="请在下拉菜单中选择要删除的反应")
                    await interaction.edit_original_response(embed=embed, view=view)
                    await view.wait()

                    if not result_indices:
                        await interaction.edit_original_response(
                            content="⏱ 超时未选择，操作已取消。", embed=None, view=None
                        )
                        return

                    choice = result_indices[0]
                    if choice == "all":
                        for m in matched:
                            await message.clear_reaction(m.emoji)
                        await interaction.edit_original_response(
                            content=f"已删除消息的 {len(matched)} 个 {clean_name} 反应",
                            embed=None, view=None,
                        )
                    else:
                        target = matched[int(choice)]
                        await message.clear_reaction(target.emoji)
                        await interaction.edit_original_response(
                            content=f"已删除消息的 {str(target.emoji)} 反应",
                            embed=None, view=None,
                        )
                    return

            if reaction_obj is None:
                await interaction.edit_original_response(content="找不到指定的反应，请确认反应是否存在")
                return
            # 获取反应数量，若太多则二次确认
            reaction_count = reaction_obj.count
            if reaction_count > 20:
                # 二次确认
                confirmed = await confirm_view(
                    interaction,
                    title="删除消息反应",
                    description=f"确定要删除消息的 {reaction_count} 个反应吗？",
                    colour=discord.Colour.red(),
                )
                if not confirmed:
                    return

            await message.clear_reaction(reaction_obj.emoji)
            await interaction.edit_original_response(content=f"已删除消息的 {reaction} 反应")
            return

        except Exception:
            await interaction.edit_original_response(content="删除反应失败，请确认反应是否存在")

    # ---- 删除单条消息 ----
    @self_manage.command(name="删除消息", description="删除指定消息")
    @app_commands.describe(message_link="要删除的消息链接")
    @app_commands.rename(message_link="消息链接")
    async def delete_message(self, interaction: discord.Interaction, message_link: str):
        # 验证是否在子区内
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        
        # 验证是否是子区所有者或管理员
        if not await self.can_manage_thread(interaction, channel):
            await interaction.response.send_message("不能在他人子区内使用此指令", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # 尝试获取消息
        try:
            message_id_int = int(message_link.strip().split("/")[-1])
            message = await channel.fetch_message(message_id_int)
        except (ValueError, discord.NotFound, discord.HTTPException):
            await interaction.edit_original_response(content="找不到指定的消息，请确认消息ID是否正确")
            return

        # 验证是否有权限删除（只能删除自己的消息或者有子区管理权限）
        if message.author.id != interaction.user.id and not await self.can_manage_thread(interaction, channel):
            await interaction.edit_original_response(content="你只能删除自己的消息")
            return

        # 删除消息
        try:
            await message.delete()
            await interaction.edit_original_response(
                content="✅ 消息已删除", embed=None, view=None
            )
        except discord.HTTPException as e:
            await interaction.edit_original_response(
                content=f"❌ 删除失败: {str(e)}", embed=None, view=None
            )

    async def delete_message_context_menu(self, interaction: discord.Interaction, message: discord.Message):
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        
        if not await self.can_manage_thread(interaction, channel):
            await interaction.response.send_message("不能在他人子区内使用此指令", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        await message.delete()
        await interaction.edit_original_response(content="✅ 消息已删除", embed=None, view=None)

    # ---- 删除整个子区 ----
    @self_manage.command(name="删帖", description="删除整个子区")
    async def delete_thread(self, interaction: discord.Interaction):
        # 验证是否在子区内
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        
        # 验证是否贴主
        if interaction.user.id != channel.owner_id:
            await interaction.response.send_message("只有子区所有者可以删除子区", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # 确认删除
        confirmed = await confirm_view(
            interaction,
            title="删除子区",
            description=f"⚠️ **危险操作** ⚠️\n\n确定要删除子区 **{channel.name}** 吗？\n\n**此操作不可逆，将删除所有消息和历史记录！**",
            colour=discord.Colour.red(),
        )

        if not confirmed:
            return

        # 二次确认
        confirmed = await confirm_view(
            interaction,
            title="删除子区",
            description=f"⚠️ **再次确认** ⚠️\n\n真的确定要删除子区 **{channel.name}** 吗？\n\n**此操作不可逆，将删除所有消息和历史记录！**",
            colour=discord.Colour.red(),
        )

        if not confirmed:
            return

        # delay 500 ms
        await asyncio.sleep(0.5)

        # 删除子区
        try:
            await channel.delete()
        except discord.HTTPException as e:
            # beautiful embed for error
            embed = discord.Embed(
                title=f"❌ 删除失败",
                description=f"```{str(e)}```",
                color=discord.Color.red()
            )
            await interaction.edit_original_response(embed=embed, view=None)

    # ---- 锁定和关闭子区 ----
    @self_manage.command(name="锁定并归档", description="锁定子区，禁止发言并归档")
    @app_commands.describe(reason="锁定原因（可选）")
    @app_commands.rename(reason="原因")
    async def lock_thread(self, interaction: discord.Interaction, reason: Optional[str] = None):
        # 验证是否在子区内
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        
        # 验证是否是子区所有者或管理员
        if not await self.can_manage_thread(interaction, channel):
            await interaction.response.send_message("不能在他人子区内使用此指令", ephemeral=True)
            return

        # 判断是否已经锁定
        if channel.locked:
            await interaction.response.send_message("此子区已经被锁定", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # 确认锁定
        lock_msg = f"确定要锁定子区 **{channel.name}** 吗？锁定后其他人将无法发言。"
        if reason:
            lock_msg += f"\n\n**锁定原因：**\n{reason}"

        confirmed = await confirm_view(
            interaction,
            title="锁定子区",
            description=lock_msg,
            colour=discord.Colour.orange(),
        )

        if not confirmed:
            return

        # 锁定子区
        try:
            # 发送公告消息
            lock_notice = f"🔒 **子区已锁定**"
            if reason:
                lock_notice += f"\n\n**原因：** {reason}"
            lock_notice += f"\n\n由 {interaction.user.mention} 锁定于 {discord.utils.format_dt(datetime.now())}"
            
            # 在子区内发送锁定通知
            await channel.send(lock_notice)

            await interaction.followup.send(
                f"可使用 `/自助管理 解锁子区 {channel.id}` 解锁子区。",
                ephemeral=True,
            )

            await channel.edit(locked=True, archived=True)

            await interaction.followup.send("✅ 子区已锁定", ephemeral=True)

        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ 锁定失败: {str(e)}", ephemeral=True)

    # ---- 解锁子区 ----
    @self_manage.command(name="解锁子区", description="解锁子区，允许发言")
    @app_commands.describe(thread_id="要解锁的子区id")
    async def unlock_thread(self, interaction: discord.Interaction, thread_id: str):
        # 验证是否在子区内
        
        # 验证是否是子区id
        try:
            thread_id_int = int(thread_id)
        except ValueError as e:
            await interaction.response.send_message("请提供有效的子区ID", ephemeral=True)
            return
        
        # 尝试获取子区，包括已归档的子区
        try:
            channel = await interaction.guild.fetch_channel(thread_id_int)
        except (discord.NotFound, discord.HTTPException):
            # 如果fetch_channel失败，尝试get_channel_or_thread
            channel = interaction.guild.get_channel_or_thread(thread_id_int)

        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("请提供有效的子区ID", ephemeral=True)
            return
        
        # 验证是否是子区所有者或管理员
        if not await self.can_manage_thread(interaction, channel):
            await interaction.response.send_message("不能对他人子区使用此指令", ephemeral=True)
            return

        # 判断是否已经解锁
        if not channel.locked:
            await interaction.response.send_message("此子区未被锁定", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        
        # 解锁子区
        try:
            await channel.edit(locked=False, archived=False)
            
            # 发送公告消息
            unlock_notice = f"🔓 **子区已解锁**\n\n由 {interaction.user.mention} 解锁于 {discord.utils.format_dt(datetime.now())}"
            
            # 在子区内发送解锁通知
            await channel.send(unlock_notice)
            
            # 通知操作者
            await interaction.followup.send("✅ 子区已解锁", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ 解锁失败: {str(e)}", ephemeral=True)

    # ---- 设置慢速模式 ----
    @self_manage.command(name="慢速模式", description="设置发言间隔时间")
    @app_commands.describe(option="选择发言间隔时间")
    @app_commands.rename(option="时间")
    @app_commands.choices(
        option=[app_commands.Choice(name=name, value=sec) for name, sec in SLOWMODE_OPTIONS]
    )
    async def set_slowmode(self, interaction: discord.Interaction, option: app_commands.Choice[int]):
        # 验证是否在子区内
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        
        # 验证是否是子区所有者或管理员
        if not await self.can_manage_thread(interaction, channel):
            await interaction.response.send_message("不能在他人子区内使用此指令", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        
        # 设置慢速模式
        try:
            await channel.edit(slowmode_delay=option.value)
            
            if option.value == 0:
                # 通知操作者
                await interaction.followup.send("✅ 已关闭慢速模式", ephemeral=True)
                # 在子区内发送通知
                await channel.send(f"⏱️ **慢速模式已关闭**\n\n由 {interaction.user.mention} 设置于 {discord.utils.format_dt(datetime.now())}")
            else:
                # 通知操作者
                await interaction.followup.send(f"✅ 已设置慢速模式为 {option.name}", ephemeral=True)
                # 在子区内发送通知
                await channel.send(f"⏱️ **慢速模式已设置为 {option.name}**\n\n由 {interaction.user.mention} 设置于 {discord.utils.format_dt(datetime.now())}")
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ 设置失败: {str(e)}", ephemeral=True)

    # ---- 编辑子区标题 ----
    @self_manage.command(name="编辑标题", description="修改子区标题")
    @app_commands.describe(new_title="新的子区标题")
    @app_commands.rename(new_title="新标题")
    async def edit_title(self, interaction: discord.Interaction, new_title: str):
        # 验证是否在子区内
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        
        # 验证是否是子区所有者或管理员
        if not await self.can_manage_thread(interaction, channel):
            await interaction.response.send_message("不能在他人子区内使用此指令", ephemeral=True)
            return

        # 验证标题长度（Discord限制为100字符）
        if len(new_title) > 100:
            await interaction.response.send_message("❌ 标题长度不能超过100字符", ephemeral=True)
            return
        
        # 验证标题不为空
        if not new_title.strip():
            await interaction.response.send_message("❌ 标题不能为空", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        
        # 保存旧标题用于显示
        old_title = channel.name
        
        # 编辑子区标题
        try:
            await channel.edit(name=new_title.strip())
            
            # 通知操作者
            await interaction.followup.send(f"✅ 子区标题已更新为：**{new_title.strip()}**", ephemeral=True)
            
            # 在子区内发送通知
            title_notice = (
                f"📝 **子区标题已更新**\n\n"
                f"**旧标题：** {old_title}\n"
                f"**新标题：** {new_title.strip()}\n\n"
                f"由 {interaction.user.mention} 更新于 {discord.utils.format_dt(datetime.now())}"
            )
            await channel.send(title_notice)
            
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ 编辑标题失败: {str(e)}", ephemeral=True)

    # ---- 标注操作 ----
    @self_manage.command(name="标注", description="标注/取消标注消息")
    @app_commands.describe(
        action="操作类型",
        message_link="消息链接"
    )
    @app_commands.rename(
        action="操作",
        message_link="消息链接"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="📌 标注消息", value="pin"),
        app_commands.Choice(name="📍 取消标注", value="unpin"),
    ])
    async def pin_operations(
        self, 
        interaction: discord.Interaction, 
        action: app_commands.Choice[str],
        message_link: str
    ):
        # 验证是否在子区内
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        
        # 验证是否是子区所有者或管理员
        if not await self.can_manage_thread(interaction, channel):
            await interaction.response.send_message("不能在他人子区内使用此指令", ephemeral=True)
            return

        # 处理标注/取消标注操作
        if not message_link:
            await interaction.response.send_message("请提供要操作的消息链接", ephemeral=True)
            return
            
        # 尝试获取消息
        try:
            message_id_int = int(message_link.strip().split("/")[-1])
            message = await channel.fetch_message(message_id_int)
        except (ValueError, discord.NotFound, discord.HTTPException):
            await interaction.response.send_message("找不到指定的消息，请确认消息ID是否正确", ephemeral=True)
            return

        # 执行操作
        if action.value == "pin":
            # 检查是否已经置顶
            if message.pinned:
                await interaction.response.send_message("此消息已经被标注", ephemeral=True)
                return
                
            # 置顶消息
            try:
                await message.pin(reason=f"由 {interaction.user} 标注")
                await interaction.response.send_message("✅ 消息已标注", ephemeral=True)
            except discord.HTTPException as e:
                await interaction.response.send_message(f"❌ 标注失败: {str(e)}", ephemeral=True)
        
        elif action.value == "unpin":
            # 检查是否已经置顶
            if not message.pinned:
                await interaction.response.send_message("此消息未被标注", ephemeral=True)
                return
                
            # 取消置顶
            try:
                await message.unpin(reason=f"由 {interaction.user} 取消标注")
                await interaction.response.send_message("✅ 已取消标注", ephemeral=True)
            except discord.HTTPException as e:
                await interaction.response.send_message(f"❌ 取消标注失败: {str(e)}", ephemeral=True)

    async def pin_operations_context_menu(self, interaction: discord.Interaction, message: discord.Message):
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        
        if not await self.can_manage_thread(interaction, channel):
            await interaction.response.send_message("不能在他人子区内使用此指令", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        if message.pinned:
            await message.unpin(reason=f"由 {interaction.user} 取消标注")
            await interaction.followup.send("✅ 已取消标注", ephemeral=True)
        else:
            await message.pin(reason=f"由 {interaction.user} 标注")
            await interaction.followup.send("✅ 已标注", ephemeral=True)

    # ---- 编辑标签 ----
    @self_manage.command(name="编辑标签", description="编辑子区标签")
    @app_commands.describe(action="操作类型", tag="标签")
    @app_commands.rename(action="操作", tag="标签")
    @app_commands.choices(action=[
        app_commands.Choice(name="添加", value="add"),
        app_commands.Choice(name="删除", value="remove"),
    ])
    async def edit_tag(self, interaction: discord.Interaction, action: app_commands.Choice[str], tag: str):
        # 验证是否在子区内
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        
        # 验证是否是子区所有者或管理员
        if not await self.can_manage_thread(interaction, channel):
            await interaction.response.send_message("不能在他人子区内使用此指令", ephemeral=True)
            return
        
        # 验证父频道是否为论坛频道
        parent = channel.parent
        if not isinstance(parent, discord.ForumChannel):
            await interaction.response.send_message("此功能仅在论坛频道的子区内有效", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # 获取当前子区的应用标签
        current_tags = list(channel.applied_tags)
        
        if action.value == "add":
            # 查找要添加的标签
            target_tag = None
            for available_tag in parent.available_tags:
                if available_tag.name.lower() == tag.lower():
                    target_tag = available_tag
                    break
            
            if not target_tag:
                await interaction.followup.send(f"❌ 标签 **{tag}** 在当前论坛频道中不存在", ephemeral=True)
                return
            
            # 检查标签是否已经存在
            if target_tag in current_tags:
                await interaction.followup.send(f"❌ 标签 **{tag}** 已经存在于当前子区", ephemeral=True)
                return
            
            # 检查标签数量限制（Discord限制为5个）
            if len(current_tags) >= 5:
                await interaction.followup.send("❌ 子区最多只能有5个标签", ephemeral=True)
                return
            
            # 添加标签
            new_tags = current_tags + [target_tag]
            try:
                await channel.edit(applied_tags=new_tags)
                
                # 成功消息
                await interaction.followup.send(f"✅ 已添加标签：**{target_tag.name}**", ephemeral=True)
                
            except discord.HTTPException as e:
                await interaction.followup.send(f"❌ 添加标签失败: {str(e)}", ephemeral=True)
        
        elif action.value == "remove":
            # 查找要移除的标签
            target_tag = None
            for applied_tag in current_tags:
                if applied_tag.name.lower() == tag.lower():
                    target_tag = applied_tag
                    break
            
            if not target_tag:
                await interaction.followup.send(f"❌ 当前子区没有标签 **{tag}**", ephemeral=True)
                return
            
            # 移除标签
            new_tags = [t for t in current_tags if t != target_tag]
            try:
                await channel.edit(applied_tags=new_tags)
                
                # 成功消息
                await interaction.followup.send(f"✅ 已移除标签：**{target_tag.name}**", ephemeral=True)
                
            except discord.HTTPException as e:
                await interaction.followup.send(f"❌ 移除标签失败: {str(e)}", ephemeral=True)

    async def tag_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """为标签参数提供自动补全"""
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            return []
        
        parent = channel.parent
        if not isinstance(parent, discord.ForumChannel):
            return []
        
        # 获取命令参数中的action
        action = None
        for option in interaction.data.get('options', []):
            if option['name'] == '操作':
                action = option['value']
                break
        
        choices = []
        if action == "add":
            # 添加模式：显示还未应用的标签
            current_tag_names = {tag.name for tag in channel.applied_tags}
            available_tags = [
                tag for tag in parent.available_tags 
                if tag.name not in current_tag_names and current.lower() in tag.name.lower()
            ]
            choices = [
                app_commands.Choice(name=tag.name, value=tag.name) 
                for tag in available_tags[:25]  # Discord限制25个选项
            ]
        elif action == "remove":
            # 移除模式：显示已应用的标签
            applied_tags = [
                tag for tag in channel.applied_tags 
                if current.lower() in tag.name.lower()
            ]
            choices = [
                app_commands.Choice(name=tag.name, value=tag.name) 
                for tag in applied_tags[:25]
            ]
        
        return choices

    # 为tag参数添加自动补全
    edit_tag.autocomplete('tag')(tag_autocomplete)

    def _get_mute_record(self, guild_id: int, thread_id: int, user_id: int) -> dict:
        key = (guild_id, thread_id, user_id)
        # 从内存缓存获取或初始化
        record = self._mute_cache.get(key)
        if record is None:
            record = {"muted_until": None, "violations": 0}
            self._mute_cache[key] = record
        return record

    def _save_mute_record(self, guild_id: int, thread_id: int, user_id: int, record: dict):
        # 更新内存缓存
        key = (guild_id, thread_id, user_id)
        self._mute_cache[key] = record
        # 持久化到文件
        data_dir = pathlib.Path("data") / "thread_mute" / str(guild_id) / str(thread_id)
        data_dir.mkdir(parents=True, exist_ok=True)
        file_path = data_dir / f"{user_id}.json"
        if not record:
            if file_path.exists():
                file_path.unlink()
            return
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

    def _parse_time(self, time_str: str) -> tuple[int, str]:
        if time_str.endswith("m"):
            return int(time_str[:-1]) * 60, time_str[:-1] + "分钟"
        elif time_str.endswith("h"):
            return int(time_str[:-1]) * 3600, time_str[:-1] + "小时"
        elif time_str.endswith("d"):
            return int(time_str[:-1]) * 86400, time_str[:-1] + "天"
        else:
            return -1, "未知时间"

    def _is_thread_muted(self, guild_id: int, thread_id: int, user_id: int) -> bool:
        rec = self._get_mute_record(guild_id, thread_id, user_id)
        mu = rec.get("muted_until")
        if mu == -1:
            return True
        if mu:
            until = datetime.fromisoformat(mu)
            if datetime.now() < until:
                return True
            rec["muted_until"] = None
            rec["violations"] = 0
            self._save_mute_record(guild_id, thread_id, user_id, rec)
            return False
        return False

    def _increment_violations(self, guild_id: int, thread_id: int, user_id: int) -> int:
        rec = self._get_mute_record(guild_id, thread_id, user_id)
        rec["violations"] = rec.get("violations", 0) + 1
        self._save_mute_record(guild_id, thread_id, user_id, rec)
        return rec["violations"]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # 机器人消息不处理
        if message.author.bot:
            return
            
        # 只处理子区（Thread）中的消息
        channel = message.channel
        if not isinstance(channel, discord.Thread):
            return
            
        # 检查是否需要自动清理（can_trigger_auto_clear 是纯同步检查，不消耗 API 配额）
        try:
            if self.auto_clear_manager.can_trigger_auto_clear(channel.id):
                success = await self.auto_clear_manager.start_auto_clear(channel)
                if success and self.logger:
                    self.logger.info(f"检测到满员子区，开始自动清理: {channel.name} (ID: {channel.id})")
        except Exception as e:
            if self.logger:
                self.logger.error(f"自动清理检测出错: {e}")
        
        guild = message.guild
        user = message.author
        # 管理组豁免
        try:
            config = getattr(self.bot, 'config', {})
            admin_roles = config.get('admins', [])
            
            for admin_role_id in admin_roles:
                role = guild.get_role(int(admin_role_id))
                if role and role in user.roles:
                    return
        except Exception:
            pass
        # 自己禁言自己
        if user.id == channel.owner_id:
            return
        # 检查是否在子区禁言
        if self._is_thread_muted(guild.id, channel.id, user.id):
            # 删除消息
            try:
                await message.delete()
            except:
                pass
            # 警告用户
            rec = self._get_mute_record(guild.id, channel.id, user.id)
            mu = rec.get('muted_until')
            if mu:
                if mu == -1:
                    warn_text = f"您在子区 {channel.name} 已被永久禁言，请联系子区所有者。"
                else:
                    until = datetime.fromisoformat(mu)
                    remain = until - datetime.now()
                    mins = int(remain.total_seconds() // 60) + 1
                    warn_text = f"您在子区 {channel.name} 已被禁言，还剩 {mins} 分钟解除。请勿发言。"
            else:
                warn_text = f"您在子区 {channel.name} 已被禁言，请联系子区所有者。"
            try:
                # await user.send(warn_text)
                # 使用通知bot发送
                await dm.send_dm(channel.guild, user, warn_text)
            except:
                pass
            # 记录违规并全服禁言
            vcount = self._increment_violations(guild.id, channel.id, user.id)
            secs = 0
            if vcount >= 3:
                secs, label = 10*60, '10分钟'
            if secs > 0:
                try:
                    await user.timeout(timedelta(seconds=secs), reason=f"子区禁言违规({vcount}次)")
                    try:
                        # await user.send(f"因多次违规，您已被全服禁言 {label}")
                        await dm.send_dm(channel.guild, user, f"因多次在被禁言的子区内发言，您已被全服禁言 {label}")
                    except:
                        pass
                except:
                    pass
            return

    # 禁止被禁言的用户添加反应        
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        channel_id = payload.channel_id
        guild_id = payload.guild_id
        user_id = payload.user_id
        if self._is_thread_muted(guild_id, channel_id, user_id):
            message_id = payload.message_id
            try:
                channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                message = await channel.fetch_message(message_id)
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                await message.remove_reaction(payload.emoji, user)
            except Exception as e:
                self.logger.error(f"移除反应失败: {e}")


    @self_manage.command(name="禁言", description="在本子区禁言成员")
    @app_commands.describe(member="要禁言的成员", duration="时长(如10m,1h,1d，可选)", reason="原因(可选)")
    async def mute(self, interaction: discord.Interaction, member: discord.Member, duration: str = None, reason: str = None):

        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        if not await self.can_manage_thread(interaction, channel):
            await interaction.response.send_message("只有子区所有者或管理员可执行此操作", ephemeral=True)
            return
        # 管理组豁免
        try:
            config = getattr(self.bot, 'config', {})
            admin_roles = config.get('admins', [])
            
            for admin_role_id in admin_roles:
                role = interaction.guild.get_role(int(admin_role_id))
                if role and role in member.roles:
                    await interaction.response.send_message("无法禁言管理组成员", ephemeral=True)
                    return
        except Exception:
            pass
        # 自己禁言自己
        if member.id == interaction.user.id:
            await interaction.response.send_message("无法禁言自己", ephemeral=True)
            return
        if duration:
            sec, human = self._parse_time(duration)
            if sec < 0:
                await interaction.response.send_message("❌ 无效时长，请使用m/h/d结尾", ephemeral=True)
                return
            until = datetime.now() + timedelta(seconds=sec)
            muted_until = until.isoformat()
        else:
            muted_until = -1 # 永久禁言
        # 子区内公示
        embed = discord.Embed(
            title="🔒 子区禁言",
            description=f"👤 {member.mention} 已被禁言",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        embed.add_field(name="原因", value=reason if reason else "无", inline=True)
        embed.add_field(name="时长", value=duration if duration else "永久", inline=True)
        embed.add_field(name="执行者", value=interaction.user.mention, inline=True)
        await channel.send(embed=embed)

        rec = self._get_mute_record(channel.guild.id, channel.id, member.id)
        rec['muted_until'] = muted_until
        rec['violations'] = 0
        self._save_mute_record(channel.guild.id, channel.id, member.id, rec)
        msg = f"✅ 已在子区禁言 {member.mention}"
        if duration:
            msg += f" 持续 {human}"
        await interaction.response.send_message(msg, ephemeral=True)
        

    @self_manage.command(name="解除禁言", description="在本子区解除禁言成员")
    @app_commands.describe(member="要解除禁言的成员")
    async def unmute(self, interaction: discord.Interaction, member: discord.Member):

        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        if not await self.can_manage_thread(interaction, channel):
            await interaction.response.send_message("只有子区所有者或管理员可执行此操作", ephemeral=True)
            return
        data_dir = pathlib.Path("data") / "thread_mute" / str(channel.guild.id) / str(channel.id)
        data_dir.mkdir(parents=True, exist_ok=True)
        file_path = data_dir / f"{member.id}.json"
        if file_path.exists():
            file_path.unlink()
            # 清理缓存
            key = (channel.guild.id, channel.id, member.id)
            self._mute_cache.pop(key, None)
            self._save_mute_record(channel.guild.id, channel.id, member.id, None)
            # 子区内公示
            embed = discord.Embed(
                title="🔒 子区禁言",
                description=f"👤 {member.mention} 已被解除禁言",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            await channel.send(embed=embed)
            await interaction.response.send_message(f"✅ 已解除 {member.mention} 的子区禁言", ephemeral=True)
        else:
            await interaction.response.send_message("该成员未被禁言", ephemeral=True)
        

    @self_manage.command(name="自动清理", description="开启或关闭子区的自动清理功能")
    @app_commands.describe(action="选择操作")
    @app_commands.rename(action="操作")
    @app_commands.choices(action=[
        app_commands.Choice(name="🟢 开启自动清理", value="enable"),
        app_commands.Choice(name="🔴 关闭自动清理", value="disable"),
        app_commands.Choice(name="📊 查看状态", value="status"),
    ])
    async def auto_clear_control(self, interaction: discord.Interaction, action: app_commands.Choice[str]):
        # 验证是否在子区内
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        
        # 验证是否是子区所有者或管理员
        if not await self.can_manage_thread(interaction, channel):
            await interaction.response.send_message("只有子区所有者或管理员可以执行此操作", ephemeral=True)
            return

        thread_id = channel.id
        is_disabled = self.auto_clear_manager.is_thread_disabled(thread_id)
        
        if action.value == "enable":
            if not is_disabled:
                await interaction.response.send_message("❓ 该子区的自动清理功能已经开启", ephemeral=True)
                return
                
            self.auto_clear_manager.enable_thread(thread_id)
            embed = discord.Embed(
                title="✅ 自动清理已开启",
                description=(
                    f"已为子区 **{channel.name}** 开启自动清理功能\n\n"
                    "ℹ️ **功能说明：**\n"
                    "• 当子区人数达到 1000 人时自动触发清理\n"
                    "• 每次清理大约 50 名不活跃成员\n"
                    "• 清理进度会在日志频道实时显示"
                ),
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        elif action.value == "disable":
            if is_disabled:
                await interaction.response.send_message("❓ 该子区的自动清理功能已经关闭", ephemeral=True)
                return
                
            self.auto_clear_manager.disable_thread(thread_id)
            embed = discord.Embed(
                title="🔴 自动清理已关闭",
                description=f"已为子区 **{channel.name}** 关闭自动清理功能\n\n该子区将不会再自动执行清理任务",
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        elif action.value == "status":
            # 获取当前成员数
            try:
                members = await channel.fetch_members()
                member_count = len(members)
            except Exception:
                member_count = "未知"
            
            # 检查是否有正在进行的任务
            has_active_task = self.auto_clear_manager.is_clearing_active(thread_id)
            
            status_text = "🟢 已开启" if not is_disabled else "🔴 已关闭"
            task_text = "✅ 有正在进行的清理任务" if has_active_task else "⭕ 暂无清理任务"
            
            embed = discord.Embed(
                title="📊 自动清理状态",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            embed.add_field(name="子区名称", value=channel.name, inline=True)
            embed.add_field(name="当前成员数", value=str(member_count), inline=True)
            embed.add_field(name="自动清理状态", value=status_text, inline=True)
            embed.add_field(name="任务状态", value=task_text, inline=False)
            
            if not is_disabled:
                embed.add_field(
                    name="ℹ️ 说明",
                    value="当成员数达到 1000 人时将自动清理约 50 名不活跃成员",
                    inline=False
                )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @self_manage.command(name="授权协管", description="授予成员当前子区的协管权限")
    @app_commands.describe(member="要授予协管权限的成员")
    @app_commands.rename(member="成员")
    async def add_thread_delegate(self, interaction: discord.Interaction, member: discord.Member):
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return

        if not await self.can_manage_delegate_settings(interaction, channel):
            await interaction.response.send_message("只有子区所有者或管理员可以管理协管权限", ephemeral=True)
            return

        if member.bot:
            await interaction.response.send_message("❌ 不能将机器人设为协管", ephemeral=True)
            return

        if member.id == channel.owner_id:
            await interaction.response.send_message("❌ 该成员已经是子区所有者，无需重复授权", ephemeral=True)
            return

        delegates = self._load_thread_delegates(channel.guild.id, channel.id)
        if member.id in delegates:
            await interaction.response.send_message(f"❌ {member.mention} 已经是本子区协管", ephemeral=True)
            return

        delegates.add(member.id)
        try:
            self._save_thread_delegates(channel.guild.id, channel.id, delegates)
        except Exception as e:
            await interaction.response.send_message(f"❌ 保存协管配置失败: {str(e)}", ephemeral=True)
            return

        await interaction.response.send_message(f"✅ 已授予 {member.mention} 本子区协管权限", ephemeral=True)
        try:
            await channel.send(f"👥 {member.mention} 已被 {interaction.user.mention} 设为本子区协管，可使用大部分自助管理指令。")
        except discord.HTTPException:
            pass

    @self_manage.command(name="移除协管", description="移除成员当前子区的协管权限")
    @app_commands.describe(member="要移除协管权限的成员（在群内时优先使用）", user_id="要移除协管权限的成员ID（用于已退群成员）")
    @app_commands.rename(member="成员", user_id="成员id")
    async def remove_thread_delegate(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
        user_id: Optional[str] = None
    ):
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return

        if not await self.can_manage_delegate_settings(interaction, channel):
            await interaction.response.send_message("只有子区所有者或管理员可以管理协管权限", ephemeral=True)
            return

        if member is None and not user_id:
            await interaction.response.send_message("❌ 请提供要移除的成员或成员ID", ephemeral=True)
            return

        if member is not None:
            target_id = member.id
            target_display = member.mention
        else:
            cleaned = user_id.strip()
            mention_match = re.fullmatch(r"<@!?(\d+)>", cleaned)
            if mention_match:
                target_id = int(mention_match.group(1))
            elif cleaned.isdigit():
                target_id = int(cleaned)
            else:
                await interaction.response.send_message("❌ 请提供有效的成员ID或@成员格式", ephemeral=True)
                return

            target_member = interaction.guild.get_member(target_id)
            target_display = target_member.mention if target_member else f"<@{target_id}>"

        if target_id == channel.owner_id:
            await interaction.response.send_message("❌ 不能移除子区所有者的权限", ephemeral=True)
            return

        delegates = self._load_thread_delegates(channel.guild.id, channel.id)
        if target_id not in delegates:
            await interaction.response.send_message(f"❌ {target_display} 不是本子区协管", ephemeral=True)
            return

        delegates.remove(target_id)
        try:
            self._save_thread_delegates(channel.guild.id, channel.id, delegates)
        except Exception as e:
            await interaction.response.send_message(f"❌ 保存协管配置失败: {str(e)}", ephemeral=True)
            return

        await interaction.response.send_message(f"✅ 已移除 {target_display} 的本子区协管权限", ephemeral=True)
        try:
            await channel.send(f"👥 {target_display} 已被 {interaction.user.mention} 移出本子区协管名单。")
        except discord.HTTPException:
            pass

    @self_manage.command(name="协管列表", description="查看当前子区的协管成员")
    async def list_thread_delegates(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return

        if not await self.can_manage_thread(interaction, channel):
            await interaction.response.send_message("只有子区所有者、协管或管理员可以查看协管列表", ephemeral=True)
            return

        delegates = sorted(self._load_thread_delegates(channel.guild.id, channel.id))

        embed = discord.Embed(
            title="👥 子区协管列表",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed.add_field(name="子区所有者", value=f"<@{channel.owner_id}>", inline=False)

        if delegates:
            embed.add_field(
                name=f"协管成员（{len(delegates)}）",
                value="\n".join(f"<@{user_id}>" for user_id in delegates),
                inline=False
            )
        else:
            embed.add_field(name="协管成员", value="当前没有协管", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ThreadSelfManage(bot))

