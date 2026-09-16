"""所有应用命令统一使用服务器安装和服务器调用上下文。"""

import discord
from discord import app_commands


class GuildOnlyCommandTree(app_commands.CommandTree):
    def __init__(self, client, **kwargs):
        # 在命令树上设置默认值，同时覆盖斜杠命令、命令组和右键菜单。
        kwargs["allowed_installs"] = app_commands.AppInstallationType(
            guild=True, user=False
        )
        kwargs["allowed_contexts"] = app_commands.AppCommandContext(
            guild=True, dm_channel=False, private_channel=False
        )
        super().__init__(client, **kwargs)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        # 注册配置尚未同步或客户端仍缓存旧命令时，也不接受个人安装调用。
        if interaction.guild_id is not None and interaction.is_guild_integration():
            return True

        await interaction.response.send_message(
            "❌ 此命令只能通过服务器安装的机器人在服务器中使用", ephemeral=True
        )
        return False
