import discord

class ConfirmView(discord.ui.View):
    """带 ✅ / ❌ 的确认视图"""

    def __init__(self, original_interaction: discord.Interaction, author: discord.User, timeout: int = 60):
        super().__init__(timeout=timeout)
        self.original_interaction = original_interaction
        self.author = author
        self.value: bool = None

    # 权限检查：只让author点击
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "这不是给你的按钮哦～", ephemeral=True
            )
            return False
        return True

    # ✅ 按钮
    @discord.ui.button(
        label="确认", style=discord.ButtonStyle.success, custom_id="confirm_yes"
    )
    async def confirm(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        self.value = True
        self.disable_all_items()
        await self.original_interaction.edit_original_response(view=self)
        self.stop()

    # ❌ 按钮
    @discord.ui.button(
        label="取消", style=discord.ButtonStyle.danger, custom_id="confirm_no"
    )
    async def cancel(
        self, button: discord.ui.Button, interaction: discord.Interaction
    ):
        self.value = False
        self.disable_all_items()
        await self.original_interaction.edit_original_response(view=self)
        self.stop()
    
    def disable_all_items(self):
        for item in self.children:
            item.disabled = True


async def confirm_view_embed(
    interaction: discord.Interaction,
    embed: discord.Embed,
    timeout: int = 120,
) -> bool:
    view = ConfirmView(original_interaction=interaction, author=interaction.user, timeout=timeout)
    await interaction.edit_original_response(embed=embed, view=view)
    await view.wait()
    if view.value is None:
        await interaction.edit_original_response(
            content="⏱ 超时未确认，操作已取消。",
            embed=None,
            view=None,
        )
        return False
    elif view.value:
        await interaction.edit_original_response(
            content="✅ 已确认，开始执行……",
            embed=None,
            view=None,
        )
        return True
    else:
        await interaction.edit_original_response(
            content="🚫 已取消操作。",
            embed=None,
            view=None,
        )
        return False

async def confirm_view(
    interaction: discord.Interaction,
    *,
    title: str = None,
    description: str = None,
    colour: discord.Colour = discord.Colour.orange(),
    timeout: int = 120,
) -> bool:
    """发送确认视图并返回结果。

    Parameters
    ----------
    interaction: 与用户交互对象。
    title: embed 的标题，默认为 "危险操作"。
    description: embed 的描述内容。
    colour: embed 颜色。
    timeout: 按钮超时时间（秒）。

    Returns
    -------
    bool
        True  表示用户确认；
        False 表示取消或超时。
    """

    # 构造 embed
    if title is None:
        title = "危险操作"
    if description is None:
        description = "**确定要执行吗？** 此操作无法撤回！"

    embed = discord.Embed(title=title, description=description, colour=colour)
    embed.set_footer(text=f"按钮将在 {timeout} 秒后失效")

    # 生成确认视图
    view = ConfirmView(original_interaction=interaction, author=interaction.user, timeout=timeout)
    await interaction.edit_original_response(embed=embed, view=view)

    # 等待用户操作 / 超时
    await view.wait()

    # 根据结果返回布尔值，并更新消息
    if view.value is None:  # 超时
        await interaction.edit_original_response(
            content="⏱ 超时未确认，操作已取消。",
            embed=None,
            view=None,
        )
        return False
    elif view.value:  # 确认
        await interaction.edit_original_response(
            content="✅ 已确认，开始执行……",
            embed=None,
            view=None,
        )
        return True
    else:  # 取消
        await interaction.edit_original_response(
            content="🚫 已取消操作。",
            embed=None,
            view=None,
        )
        return False
