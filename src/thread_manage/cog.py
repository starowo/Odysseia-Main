#src\thread_manage\cog.py
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
from src.utils.confirm_view import confirm_view
from src.thread_manage.thread_clear import clear_thread_members
from typing import Optional
import re
from datetime import datetime
import traceback

class ThreadSelfManage(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger
        self.name = "自助管理"

    self_manage = app_commands.Group(name="自助管理", description="在贴内进行权限操作，仅在自己子贴内有效")

    @commands.Cog.listener()
    async def on_ready(self):
        if self.logger:
            self.logger.info("自助管理指令加载成功")

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
            colour=discord.Colour.red(), # 将颜色改为红色以强调危险操作
            timeout=60,
        )

        if not confirmed:
            await interaction.delete_original_response() # 如果用户取消，删除确认消息
            return

        # ── 进行清理，实时更新进度 ──────────────────────────────

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
        # 不再发送第二个消息，因为 edit_original_response 已经更新了
        # await interaction.followup.send("✅ 子区清理完成", embed=final_embed, ephemeral=False)

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
            await interaction.edit_original_response(content="找不到指定的消息，请确认消息ID是否正确。", ephemeral=True)
            return

        # 验证是否有权限删除（只能删除自己的消息或者子区内的所有消息）
        # Discord bot 自身需要有 manage_messages 权限才能删除他人消息
        if message.author.id != interaction.user.id and not channel.owner_id == interaction.user.id:
            # 如果不是自己的消息，也不是子区所有者，并且机器人也没有管理消息权限，则不允许
            if not channel.permissions_for(self.bot.user).manage_messages:
                await interaction.edit_original_response("你只能删除自己的消息，或机器人没有管理消息权限无法删除他人的消息。", ephemeral=True)
                return
            # 如果机器人有管理消息权限，但操作者不是子区所有者，理论上可以删除，但为了安全，限制为只有子区所有者可以删除他人消息
            if not interaction.user.id == channel.owner_id:
                await interaction.edit_original_response("你不是子区所有者，无法删除他人的消息。", ephemeral=True)
                return

        # 删除消息
        try:
            await message.delete()
            await interaction.edit_original_response(
                content="✅ 消息已删除", embed=None, view=None
            )
        except discord.Forbidden:
            await interaction.edit_original_response(
                content="❌ 删除失败: 机器人无权限删除此消息。", embed=None, view=None
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
            timeout=30 # 缩短超时时间
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
            # 由于线程被删除，原有的 ephemeral response 可能无法更新
            # 如果需要确认，可以在父频道发送一个确认消息
            # 但通常 ephemeral response 即使在线程删除后也能显示
            # 这里依赖于 ephemeral response 的持久性
        except discord.Forbidden:
            embed = discord.Embed(
                title=f"❌ 删除失败",
                description=f"机器人无权限删除此子区，请检查权限。",
                color=discord.Color.red()
            )
            await interaction.edit_original_response(embed=embed, view=None)
        except discord.HTTPException as e:
            # beautiful embed for error
            embed = discord.Embed(
                title=f"❌ 删除失败",
                description=f"```{str(e)}```",
                color=discord.Color.red()
            )
            await interaction.edit_original_response(embed=embed, view=None)
        except Exception as e:
            self.logger.error(f"删除子区时出错: {traceback.format_exc()}")
            embed = discord.Embed(
                title=f"❌ 删除失败",
                description=f"发生未知错误: {str(e)}",
                color=discord.Color.red()
            )
            await interaction.edit_original_response(embed=embed, view=None)


    # ---- 锁定和关闭子区 ----
    @self_manage.command(name="锁定子区", description="锁定子区，禁止发言")
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
            timeout=30 # 缩短超时时间
        )

        if not confirmed:
            await interaction.delete_original_response()
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
        except discord.Forbidden:
            await interaction.followup.send("❌ 锁定失败: 机器人无权限锁定此子区。", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ 锁定失败: {str(e)}", ephemeral=True)
        except Exception as e:
            self.logger.error(f"锁定子区时出错: {traceback.format_exc()}")
            await interaction.followup.send(f"❌ 锁定失败: {str(e)}", ephemeral=True)

    # ---- 解锁子区 ----
    @self_manage.command(name="解锁子区", description="解锁子区，允许发言")
    async def unlock_thread(self, interaction: discord.Interaction):
        # 验证是否在子区内
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message("此指令仅在子区内有效", ephemeral=True)
            return
        
        # 验证是否是子区所有者
        if not interaction.user.id == channel.owner_id:
            await interaction.response.send_message("不能在他人子区内使用此指令", ephemeral=True)
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
        except discord.Forbidden:
            await interaction.followup.send("❌ 解锁失败: 机器人无权限解锁此子区。", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ 解锁失败: {str(e)}", ephemeral=True)
        except Exception as e:
            self.logger.error(f"解锁子区时出错: {traceback.format_exc()}")
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
        app_commands.Choice(name="5分钟", value=300), # 新增
        app_commands.Choice(name="10分钟", value=600), # 新增
        app_commands.Choice(name="15分钟", value=900), # 新增
        app_commands.Choice(name="30分钟", value=1800), # 新增
        app_commands.Choice(name="1小时", value=3600), # 新增
        app_commands.Choice(name="2小时", value=7200), # 新增
        app_commands.Choice(name="6小时", value=21600), # 新增
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
        except discord.Forbidden:
            await interaction.followup.send("❌ 设置失败: 机器人无权限设置慢速模式。", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ 设置失败: {str(e)}", ephemeral=True)
        except Exception as e:
            self.logger.error(f"设置慢速模式时出错: {traceback.format_exc()}")
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
            await interaction.response.send_message("找不到指定的消息，请确认消息ID是否正确。", ephemeral=True)
            return

        # 检查机器人是否有权限管理消息（置顶/取消置顶需要此权限）
        if not channel.permissions_for(self.bot.user).manage_messages:
            await interaction.response.send_message("❌ 机器人无 '管理消息' 权限，无法执行此操作。", ephemeral=True)
            return

        # 执行操作
        if action.value == "pin":
            # 检查是否已经置顶
            if message.pinned:
                await interaction.response.send_message("此消息已经被标注。", ephemeral=True)
                return
                
            # 置顶消息
            try:
                await message.pin(reason=f"由 {interaction.user} 标注")
                await interaction.response.send_message("✅ 消息已标注", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ 标注失败: 机器人无权限置顶此消息。", ephemeral=True)
            except discord.HTTPException as e:
                await interaction.response.send_message(f"❌ 标注失败: {str(e)}", ephemeral=True)
            except Exception as e:
                self.logger.error(f"标注消息时出错: {traceback.format_exc()}")
                await interaction.response.send_message(f"❌ 标注失败: {str(e)}", ephemeral=True)
        
        elif action.value == "unpin":
            # 检查是否已经置顶
            if not message.pinned:
                await interaction.response.send_message("此消息未被标注。", ephemeral=True)
                return
                
            # 取消置顶
            try:
                await message.unpin(reason=f"由 {interaction.user} 取消标注")
                await interaction.response.send_message("✅ 已取消标注", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ 取消标注失败: 机器人无权限取消置顶此消息。", ephemeral=True)
            except discord.HTTPException as e:
                await interaction.response.send_message(f"❌ 取消标注失败: {str(e)}", ephemeral=True)
            except Exception as e:
                self.logger.error(f"取消标注消息时出错: {traceback.format_exc()}")
                await interaction.response.send_message(f"❌ 取消标注失败: {str(e)}", ephemeral=True)

# 每个 Cog 模块都需要一个 setup 函数，供 discord.py 加载扩展时调用
async def setup(bot: commands.Bot):
    await bot.add_cog(ThreadSelfManage(bot))

