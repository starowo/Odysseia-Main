import typing
from typing import List

import discord
from discord import ui

if typing.TYPE_CHECKING:
    from src.license.cog import LicenseCog
from src.license.constants import SIGNATURE_HELPER, MESSAGE_IGNORE_ONCE, MESSAGE_IGNORE, HUB_VIEW_CONTENT
from src.license.database import LicenseDB, LicenseConfig
from src.license.view.view_license_edit import LicenseEditHubView
from src.license.ui_factory import prepare_confirmation_flow, prepare_edit_hub
from src.license.utils import do_simple_owner_id_interaction_check, get_member_async_thread, build_footer_text, build_license_embeds, safe_defer, \
    create_helper_embed, build_settings_embed
from src.license.view.view_setting import SettingsView


class InitialActionView(ui.View):
    """
    这是用户发帖后看到的主要交互面板（针对已注册用户）。
    提供了所有核心操作的入口：直接发布、临时编辑后发布、永久编辑、预览、设置等。
    """

    def __init__(self, cog: 'LicenseCog', db: LicenseDB, config: LicenseConfig, thread: discord.Thread, commercial_use_allowed: bool):
        super().__init__(timeout=3600)  # 较长的超时时间，给用户充分的反应时间
        self.cog = cog
        self.db = db
        self.config = config
        self.thread = thread
        self.owner_id = thread.owner_id
        # 缓存原始的Embed，以便在各种操作后可以方便地“返回主菜单”。
        self._original_embed = None
        self.commercial_use_allowed = commercial_use_allowed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """权限检查"""
        return await do_simple_owner_id_interaction_check(self.owner_id, interaction)

    async def build_original_embed(self) -> discord.Embed:
        """构建主交互面板的Embed。"""
        member = await get_member_async_thread(self.thread, self.owner_id)
        display_name = member.display_name if member else "创作者"

        embed = discord.Embed(
            title=f"👋 你好, {display_name}！",
            description="我注意到你发布了一个新作品。你希望如何处理内容的授权协议呢？",
            color=discord.Color.blue()
        )
        embed.set_footer(text=build_footer_text(SIGNATURE_HELPER))
        return embed

    async def get_original_embed(self):
        if self._original_embed is None:
            self._original_embed = await self.build_original_embed()
        return self._original_embed

    # --- 核心UI流程方法 ---

    async def post_license_directly(self, interaction: discord.Interaction, config_to_post: LicenseConfig):
        """
        一个直接发布协议的辅助函数。
        它现在相信 build_license_embed 总能成功。
        """
        # 直接构建并获取 Embed，不再检查错误
        final_embeds = build_license_embeds(
            config_to_post,
            interaction.user,
            self.commercial_use_allowed
        )

        # 直接发布
        await self.thread.send(embeds=final_embeds)
        await interaction.edit_original_response(
            content="✅ 协议已直接发布。", embed=None, view=None
        )
        if isinstance(interaction, discord.Interaction):
            self.stop()

    async def show_confirmation_view(self, interaction: discord.Interaction, config_to_show: LicenseConfig):
        """
        显示预览和确认发布的界面。这是一个可复用的流程。
        Args:
            interaction: 触发此流程的交互。
            config_to_show: 需要被预览和发布的 `LicenseConfig` 对象。
        """
        await safe_defer(interaction)

        # 定义确认和取消按钮的具体行为
        async def do_post(post_interaction: discord.Interaction, final_embeds: List[discord.Embed]):
            """确认=发帖并关闭面板"""
            await post_interaction.edit_original_response(content=f"{SIGNATURE_HELPER}: ✅ 协议已发布。", embed=None, view=None)
            await self.thread.send(embeds=final_embeds)
            self.stop()

        async def do_cancel(cancel_interaction: discord.Interaction):
            """取消=返回主菜单"""
            await self.back_to_main_menu(cancel_interaction)

        # 创建并显示确认视图
        preview_content, preview_embeds, confirm_view = await prepare_confirmation_flow(
            cog=self.cog,  # 传递 self.cog！
            thread=self.thread,
            config=config_to_show,
            author=interaction.user,
            on_confirm_action=do_post,
            on_cancel_action=do_cancel
        )

        await interaction.edit_original_response(content=preview_content, embeds=preview_embeds, view=confirm_view)

    async def back_to_main_menu(self, interaction: discord.Interaction):
        """
        一个可复用的方法，用于将UI完全恢复到初始的主菜单状态。
        """

        # 核心：用原始的Embed和自身(self, 即InitialActionView)来编辑消息，实现“返回”效果。
        await interaction.edit_original_response(
            content=None,  # 清除可能存在的上层文本，如“你正在编辑...”
            embed=await self.get_original_embed(),
            view=self
        )

    # --- 按钮定义 ---

    @ui.button(label="发布默认协议", style=discord.ButtonStyle.success, row=0)
    async def post_default(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：直接使用用户保存的默认配置进行发布流程。"""
        await safe_defer(interaction)
        if self.config.require_confirmation:
            await self.show_confirmation_view(interaction, self.config)
        else:
            await self.post_license_directly(interaction, self.config)

    @ui.button(label="编辑并发布(仅本次)", style=discord.ButtonStyle.primary, row=0)
    async def edit_and_post_once(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：临时编辑协议并发布。"""
        await safe_defer(interaction)

        # 1. 定义此场景下的回调
        async def on_edit_complete(edit_interaction: discord.Interaction, temp_details: dict):
            temp_config = LicenseConfig(edit_interaction.user)
            temp_config.license_details = temp_details
            await self.show_confirmation_view(edit_interaction, temp_config)

        async def on_edit_cancel(cancel_interaction: discord.Interaction):
            await self.back_to_main_menu(cancel_interaction)

        # 2. 调用工厂
        content, hub_view = prepare_edit_hub(
            db=self.db,
            config=self.config,
            on_success_callback=on_edit_complete,
            on_cancel_callback=on_edit_cancel,
            commercial_use_allowed=self.commercial_use_allowed,
            is_temporary=True,
            owner_id=self.owner_id
        )

        # 呈现UI时使用标准Embed
        hub_embed = create_helper_embed(
            title="📝 编辑临时协议 (仅本次)",
            description=content
        )
        await interaction.edit_original_response(
            embed=hub_embed,
            view=hub_view
        )

    @ui.button(label="编辑默认协议", style=discord.ButtonStyle.secondary, row=0)
    async def edit_default_license(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：编辑并永久保存用户的默认协议。"""
        await safe_defer(interaction)

        # 1. 定义此场景下的“成功”和“取消”回调
        async def on_edit_complete(edit_interaction: discord.Interaction, new_details: dict):
            # 成功：保存配置，然后返回主菜单
            self.config.license_details = new_details
            self.db.save_config(self.config)
            await self.back_to_main_menu(edit_interaction)
            await edit_interaction.followup.send("✅ 你的默认协议已永久保存！", ephemeral=True)

        async def on_edit_cancel(cancel_interaction: discord.Interaction):
            # 取消：直接返回主菜单
            await self.back_to_main_menu(cancel_interaction)

        # 2. 调用同一个工厂函数来构建UI组件
        content, hub_view = prepare_edit_hub(
            db=self.db,
            config=self.config,
            on_success_callback=on_edit_complete,
            on_cancel_callback=on_edit_cancel,
            commercial_use_allowed=self.commercial_use_allowed,
            is_temporary=False,
            owner_id=self.owner_id
        )

        # 3. 在自己的上下文中呈现UI (编辑当前消息)
        # 呈现UI时使用标准Embed
        hub_embed = create_helper_embed(
            title="📝 编辑默认协议 (永久)",
            description=content
        )
        await interaction.edit_original_response(
            embed=hub_embed,
            view=hub_view
        )

    @ui.button(label="预览协议", style=discord.ButtonStyle.primary, row=0)
    async def preview_license(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：以一条临时的、只有自己能看到的消息来预览当前默认协议。"""
        await safe_defer(interaction)

        embeds = build_license_embeds(
            self.config,
            interaction.user,
            commercial_use_allowed=self.commercial_use_allowed,
            title_override="👀 你的当前默认协议预览",
            footer_override=build_footer_text(SIGNATURE_HELPER)
        )

        # 使用 followup.send 发送私密消息。这是最可靠的发送 ephemeral 消息的方式。
        await interaction.followup.send(embeds=embeds, ephemeral=True)

    @ui.button(label="机器人设置", style=discord.ButtonStyle.secondary, row=1)
    async def settings(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：打开独立的机器人行为设置面板。"""
        await safe_defer(interaction)
        # 这个逻辑和斜杠命令 `/内容授权 设置` 完全一样
        config = self.db.get_config(interaction.user)
        # 使用新的工厂函数创建Embed
        embed = build_settings_embed(config)
        view = SettingsView(self.db, config, self.cog, self.thread, initial_interaction=interaction)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @ui.button(label="本次跳过", style=discord.ButtonStyle.secondary, row=1)
    async def skip_for_now(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：关闭交互面板，不执行任何操作。"""
        await safe_defer(interaction)
        await interaction.edit_original_response(
            content=MESSAGE_IGNORE_ONCE,
            embed=None, view=None
        )
        self.stop()

    @ui.button(label="别再打扰我", style=discord.ButtonStyle.danger, row=1)
    async def disable_bot(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：禁用机器人，机器人将不再主动发送提醒。"""
        await safe_defer(interaction)
        config = self.db.get_config(interaction.user)
        config.bot_enabled = False
        self.db.save_config(config)
        await interaction.edit_original_response(content=MESSAGE_IGNORE, embed=None, view=None)
        self.stop()


class FirstTimeSetupView(ui.View):
    """
    新用户第一次与机器人交互时看到的欢迎和引导视图。
    主要目的是引导用户完成首次协议创建。
    """

    def __init__(self, cog: 'LicenseCog', db: 'LicenseDB', owner_id: int, thread: discord.Thread, commercial_use_allowed: bool):
        super().__init__(timeout=3600)
        self.cog = cog
        self.db = db
        self.owner_id = owner_id
        self.thread = thread
        self.commercial_use_allowed = commercial_use_allowed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """权限检查"""
        return await do_simple_owner_id_interaction_check(self.owner_id, interaction)

    @ui.button(label="✨ 创建我的授权协议", style=discord.ButtonStyle.success)
    async def create_license(self, interaction: discord.Interaction, button: ui.Button):
        """
        按钮：引导新用户创建他们的第一个默认协议。
        设计模式：此流程完成后，会将当前的 `FirstTimeSetupView` 替换为标准的 `InitialActionView`，
        使用户的体验与老用户保持一致，无需为新用户编写一套完全独立的后续逻辑。
        """
        await safe_defer(interaction)
        config = self.db.get_config(interaction.user)  # 获取一个默认配置

        # 定义创建完成后的行为：保存数据，然后用标准的主交互面板替换当前欢迎界面
        async def on_create_complete(create_interaction: discord.Interaction, new_details: dict):
            # a. 保存数据
            config.license_details = new_details
            self.db.save_config(config)

            # b. 创建标准的主交互面板视图
            main_view = InitialActionView(self.cog, self.db, config, self.thread, commercial_use_allowed=self.commercial_use_allowed)

            # c. 用主交互面板替换当前的欢迎界面
            await create_interaction.edit_original_response(
                content=None,  # 清理掉之前的欢迎文字
                embed=await main_view.get_original_embed(),
                view=main_view
            )
            # 此后，交互的控制权交给了 main_view

        # 定义取消创建的行为：返回欢迎界面
        async def on_create_cancel(cancel_interaction: discord.Interaction):
            await cancel_interaction.edit_original_response(
                embed=interaction.message.embeds[0], view=self
            )

        hub_content = (
            "太棒了！请创建你的第一份默认协议。\n"
            "这将成为你未来发布作品时的默认设置。\n"
            f"{HUB_VIEW_CONTENT}"
        )

        # 创建并显示编辑枢纽视图
        hub_view = LicenseEditHubView(
            db=self.db,
            config=config,
            content=hub_content,
            callback=on_create_complete,
            on_cancel=on_create_cancel,
            commercial_use_allowed=self.commercial_use_allowed,
            is_temporary=False,
            owner_id=interaction.user.id
        )
        hub_embed = create_helper_embed(
            title="👋 欢迎！创建你的第一份协议",
            description=hub_content,
            color=discord.Color.magenta()
        )
        await interaction.edit_original_response(
            embed=hub_embed,
            view=hub_view
        )

    @ui.button(label="本次跳过", style=discord.ButtonStyle.secondary)
    async def skip_for_now(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：关闭欢迎面板。"""
        await safe_defer(interaction)
        await interaction.edit_original_response(
            content=MESSAGE_IGNORE_ONCE,
            embed=None, view=None
        )
        self.stop()

    @ui.button(label="别再打扰我", style=discord.ButtonStyle.danger)
    async def disable_bot(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：直接禁用机器人。"""
        await safe_defer(interaction)
        config = self.db.get_config(interaction.user)
        config.bot_enabled = False
        self.db.save_config(config)
        await interaction.edit_original_response(content=MESSAGE_IGNORE, embed=None, view=None)
        self.stop()
