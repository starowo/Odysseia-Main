import discord
from discord import ui

from src.license.utils import safe_defer, do_simple_owner_id_interaction_check


class ConfirmPostView(ui.View):
    """
    一个通用的、用于最终确认操作的视图。
    常见于“预览并发布”的场景。
    """

    def __init__(self, author_id: int, on_confirm: callable, on_cancel: callable):
        """
        Args:
            author_id: 授权进行操作的用户ID。
            on_confirm: 点击确认按钮时调用的回调，签名 `async def on_confirm(interaction)`。
            on_cancel: 点击取消/返回按钮时调用的回调，签名 `async def on_cancel(interaction)`。
        """
        super().__init__(timeout=300)
        self.author_id = author_id
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """权限检查"""
        return await do_simple_owner_id_interaction_check(self.author_id, interaction)

    @ui.button(label="✅ 确认发布", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        """调用确认回调。"""
        await safe_defer(interaction)
        await self.on_confirm(interaction)

    @ui.button(label="❌ 返回", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        """调用取消回调。"""
        await safe_defer(interaction)
        await self.on_cancel(interaction)
