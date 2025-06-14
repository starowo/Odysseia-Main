import asyncio
import json
import pathlib
import discord
from discord.ext import commands
from discord import app_commands
from src.utils.confirm_view import confirm_view
from src.thread_manage.thread_clear import clear_thread_members
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

    self_manage = app_commands.Group(name="自助管理", description="在贴内进行权限操作，仅在自己子贴内有效")

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

    @self_manage.command(name="清理子区", description="清理子区内不活跃成员")
    @app_commands.describe(threshold="阈值(默认900，最低800)")
    @app_commands.rename(threshold="阈值")
    async def clear_thread(self, interaction: discord.Interaction, threshold: app_commands.Range[int, 800, 1000]=900):
        # 获取当前子区
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        
        if not interaction.user.id == channel.owner_id:
            await interaction.response.send_message("不能在他人子区内使用此指令", ephemeral=True)
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
        
        # 验证是否是子区所有者
        if not interaction.user.id == channel.owner_id:
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
            await message.clear_reactions()
            await interaction.edit_original_response(content="已删除消息的所有反应")
            return
        
        # 删除指定反应
        try:
            await message.clear_reaction(reaction)
            await interaction.edit_original_response(content=f"已删除消息的 {reaction} 反应")
        except discord.HTTPException:
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
        
        # 验证是否是子区所有者
        if not interaction.user.id == channel.owner_id:
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

        # 验证是否有权限删除（只能删除自己的消息或者子区内的所有消息）
        if message.author.id != interaction.user.id and not channel.owner_id == interaction.user.id:
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

    # ---- 删除整个子区 ----
    @self_manage.command(name="删帖", description="删除整个子区")
    async def delete_thread(self, interaction: discord.Interaction):
        # 验证是否在子区内
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        
        # 验证是否是子区所有者
        if not interaction.user.id == channel.owner_id:
            await interaction.response.send_message("不能在他人子区内使用此指令", ephemeral=True)
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
            # delete message
            await interaction.delete_original_response()
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
        
        # 验证是否是子区所有者
        if not interaction.user.id == channel.owner_id:
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
            await channel.edit(locked=True, archived=False)
            
            # 发送公告消息
            lock_notice = f"🔒 **子区已锁定**"
            if reason:
                lock_notice += f"\n\n**原因：** {reason}"
            lock_notice += f"\n\n由 {interaction.user.mention} 锁定于 {discord.utils.format_dt(datetime.now())}"
            
            # 在子区内发送锁定通知
            await channel.send(lock_notice)
            
            # 通知操作者
            await interaction.followup.send("✅ 子区已锁定", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ 锁定失败: {str(e)}", ephemeral=True)

    # ---- 解锁子区 ----
    @self_manage.command(name="解锁子区", description="解锁子区，允许发言")
    @app_commands.describe(thread="要解锁的子区（留空则为当前子区）")
    async def unlock_thread(self, interaction: discord.Interaction, thread: discord.Thread = None):
        # 验证是否在子区内
        channel = thread or interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        
        # 验证是否是子区所有者
        if not interaction.user.id == channel.owner_id:
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
    @app_commands.choices(option=[
        app_commands.Choice(name="无", value=0),
        app_commands.Choice(name="5秒", value=5),
        app_commands.Choice(name="10秒", value=10),
        app_commands.Choice(name="15秒", value=15),
        app_commands.Choice(name="30秒", value=30),
        app_commands.Choice(name="1分钟", value=60),
    ])
    async def set_slowmode(self, interaction: discord.Interaction, option: app_commands.Choice[int]):
        # 验证是否在子区内
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        
        # 验证是否是子区所有者
        if not interaction.user.id == channel.owner_id:
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
        
        # 验证是否是子区所有者
        if not interaction.user.id == channel.owner_id:
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
            return datetime.now() < until
        return False

    def _increment_violations(self, guild_id: int, thread_id: int, user_id: int) -> int:
        rec = self._get_mute_record(guild_id, thread_id, user_id)
        rec["violations"] = rec.get("violations", 0) + 1
        self._save_mute_record(guild_id, thread_id, user_id, rec)
        return rec["violations"]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        # 只处理子区（Thread）中的消息
        channel = message.channel
        if not isinstance(channel, discord.Thread):
            return
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
                await user.send(warn_text)
            except:
                pass
            # 记录违规并全服禁言
            vcount = self._increment_violations(guild.id, channel.id, user.id)
            secs = 0
            if vcount == 3:
                secs, label = 10*60, '10分钟'
            elif vcount == 4:
                secs, label = 60*60, '1小时'
            elif vcount >= 5:
                secs, label = 24*3600, '1天'
            if secs > 0:
                try:
                    await user.timeout(timedelta(seconds=secs), reason=f"子区禁言违规({vcount}次)")
                    try:
                        await user.send(f"因多次违规，您已被全服禁言 {label}")
                    except:
                        pass
                except:
                    pass
            return

    @self_manage.command(name="禁言", description="在本子区禁言成员")
    @app_commands.describe(member="要禁言的成员", duration="时长(如10m,1h,1d，可选)", reason="原因(可选)")
    async def mute(self, interaction: discord.Interaction, member: discord.Member, duration: str = None, reason: str = None):
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        if interaction.user.id != channel.owner_id:
            await interaction.response.send_message("只有子区所有者可执行此操作", ephemeral=True)
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
        rec = self._get_mute_record(channel.guild.id, channel.id, member.id)
        rec['muted_until'] = muted_until
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
        if interaction.user.id != channel.owner_id:
            await interaction.response.send_message("只有子区所有者可执行此操作", ephemeral=True)
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
            await interaction.response.send_message(f"✅ 已解除 {member.mention} 的子区禁言", ephemeral=True)
        else:
            await interaction.response.send_message("该成员未被禁言", ephemeral=True)
