"""自助管理「菜单」命令的 UI：下拉菜单、慢速模式子选单、Modal、标签按钮网格、论坛欢迎消息。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import discord
from discord import ui

if TYPE_CHECKING:
    from src.thread_manage.cog import ThreadSelfManage

SLOWMODE_OPTIONS: list[tuple[str, int]] = [
    ("关闭", 0),
    ("5秒", 5),
    ("10秒", 10),
    ("15秒", 15),
    ("30秒", 30),
    ("1分钟", 60),
    ("2分钟", 120),
    ("5分钟", 300),
    ("10分钟", 600),
    ("15分钟", 900),
    ("30分钟", 1800),
    ("1小时", 3600),
    ("2小时", 7200),
    ("6小时", 21600),
]


async def forum_user_opted_out(user_id: int) -> bool:
    """检查用户是否已 opt-out 论坛欢迎消息。"""
    from src.thread_manage import db
    users = await db.load_forum_optout()
    return user_id in users


async def forum_add_optout(user_id: int) -> None:
    """添加用户到论坛 opt-out 列表。"""
    from src.thread_manage import db
    users = await db.load_forum_optout()
    users.add(user_id)
    await db.save_forum_optout(users)


MORE_FEATURES_HELP = (
    "**更多功能**需使用斜杠命令或右键菜单：\n\n"
    "**斜杠命令**（在子区内输入 `/自助管理`）\n"
    "• `删除消息` — 参数：消息链接\n"
    "• `删除消息反应` — 消息链接、可选反应\n"
    "• `清理子区` — 可选阈值\n"
    "• `解锁子区` — 参数：要解锁的子区 ID（可在已锁定的贴外使用）\n"
    "• `标注` — 选择标注/取消标注与消息链接\n"
    "• `禁言` / `解除禁言` — 选择成员及时长等\n"
    "• `自动清理` — 开启/关闭/查看状态\n"
    "• `授权协管` / `移除协管` / `协管列表` — 管理本子区协管\n\n"
    "**右键菜单**（在子区内对消息或成员）\n"
    "• 消息：`删除消息`、`标注/取消标注`\n"
    "• 成员：`子区禁言`、`子区解除禁言`\n"
)


class MenuMessageConfirmView(ui.View):
    """在组件交互消息上使用「确认/取消」，结果写入 self.value。"""

    def __init__(self, author_id: int, *, title: str, description: str, colour: discord.Colour, timeout: float = 120):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value: Optional[bool] = None
        self._embed = discord.Embed(title=title, description=description, colour=colour)
        self._embed.set_footer(text=f"按钮将在 {int(timeout)} 秒后失效")

    @property
    def embed(self) -> discord.Embed:
        return self._embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("这不是给你的按钮哦～", ephemeral=True)
            return False
        return True

    def _disable_all(self) -> None:
        for c in self.children:
            c.disabled = True

    @ui.button(label="确认", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        self.value = True
        self._disable_all()
        await interaction.response.edit_message(content="✅ 已确认，开始执行……", embed=None, view=self)
        self.stop()

    @ui.button(label="取消", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        self.value = False
        self._disable_all()
        await interaction.response.edit_message(content="🚫 已取消操作。", embed=None, view=self)
        self.stop()


async def wait_menu_confirm_on_message(
    message: discord.Message,
    user_id: int,
    *,
    title: str,
    description: str,
    colour: discord.Colour = discord.Colour.orange(),
    timeout: float = 120,
) -> bool:
    """在已有菜单消息上显示确认视图并等待（用于二次确认链，不依赖已过期的 interaction）。"""
    view = MenuMessageConfirmView(
        user_id, title=title, description=description, colour=colour, timeout=timeout
    )
    await message.edit(content=None, embed=view.embed, view=view)
    await view.wait()
    if view.value is None:
        try:
            await message.edit(content="⏱ 超时未确认，操作已取消。", embed=None, view=None)
        except Exception:
            pass
        return False
    return bool(view.value)


class SelfManageMainSelect(ui.Select):
    def __init__(self, cog: ThreadSelfManage, thread: discord.Thread):
        opts = [
            discord.SelectOption(label="慢速模式", value="slowmode", description="设置发言间隔"),
            discord.SelectOption(label="全体通知", value="announce", description="@本贴内成员"),
            discord.SelectOption(label="修改标题", value="title", description="修改子区标题"),
            discord.SelectOption(label="编辑标签", value="tags", description="仅论坛频道的贴"),
            discord.SelectOption(label="锁定并归档", value="lock", description="需确认"),
            discord.SelectOption(label="删帖", value="delete_thread", description="仅贴主，两次确认"),
            discord.SelectOption(label="更多功能", value="more", description="斜杠与右键说明"),
        ]
        super().__init__(placeholder="选择要执行的功能…", min_values=1, max_values=1, options=opts)
        self.cog = cog
        self.thread = thread

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("请在子区内使用。", ephemeral=True)
            return
        ch = interaction.channel
        if ch.id != self.thread.id:
            await interaction.response.send_message("子区已切换，请重新打开菜单。", ephemeral=True)
            return
        if not await self.cog.can_manage_thread(interaction, ch):
            await interaction.response.send_message("不能在他人子区内使用此功能。", ephemeral=True)
            return

        v = self.values[0]
        if v == "slowmode":
            view = SlowModeSubView(self.cog, ch)
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="慢速模式",
                    description="请选择发言间隔（将通知子区并私信反馈）。",
                    colour=discord.Colour.blue(),
                ),
                view=view,
            )
            return
        if v == "announce":
            await interaction.response.send_modal(AnnounceModal(self.cog, ch))
            return
        if v == "title":
            await interaction.response.send_modal(TitleModal(self.cog, ch))
            return
        if v == "tags":
            parent = ch.parent
            if not isinstance(parent, discord.ForumChannel):
                await interaction.response.send_message("编辑标签仅适用于论坛频道的帖子。", ephemeral=True)
                return
            view = TagEditView(self.cog, ch)
            await view.refresh(interaction)
            return
        if v == "lock":
            await interaction.response.defer(ephemeral=True)
            await self.cog.menu_run_lock(interaction, ch)
            return
        if v == "delete_thread":
            await interaction.response.defer(ephemeral=True)
            await self.cog.menu_run_delete_thread(interaction, ch)
            return
        if v == "more":
            embed = discord.Embed(
                title="更多功能说明",
                description=MORE_FEATURES_HELP,
                colour=discord.Colour.dark_teal(),
            )
            await interaction.response.edit_message(embed=embed, view=BackToMainView(self.cog, ch))
            return


class BackToMainView(ui.View):
    def __init__(self, cog: ThreadSelfManage, thread: discord.Thread):
        super().__init__(timeout=300)
        self.cog = cog
        self.add_item(SelfManageMainSelect(cog, thread))

    @ui.button(label="返回主菜单", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("请在子区内使用。", ephemeral=True)
            return
        ch = interaction.channel
        if not await self.cog.can_manage_thread(interaction, ch):
            await interaction.response.send_message("不能在他人子区内使用此功能。", ephemeral=True)
            return
        embed = discord.Embed(
            title="自助管理菜单",
            description="请在下拉列表中选择要执行的操作。",
            colour=discord.Colour.blue(),
        )
        await interaction.response.edit_message(
            embed=embed,
            view=SelfManageMainMenuView(self.cog, ch),
        )


class SelfManageMainMenuView(ui.View):
    def __init__(self, cog: ThreadSelfManage, thread: discord.Thread):
        super().__init__(timeout=300)
        self.add_item(SelfManageMainSelect(cog, thread))


class SlowModeSubSelect(ui.Select):
    def __init__(self, cog: ThreadSelfManage, thread: discord.Thread):
        options = [
            discord.SelectOption(label=name, value=str(sec)) for name, sec in SLOWMODE_OPTIONS
        ]
        super().__init__(placeholder="选择慢速间隔…", min_values=1, max_values=1, options=options[:25])
        self.cog = cog
        self.thread = thread

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("请在子区内使用。", ephemeral=True)
            return
        ch = interaction.channel
        if not await self.cog.can_manage_thread(interaction, ch):
            await interaction.response.send_message("不能在他人子区内使用此功能。", ephemeral=True)
            return
        sec = int(self.values[0])
        await self.cog.apply_slowmode_from_menu(interaction, ch, sec)


class SlowModeSubView(ui.View):
    def __init__(self, cog: ThreadSelfManage, thread: discord.Thread):
        super().__init__(timeout=300)
        self.cog = cog
        self.thread = thread
        self.add_item(SlowModeSubSelect(cog, thread))

    @ui.button(label="返回主菜单", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: ui.Button):
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("请在子区内使用。", ephemeral=True)
            return
        ch = interaction.channel
        if not await self.cog.can_manage_thread(interaction, ch):
            await interaction.response.send_message("不能在他人子区内使用此功能。", ephemeral=True)
            return
        embed = discord.Embed(
            title="自助管理菜单",
            description="请在下拉列表中选择要执行的操作。",
            colour=discord.Colour.blue(),
        )
        await interaction.response.edit_message(embed=embed, view=SelfManageMainMenuView(self.cog, ch))


class AnnounceModal(ui.Modal, title="全体通知"):
    message = ui.TextInput(label="通知内容", style=discord.TextStyle.paragraph, max_length=1800)

    def __init__(self, cog: ThreadSelfManage, thread: discord.Thread):
        super().__init__()
        self.cog = cog
        self.thread = thread

    async def on_submit(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("请在子区内使用。", ephemeral=True)
            return
        ch = interaction.channel
        if not await self.cog.can_manage_thread(interaction, ch):
            await interaction.response.send_message("不能在他人子区内使用此功能。", ephemeral=True)
            return
        await self.cog.apply_announce_from_modal(interaction, ch, str(self.message.value))


class TitleModal(ui.Modal, title="修改标题"):
    def __init__(self, cog: ThreadSelfManage, thread: discord.Thread):
        super().__init__()
        self.cog = cog
        self.thread = thread
        current = (thread.name or "")[:100]
        self.new_title = ui.TextInput(
            label="新标题",
            style=discord.TextStyle.short,
            max_length=100,
            default=current,
        )
        self.add_item(self.new_title)

    async def on_submit(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("请在子区内使用。", ephemeral=True)
            return
        ch = interaction.channel
        if not await self.cog.can_manage_thread(interaction, ch):
            await interaction.response.send_message("不能在他人子区内使用此功能。", ephemeral=True)
            return
        await self.cog.apply_title_from_modal(interaction, ch, str(self.new_title.value))


class TagEditView(ui.View):
    def __init__(self, cog: ThreadSelfManage, thread: discord.Thread):
        super().__init__(timeout=300)
        self.cog = cog
        self.thread = thread

    def _build_buttons(self, forum: discord.ForumChannel, thread: discord.Thread) -> None:
        self.clear_items()
        applied_ids = {t.id for t in thread.applied_tags}
        tags = list(forum.available_tags)
        # 最多 5 行：前 4 行放标签（20 个），第 5 行放「返回」
        for i, tag in enumerate(tags[:20]):
            row = i // 5
            label = tag.name[:80] if len(tag.name) <= 80 else tag.name[:77] + "..."
            style = discord.ButtonStyle.primary if tag.id in applied_ids else discord.ButtonStyle.secondary

            async def toggle_handler(itx: discord.Interaction, tid: int = tag.id, tname: str = tag.name):
                await self.cog.toggle_forum_tag(itx, self.thread, tid, tname)

            btn = ui.Button(label=label, style=style, row=row)
            btn.callback = toggle_handler
            self.add_item(btn)

        back = ui.Button(label="返回主菜单", style=discord.ButtonStyle.secondary, row=4)
        cog, th = self.cog, self.thread

        async def back_cb(itx: discord.Interaction):
            if not isinstance(itx.channel, discord.Thread):
                await itx.response.send_message("请在子区内使用。", ephemeral=True)
                return
            ch = itx.channel
            if not await cog.can_manage_thread(itx, ch):
                await itx.response.send_message("不能在他人子区内使用此功能。", ephemeral=True)
                return
            embed = discord.Embed(
                title="自助管理菜单",
                description="请在下拉列表中选择要执行的操作。",
                colour=discord.Colour.blue(),
            )
            await itx.response.edit_message(embed=embed, view=SelfManageMainMenuView(cog, ch))

        back.callback = back_cb
        self.add_item(back)

    async def refresh(self, interaction: discord.Interaction):
        parent = self.thread.parent
        if not isinstance(parent, discord.ForumChannel):
            await interaction.response.send_message("仅论坛频道可用。", ephemeral=True)
            return
        try:
            fresh = await interaction.guild.fetch_channel(self.thread.id)
        except Exception:
            fresh = self.thread
        if not isinstance(fresh, discord.Thread):
            await interaction.response.send_message("无法获取子区。", ephemeral=True)
            return
        self.thread = fresh
        self._build_buttons(parent, fresh)
        applied = ", ".join(t.name for t in fresh.applied_tags) or "（无）"
        embed = discord.Embed(
            title="编辑标签",
            description=(
                "点击下方标签可添加或移除（每帖最多 5 个标签）。\n"
                f"**当前标签：** {applied}"
            ),
            colour=discord.Colour.green(),
        )
        await interaction.response.edit_message(embed=embed, view=self)


class ForumWelcomeView(ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=600)
        self._owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._owner_id:
            await interaction.response.send_message("只有本帖贴主可以操作这条提示。", ephemeral=True)
            return False
        return True

    async def _delete_welcome_after_defer(self, interaction: discord.Interaction) -> None:
        """在已对交互 defer 后，删除带按钮的频道原消息。"""
        try:
            await interaction.delete_original_response()
        except Exception:
            try:
                if interaction.message:
                    await interaction.message.delete()
            except Exception:
                pass

    @ui.button(label="我知道了", style=discord.ButtonStyle.primary)
    async def ok(self, interaction: discord.Interaction, button: ui.Button):
        self.stop()
        self.clear_items()
        await interaction.response.defer()
        await self._delete_welcome_after_defer(interaction)

    @ui.button(label="不再提醒", style=discord.ButtonStyle.secondary)
    async def optout(self, interaction: discord.Interaction, button: ui.Button):
        self.stop()
        self.clear_items()
        msg = interaction.message
        await forum_add_optout(self._owner_id)
        await interaction.response.send_message(
            "已记录：之后您发布的新帖不会再显示此欢迎提示。",
            ephemeral=True,
        )
        try:
            if msg:
                await msg.delete()
        except Exception:
            pass


class ThreadMuteModal(ui.Modal, title="子区禁言"):
    duration = ui.TextInput(
        label="时长（可选）",
        placeholder="如 10m、1h、1d；留空表示永久",
        required=False,
        max_length=16,
    )
    reason = ui.TextInput(
        label="原因（可选）",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
    )

    def __init__(self, cog: ThreadSelfManage, thread: discord.Thread, member: discord.Member):
        super().__init__()
        self.cog = cog
        self.thread = thread
        self.member = member

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.apply_ctx_mute_from_modal(
            interaction,
            self.thread,
            self.member,
            str(self.duration.value or "").strip(),
            str(self.reason.value or "").strip(),
        )


async def schedule_delete_message(message: discord.Message, delay_sec: float) -> None:
    await asyncio.sleep(delay_sec)
    try:
        await message.delete()
    except Exception:
        pass
