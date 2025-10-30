"""
轮换通知UI组件
包含申请按钮、申请表单、审核界面等UI组件
"""

import discord
from discord import ui
from typing import Optional
import uuid
import datetime
from src.utils import dm

from src.banner.database import BannerDatabase, BannerApplication, ApplicationStatus
from src.utils.config_helper import get_config_value


async def _send_audit_log(guild: discord.Guild, application: BannerApplication, 
                         action: str, reviewer: discord.Member, 
                         reason: Optional[str] = None) -> bool:
    """发送审核记录到配置的频道或线程"""
    try:
        # 获取配置
        config = get_config_value("banner_application", guild.id, {})
        audit_channel_id = config.get("audit_channel_id")
        audit_thread_id = config.get("audit_thread_id")
        
        if not audit_channel_id:
            return False
        
        # 确定目标位置
        target = None
        if audit_thread_id:
            # 尝试获取线程
            try:
                target = guild.get_thread(audit_thread_id)
                if not target:
                    # 线程可能不在缓存中，尝试从频道获取
                    target = guild.fetch_channel(audit_thread_id)
            except:
                pass
        
        if not target:
            # 使用频道
            target = guild.get_channel_or_thread(audit_channel_id)
        
        if not target:
            return False
        
        # 创建审核记录嵌入
        color_map = {
            "通过": discord.Color.green(),
            "拒绝": discord.Color.red(),
            "提交": discord.Color.blue(),
            "晋升": discord.Color.orange(),
            "过期": discord.Color.dark_grey()
        }
        
        embed = discord.Embed(
            title=f"📝 轮换通知申请{action}记录",
            color=color_map.get(action, discord.Color.blue()),
            timestamp=datetime.datetime.utcnow()
        )
        
        embed.add_field(name="申请ID", value=f"`{application.id}`", inline=True)
        embed.add_field(name="申请者", value=f"<@{application.applicant_id}>", inline=True)
        embed.add_field(name="审核员", value=reviewer.mention, inline=True)
        
        embed.add_field(name="标题", value=application.title, inline=False)
        embed.add_field(name="位置", value=application.location, inline=True)
        
        if application.description:
            embed.add_field(name="内容", value=application.description, inline=False)
        
        if reason:
            embed.add_field(name="理由", value=reason, inline=False)
        
        # 添加申请时间
        try:
            created_time = datetime.datetime.fromisoformat(application.created_at)
            embed.add_field(
                name="申请时间", 
                value=created_time.strftime("%Y-%m-%d %H:%M:%S UTC"), 
                inline=True
            )
        except:
            pass
        
        if application.cover_image:
            embed.set_thumbnail(url=application.cover_image)
        
        embed.set_footer(text=f"审核系统 | Odysseia Bot")
        
        await target.send(embed=embed)
        return True
        
    except Exception as e:
        print(f"发送审核记录失败: {e}")
        return False


class ApplicationButton(ui.View):
    """申请按钮视图"""
    
    def __init__(self):
        super().__init__(timeout=None)
        self.db = BannerDatabase()
    
    @ui.button(
        label="申请banner", 
        style=discord.ButtonStyle.primary, 
        emoji="📝",
        custom_id="banner_application_button"
    )
    async def apply_button(self, interaction: discord.Interaction, button: ui.Button):
        """处理申请按钮点击"""
        if not interaction.guild:
            await interaction.response.send_message("❌ 此功能只能在服务器中使用", ephemeral=True)
            return
        
        # 获取配置
        config = get_config_value("banner_application", interaction.guild.id, {})
        applicant_role_id = config.get("applicant_role_id")
        max_applications_per_user = config.get("max_applications_per_user", 1)
        max_active_banners = config.get("max_active_banners", 30)
        max_waitlist = config.get("max_waitlist", 30)
        
        # 检查申请身份组权限
        if applicant_role_id:
            if (not any(role.id == applicant_role_id for role in interaction.user.roles)) and (not interaction.user.guild_permissions.administrator):
                await interaction.response.send_message("❌ 您没有权限申请轮换通知", ephemeral=True)
                return
        
        # 检查用户申请数量限制
        user_count = self.db.get_user_application_count(interaction.guild.id, interaction.user.id)
        if user_count >= max_applications_per_user:
            await interaction.response.send_message(
                f"❌ 您已达到申请数量上限（{max_applications_per_user}个）", 
                ephemeral=True
            )
            return
        
        # 检查当前申请数量和等待列表
        db_config = self.db.load_config(interaction.guild.id)
        active_count = len([item for item in db_config.items if item.application_id])
        pending_count = len([app for app in db_config.applications if app.status == ApplicationStatus.PENDING])
        waitlist_count = len(db_config.waitlist)
        
        # 检查是否可以申请
        if active_count + pending_count >= max_active_banners and waitlist_count >= max_waitlist:
            await interaction.response.send_message(
                "❌ 申请窗口已暂停，请稍后再试", 
                ephemeral=True
            )
            return
        
        # 显示申请表单
        modal = ApplicationModal()
        await interaction.response.send_modal(modal)


class ApplicationModal(ui.Modal):
    """申请表单"""
    
    def __init__(self):
        super().__init__(title="轮换通知申请", timeout=300)
        self.db = BannerDatabase()
    
    title_input = ui.TextInput(
        label="标题",
        placeholder="请输入轮换通知的标题...",
        max_length=100,
        required=True
    )
    
    description_input = ui.TextInput(
        label="内容（可选）",
        placeholder="请输入轮换通知的详细内容...",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=False
    )
    
    location_input = ui.TextInput(
        label="位置",
        placeholder="请输入位置（通常是跳转链接）...",
        max_length=100,
        required=True
    )
    
    image_input = ui.TextInput(
        label="图片链接（可选）",
        placeholder="请输入图片URL链接...",
        max_length=500,
        required=False
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        """处理表单提交"""
        if not interaction.guild:
            await interaction.response.send_message("❌ 此功能只能在服务器中使用", ephemeral=True)
            return
        
        # 获取配置
        config = get_config_value("banner_application", interaction.guild.id, {})
        review_channel_id = config.get("review_channel_id")
        max_active_banners = config.get("max_active_banners", 30)
        
        if not review_channel_id:
            await interaction.response.send_message("❌ 审核频道未配置", ephemeral=True)
            return
        
        # 创建申请
        app_id = f"app_{uuid.uuid4().hex[:8]}"
        application = BannerApplication(
            id=app_id,
            applicant_id=interaction.user.id,
            applicant_name=str(interaction.user),
            title=self.title_input.value,
            description=self.description_input.value or "",
            location=self.location_input.value,
            cover_image=self.image_input.value if self.image_input.value else None
        )
        
        # 检查是否应该进入等待列表
        db_config = self.db.load_config(interaction.guild.id)
        active_count = len([item for item in db_config.items if item.application_id])
        pending_count = len([app for app in db_config.applications if app.status == ApplicationStatus.PENDING])
        
        if active_count + pending_count >= max_active_banners:
            # 进入等待列表
            application.status = ApplicationStatus.WAITLISTED
            db_config.waitlist.append(application)
            self.db.save_config(db_config)
            
            await interaction.response.send_message(
                f"✅ 申请已提交并进入等待列表\n"
                f"**申请ID**: `{app_id}`\n"
                f"**标题**: {application.title}\n"
                f"**位置**: {application.location}\n"
                f"**等待位置**: {len(db_config.waitlist)}",
                ephemeral=True
            )
        else:
            # 添加到申请列表
            if not self.db.add_application(interaction.guild.id, application):
                await interaction.response.send_message("❌ 申请提交失败", ephemeral=True)
                return
            
            # 发送到审核频道
            review_channel = interaction.guild.get_channel_or_thread(review_channel_id)
            if not review_channel:
                await interaction.response.send_message("❌ 审核频道无效", ephemeral=True)
                return
            
            # 创建审核嵌入消息
            embed = discord.Embed(
                title="🆕 轮换通知申请",
                description=f"申请ID: `{app_id}`",
                color=discord.Color.blue(),
                timestamp=datetime.datetime.utcnow()
            )
            embed.add_field(name="📝 标题", value=application.title, inline=False)
            embed.add_field(name="📍 位置", value=application.location, inline=True)
            embed.add_field(name="👤 申请者", value=interaction.user.mention, inline=True)
            
            if application.description:
                embed.add_field(name="📄 内容", value=application.description, inline=False)
            
            if application.cover_image:
                embed.set_image(url=application.cover_image)
            
            # 发送审核视图
            view = ReviewView(app_id)
            await review_channel.send(embed=embed, view=view)
            
            # 记录申请提交到审核日志
            await _send_audit_log(
                interaction.guild,
                application,
                "提交",
                interaction.user
            )
            
            await interaction.response.send_message(
                f"✅ 申请已提交，等待审核\n"
                f"**申请ID**: `{app_id}`\n"
                f"**标题**: {application.title}\n"
                f"**位置**: {application.location}",
                ephemeral=True
            )


class ReviewView(ui.View):
    """审核视图"""
    
    def __init__(self, application_id: str):
        super().__init__(timeout=None)
        self.application_id = application_id
        self.db = BannerDatabase()
    
    @ui.button(label="通过", style=discord.ButtonStyle.success, emoji="✅")
    async def approve_button(self, interaction: discord.Interaction, button: ui.Button):
        """通过申请"""
        if not interaction.guild:
            await interaction.response.send_message("❌ 此功能只能在服务器中使用", ephemeral=True)
            return
        
        # 检查审核权限
        config = get_config_value("banner_application", interaction.guild.id, {})
        reviewer_role_ids = config.get("reviewer_role_ids", [])
        admin_roles = get_config_value("admins", interaction.guild.id, [])
        senior_admin_roles = get_config_value("senior_admins", interaction.guild.id, [])
        
        reviewer_role_ids.extend(admin_roles)
        reviewer_role_ids.extend(senior_admin_roles)
        if reviewer_role_ids:
            has_permission = any(role.id in reviewer_role_ids for role in interaction.user.roles)
            if not has_permission and not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("❌ 您没有权限审核申请", ephemeral=True)
                return
        
        # 获取申请
        application = self.db.get_application(interaction.guild.id, self.application_id)
        if not application:
            await interaction.response.send_message("❌ 申请不存在", ephemeral=True)
            return
        
        if application.status != ApplicationStatus.PENDING:
            await interaction.response.send_message("❌ 申请已被处理", ephemeral=True)
            return
        
        # 通过申请
        duration_days = config.get("banner_duration_days", 7)
        if not self.db.approve_application(interaction.guild.id, self.application_id, duration_days):
            await interaction.response.send_message("❌ 通过申请失败", ephemeral=True)
            return
        
        # 更新申请状态
        self.db.update_application_status(
            interaction.guild.id, 
            self.application_id, 
            ApplicationStatus.APPROVED,
            interaction.user.id,
            str(interaction.user)
        )
        
        # 禁用按钮
        for item in self.children:
            item.disabled = True
        
        # 创建结果嵌入
        embed = discord.Embed(
            title="✅ 申请已通过",
            description=f"申请ID: `{self.application_id}`",
            color=discord.Color.green(),
            timestamp=datetime.datetime.utcnow()
        )
        embed.add_field(name="审核员", value=interaction.user.mention, inline=True)
        embed.add_field(name="持续时间", value=f"{duration_days}天", inline=True)
        
        await interaction.response.edit_message(embed=embed, view=self)
        
        # 发送审核记录
        await _send_audit_log(
            interaction.guild,
            application,
            "通过", 
            interaction.user
        )
        
        # 私信通知申请者
        try:
            applicant = interaction.guild.get_member(application.applicant_id)
            if applicant:
                await dm.send_dm(applicant.guild, applicant, embed=discord.Embed(title="🎉 您的轮换通知申请已通过！", description=f"申请ID: `{self.application_id}`\n标题: {application.title}\n审核员: {interaction.user}\n持续时间: {duration_days}天"))
        except:
            pass  # 忽略私信失败
    
    @ui.button(label="拒绝", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject_button(self, interaction: discord.Interaction, button: ui.Button):
        """拒绝申请"""
        if not interaction.guild:
            await interaction.response.send_message("❌ 此功能只能在服务器中使用", ephemeral=True)
            return
        
        # 检查审核权限
        config = get_config_value("banner_application", interaction.guild.id, {})
        reviewer_role_ids = config.get("reviewer_role_ids", [])
        
        if reviewer_role_ids:
            has_permission = any(role.id in reviewer_role_ids for role in interaction.user.roles)
            if not has_permission:
                await interaction.response.send_message("❌ 您没有权限审核申请", ephemeral=True)
                return
        
        # 获取申请
        application = self.db.get_application(interaction.guild.id, self.application_id)
        if not application:
            await interaction.response.send_message("❌ 申请不存在", ephemeral=True)
            return
        
        if application.status != ApplicationStatus.PENDING:
            await interaction.response.send_message("❌ 申请已被处理", ephemeral=True)
            return
        
        # 显示拒绝理由表单
        modal = RejectModal(self.application_id, interaction.user)
        await interaction.response.send_modal(modal)


class RejectModal(ui.Modal):
    """拒绝理由表单"""
    
    def __init__(self, application_id: str, reviewer: discord.Member):
        super().__init__(title="拒绝申请", timeout=300)
        self.application_id = application_id
        self.reviewer = reviewer
        self.db = BannerDatabase()
    
    reason_input = ui.TextInput(
        label="拒绝理由",
        placeholder="请输入拒绝申请的理由...",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        """处理拒绝理由提交"""
        if not interaction.guild:
            await interaction.response.send_message("❌ 此功能只能在服务器中使用", ephemeral=True)
            return
        
        # 获取申请
        application = self.db.get_application(interaction.guild.id, self.application_id)
        if not application:
            await interaction.response.send_message("❌ 申请不存在", ephemeral=True)
            return
        
        # 更新申请状态
        if not self.db.update_application_status(
            interaction.guild.id,
            self.application_id,
            ApplicationStatus.REJECTED,
            self.reviewer.id,
            str(self.reviewer),
            self.reason_input.value
        ):
            await interaction.response.send_message("❌ 拒绝申请失败", ephemeral=True)
            return
        
        # 创建结果嵌入
        embed = discord.Embed(
            title="❌ 申请已拒绝",
            description=f"申请ID: `{self.application_id}`",
            color=discord.Color.red(),
            timestamp=datetime.datetime.utcnow()
        )
        embed.add_field(name="审核员", value=self.reviewer.mention, inline=True)
        embed.add_field(name="拒绝理由", value=self.reason_input.value, inline=False)
        
        # 从等待列表晋升一个申请
        promoted = self.db.promote_from_waitlist(interaction.guild.id, 1)
        
        # 发送审核记录
        await _send_audit_log(
            interaction.guild,
            application,
            "拒绝",
            self.reviewer,
            self.reason_input.value
        )
        
        await interaction.response.send_message(
            f"✅ 已拒绝申请 `{self.application_id}`",
            ephemeral=True
        )
        
        # 更新原消息
        try:
            # 获取原消息并更新
            message = interaction.message
            if message:
                # 禁用按钮
                view = ReviewView(self.application_id)
                for item in view.children:
                    item.disabled = True
                
                await message.edit(embed=embed, view=view)
        except:
            pass  # 忽略更新失败
        
        # 私信通知申请者
        try:
            applicant = interaction.guild.get_member(application.applicant_id)
            if applicant:
                await dm.send_dm(applicant.guild, applicant, embed=discord.Embed(title="❌ 您的轮换通知申请已被拒绝", description=f"申请ID: `{self.application_id}`\n标题: {application.title}\n审核员: {self.reviewer}\n拒绝理由: {self.reason_input.value}"))
        except:
            pass  # 忽略私信失败
        
        # 如果有申请从等待列表晋升，发送新的审核消息
        if promoted:
            config = get_config_value("banner_application", interaction.guild.id, {})
            review_channel_id = config.get("review_channel_id")
            review_channel = interaction.guild.get_channel_or_thread(review_channel_id)
            
            if review_channel:
                for app in promoted:
                    embed = discord.Embed(
                        title="🔄 等待列表申请晋升",
                        description=f"申请ID: `{app.id}`",
                        color=discord.Color.orange(),
                        timestamp=datetime.datetime.utcnow()
                    )
                    embed.add_field(name="📝 标题", value=app.title, inline=False)
                    embed.add_field(name="📍 位置", value=app.location, inline=True)
                    embed.add_field(name="👤 申请者", value=f"<@{app.applicant_id}>", inline=True)
                    
                    if app.description:
                        embed.add_field(name="📄 内容", value=app.description, inline=False)
                    
                    if app.cover_image:
                        embed.set_image(url=app.cover_image)
                    
                    view = ReviewView(app.id)
                    await review_channel.send(embed=embed, view=view)
                    
                    # 记录等待列表晋升到审核日志
                    await _send_audit_log(
                        interaction.guild,
                        app,
                        "晋升",
                        interaction.guild.me  # 使用机器人作为"审核员"
                    )


class BannerListView(ui.View):
    """Banner列表管理视图"""
    
    def __init__(self, guild_id: int, items: list):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.items = items
        self.selected_item_id = None
        self.db = BannerDatabase()
        
        # 添加下拉选择框
        if items:
            options = []
            for item in items:
                # 截断长标题以适应Discord限制
                label = item.title[:100] if len(item.title) > 100 else item.title
                description = item.description[:100] if item.description and len(item.description) > 100 else item.description
                options.append(discord.SelectOption(
                    label=label,
                    value=item.id,
                    description=description[:100] if description else f"位置: {item.location[:50]}"
                ))
            
            self.banner_select.options = options
        else:
            # 没有条目时禁用选择框
            self.banner_select.disabled = True
            self.edit_button.disabled = True 
            self.delete_button.disabled = True
    
    @ui.select(placeholder="选择要操作的banner条目...")
    async def banner_select(self, interaction: discord.Interaction, select: ui.Select):
        """处理下拉选择框选择"""
        self.selected_item_id = select.values[0]
        
        # 启用编辑和删除按钮
        self.edit_button.disabled = False
        self.delete_button.disabled = False
        
        # 获取选中的条目信息
        selected_item = next((item for item in self.items if item.id == self.selected_item_id), None)
        if selected_item:
            # 创建选中条目的详细信息embed
            embed = discord.Embed(
                title="🔹 已选择Banner条目",
                description=f"**ID**: `{selected_item.id}`",
                color=discord.Color.blue()
            )
            embed.add_field(name="📝 标题", value=selected_item.title, inline=False)
            embed.add_field(name="📄 描述", value=selected_item.description or "无", inline=False)
            embed.add_field(name="📍 位置", value=selected_item.location, inline=False)
            
            if selected_item.cover_image:
                embed.add_field(name="🖼️ 封面图", value=f"[查看图片]({selected_item.cover_image})", inline=False)
                embed.set_thumbnail(url=selected_item.cover_image)
            
            embed.set_footer(text="您现在可以编辑或删除此条目")
            
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.send_message("❌ 选中的条目不存在", ephemeral=True)
    
    @ui.button(label="编辑", style=discord.ButtonStyle.secondary, emoji="✏️", disabled=True)
    async def edit_button(self, interaction: discord.Interaction, button: ui.Button):
        """处理编辑按钮点击"""
        if not self.selected_item_id:
            await interaction.response.send_message("❌ 请先选择一个条目", ephemeral=True)
            return
        
        # 获取选中的条目
        selected_item = next((item for item in self.items if item.id == self.selected_item_id), None)
        if not selected_item:
            await interaction.response.send_message("❌ 选中的条目不存在", ephemeral=True)
            return
        
        # 显示编辑modal
        modal = BannerEditModal(self.guild_id, selected_item)
        await interaction.response.send_modal(modal)
    
    @ui.button(label="删除", style=discord.ButtonStyle.danger, emoji="🗑️", disabled=True)
    async def delete_button(self, interaction: discord.Interaction, button: ui.Button):
        """处理删除按钮点击"""
        if not self.selected_item_id:
            await interaction.response.send_message("❌ 请先选择一个条目", ephemeral=True)
            return
        
        # 获取选中的条目
        selected_item = next((item for item in self.items if item.id == self.selected_item_id), None)
        if not selected_item:
            await interaction.response.send_message("❌ 选中的条目不存在", ephemeral=True)
            return
        
        # 确认删除
        confirm_embed = discord.Embed(
            title="⚠️ 确认删除",
            description=f"您确定要删除以下banner条目吗？\n\n**ID**: `{selected_item.id}`\n**标题**: {selected_item.title}",
            color=discord.Color.red()
        )
        
        # 创建确认视图
        confirm_view = BannerDeleteConfirmView(self.guild_id, self.selected_item_id, selected_item.title)
        await interaction.response.send_message(embed=confirm_embed, view=confirm_view, ephemeral=True)


class BannerDeleteConfirmView(ui.View):
    """删除确认视图"""
    
    def __init__(self, guild_id: int, item_id: str, item_title: str):
        super().__init__(timeout=30)
        self.guild_id = guild_id
        self.item_id = item_id 
        self.item_title = item_title
        self.db = BannerDatabase()
    
    @ui.button(label="确认删除", style=discord.ButtonStyle.danger, emoji="✅")
    async def confirm_delete(self, interaction: discord.Interaction, button: ui.Button):
        """确认删除"""
        # 删除条目
        if self.db.remove_item(self.guild_id, self.item_id):
            # 获取BannerCommands cog来处理event更新
            try:
                banner_cog = interaction.client.get_cog("轮换通知")
                if banner_cog:
                    # 检查是否需要删除或更新event
                    config = self.db.load_config(self.guild_id)
                    if len(config.items) == 0 and config.event_id:
                        # 如果没有条目了，删除event
                        await banner_cog._delete_event(interaction.guild)
                    else:
                        # 更新event显示下一个条目
                        await banner_cog._create_or_update_event(interaction.guild)
            except Exception as e:
                # 记录错误但不阻止删除成功响应
                print(f"更新event时出错: {e}")
            
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="✅ 删除成功",
                    description=f"已删除banner条目 `{self.item_id}` - {self.item_title}",
                    color=discord.Color.green()
                ),
                view=None
            )
        else:
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="❌ 删除失败",
                    description="删除操作失败，请稍后重试",
                    color=discord.Color.red()
                ),
                view=None
            )
    
    @ui.button(label="取消", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel_delete(self, interaction: discord.Interaction, button: ui.Button):
        """取消删除"""
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="❌ 已取消",
                description="删除操作已取消",
                color=discord.Color.orange()
            ),
            view=None
        )


class BannerEditModal(ui.Modal):
    """Banner编辑表单"""
    
    def __init__(self, guild_id: int, item):
        super().__init__(title=f"编辑Banner - {item.id}", timeout=300)
        self.guild_id = guild_id
        self.item = item
        self.db = BannerDatabase()
        
        # 设置初始值
        self.title_input.default = item.title
        self.description_input.default = item.description or ""
        self.location_input.default = item.location
        self.cover_image_input.default = item.cover_image or ""
    
    title_input = ui.TextInput(
        label="标题",
        placeholder="请输入banner标题...",
        max_length=100,
        required=True
    )
    
    description_input = ui.TextInput(
        label="描述",
        placeholder="请输入banner描述...",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=False
    )
    
    location_input = ui.TextInput(
        label="位置",
        placeholder="请输入位置...",
        max_length=100,
        required=True
    )
    
    cover_image_input = ui.TextInput(
        label="封面图链接（可选）",
        placeholder="请输入封面图URL...",
        max_length=500,
        required=False
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        """处理编辑提交"""
        from src.banner.database import BannerItem
        
        # 创建更新后的条目
        updated_item = BannerItem(
            id=self.item.id,
            title=self.title_input.value,
            description=self.description_input.value or None,
            location=self.location_input.value,
            cover_image=self.cover_image_input.value or None
        )
        
        # 更新数据库
        if self.db.update_item(self.guild_id, updated_item):
            # 获取BannerCommands cog来处理event更新
            try:
                banner_cog = interaction.client.get_cog("轮换通知")
                if banner_cog:
                    # 更新event以反映更改
                    await banner_cog._create_or_update_event(interaction.guild)
            except Exception as e:
                # 记录错误但不阻止编辑成功响应
                print(f"更新event时出错: {e}")
            
            embed = discord.Embed(
                title="✅ 编辑成功",
                description=f"已更新banner条目 `{self.item.id}`",
                color=discord.Color.green()
            )
            embed.add_field(name="📝 标题", value=updated_item.title, inline=False)
            embed.add_field(name="📄 描述", value=updated_item.description or "无", inline=False)
            embed.add_field(name="📍 位置", value=updated_item.location, inline=False)
            
            if updated_item.cover_image:
                embed.add_field(name="🖼️ 封面图", value=f"[查看图片]({updated_item.cover_image})", inline=False)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ 编辑失败",
                    description="更新操作失败，请稍后重试",
                    color=discord.Color.red()
                ),
                ephemeral=True
            )