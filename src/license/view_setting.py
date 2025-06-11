from typing import TYPE_CHECKING

import discord
from discord import ui, Thread

if TYPE_CHECKING:
    from src.license.cog import LicenseCog
from src.license.constants import ACTIVE_COMMAND_CONFIG
from src.license.database import LicenseDB, LicenseConfig, get_default_license_details
from src.license.view_tool import ConfirmPostView
from src.license.utils import build_settings_embed, safe_delete_original_response


class SettingsView(ui.View):
    """
    机器人行为设置的独立面板视图。
    用户可以在这里开关各项功能。
    现在，它会自我更新整个Embed以显示最新的设置状态和解释。

    设计模式：
    - 状态自更新：每个开关按钮被点击后，会更新后台数据，然后调用 `update_button_labels` 和
      `interaction.response.edit_message(view=self)` 来刷新自身，从而在界面上即时反映出
      新的状态（如 ✅ 和 ❌ 的切换），提供了良好的交互反馈。
    - 独立确认流程：对于危险操作（重置、删除数据），它不会直接执行，而是会弹出另一个
      临时的、独立的确认视图（`ConfirmPostView`），防止用户误操作。
    """

    def __init__(self, db: 'LicenseDB', config: 'LicenseConfig', cog: 'LicenseCog', thread: Thread = None, initial_interaction: discord.Interaction = None):
        super().__init__(timeout=600)
        self.db = db
        self.config = config
        self.cog = cog  # 传入Cog实例，主要为了访问 logger
        self.thread = thread
        self.initial_interaction = initial_interaction  # 看上去是外层传入的消息，但是有趣的是，它的delete_original_response似乎会删除掉SettingsView自身
        self.update_button_labels()  # 初始化时设置正确的按钮标签

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # self.config.user_id 是这个设置面板的真正主人
        if interaction.user.id != self.config.user_id:
            await interaction.response.send_message("❌ 你不能修改别人的设置。", ephemeral=True)
            return False
        return True

    def update_button_labels(self):
        """根据当前的 `self.config` 状态，更新按钮上的标签和表情符号。"""
        self.toggle_auto_post_button.label = f"自动发布: {'✅' if self.config.auto_post else '❌'}"
        self.toggle_bot_enabled_button.label = f"机器人总开关: {'✅' if self.config.bot_enabled else '❌'}"
        self.toggle_confirmation_button.label = f"发布前二次确认: {'✅' if self.config.require_confirmation else '❌'}"

    # 【新增】一个私有的更新视图的辅助方法
    async def _update_view(self, interaction: discord.Interaction):
        """保存配置，并用全新的Embed和更新后的按钮刷新视图。"""
        self.db.save_config(self.config)
        self.update_button_labels()

        # 使用工厂函数创建新的Embed
        new_embed = build_settings_embed(self.config)

        # 编辑原始消息，同时更新Embed和View
        await interaction.response.edit_message(embed=new_embed, view=self)

    @ui.button(label="切换自动发布", style=discord.ButtonStyle.primary, row=0)
    async def toggle_auto_post_button(self, interaction: discord.Interaction, button: ui.Button):
        """切换“自动发布”选项。"""
        self.config.auto_post = not self.config.auto_post
        await self._update_view(interaction)

    @ui.button(label="切换机器人总开关", style=discord.ButtonStyle.primary, row=0)
    async def toggle_bot_enabled_button(self, interaction: discord.Interaction, button: ui.Button):
        """切换“机器人总开关”选项。"""
        self.config.bot_enabled = not self.config.bot_enabled
        await self._update_view(interaction)

    @ui.button(label="切换发布前二次确认", style=discord.ButtonStyle.primary, row=1)
    async def toggle_confirmation_button(self, interaction: discord.Interaction, button: ui.Button):
        """切换“发布前二次确认”选项。"""
        self.config.require_confirmation = not self.config.require_confirmation
        await self._update_view(interaction)

    @ui.button(label="重置我的协议", style=discord.ButtonStyle.danger, row=2)
    async def reset_license(self, interaction: discord.Interaction, button: ui.Button):
        """重置用户的授权协议为默认值，这是一个危险操作，需要二次确认。"""

        async def on_confirm(confirm_interaction: discord.Interaction):
            # 确认后，执行重置操作
            self.config.license_details = get_default_license_details(self.config.user_id)
            self.db.save_config(self.config)
            await confirm_interaction.response.edit_message(content="✅ 你的授权协议已重置为默认值。", embed=None, view=None)
            await safe_delete_original_response(confirm_interaction, sleep_time=1)

        async def on_cancel(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(content="🚫 操作已取消。", embed=None, view=None)
            await safe_delete_original_response(cancel_interaction, sleep_time=1)

        # 发起一个独立的、临时的确认流程
        confirm_view = ConfirmPostView(interaction.user.id, on_confirm, on_cancel)
        await interaction.response.send_message(
            "**⚠️ 警告：** 此操作会将你的默认协议恢复为初始设置！\n请确认你的操作：",
            view=confirm_view,
            ephemeral=True
        )

    @ui.button(label="删除所有数据", style=discord.ButtonStyle.danger, row=2)
    async def delete_data(self, interaction: discord.Interaction, button: ui.Button):
        """删除用户在本机器人中的所有数据，这是一个非常危险的操作，需要二次确认。"""

        async def on_confirm(confirm_interaction: discord.Interaction):
            # 执行真正的删除操作
            # try:
            #     self.db.delete_config(self.config.user_id)
            # except OSError as e:
            #     if self.cog.logger: self.cog.logger.error(f"删除用户数据文件失败: {self.config.user_id}, 错误: {e}")
            #     await confirm_interaction.response.edit_message(content=f"❌ 删除数据时发生错误！请联系管理员。", view=None)
            #     return

            # 在确认面板上给出最终反馈
            cmd_name = ACTIVE_COMMAND_CONFIG["group"]["name"]
            cmd_name_panel = ACTIVE_COMMAND_CONFIG["panel"]["name"]

            await confirm_interaction.response.edit_message(
                content=
                "🗑️ **你的所有数据已被永久删除。**\n"
                "> **所有的控制面板即将/已经关闭。**\n"
                f" 你可以随时使用 `/{cmd_name} {cmd_name_panel}` 指令来重新打开面板。"
                , embed=None,
                view=None)
            if self.initial_interaction is not None:
                await self.initial_interaction.edit_original_response(content="🗑️ **你的所有数据已被永久删除。", embed=None, view=None)
            # await safe_delete_original_response(confirm_interaction, sleep_time=1)
            if self.initial_interaction is not None and self.thread is not None:
                await safe_delete_original_response(self.initial_interaction, sleep_time=1)
                await self.cog.cleanup_previous_helpers(thread=self.thread)

        async def on_cancel(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(content="🚫 操作已取消，你的数据安然无恙。", view=None)
            await safe_delete_original_response(cancel_interaction, sleep_time=1)

        # 创建并发送带有强烈警告的独立确认视图
        confirm_view = ConfirmPostView(interaction.user.id, on_confirm, on_cancel)
        await interaction.response.send_message(
            "**🚨 终极警告：此操作不可逆！🚨**\n\n"
            "你确定要**永久删除**你保存在本机器人中的所有数据吗？这包括：\n"
            "- 你保存的默认授权协议\n"
            "- 所有的机器人行为设置\n\n"
            "**此操作无法撤销！请再次确认！**",
            view=confirm_view,
            ephemeral=True
        )

    @ui.button(label="关闭面板", style=discord.ButtonStyle.secondary, row=3)
    async def close_panel(self, interaction: discord.Interaction, button: ui.Button):
        """关闭（即删除）这个设置面板消息。"""
        await interaction.response.defer()  # 先响应，防止超时
        await interaction.delete_original_response()
        self.stop()
