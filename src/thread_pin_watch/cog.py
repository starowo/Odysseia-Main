from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import re
from datetime import datetime
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from src.utils.auth import check_admin_permission, guild_only, is_admin
from src.thread_pin_watch.store import (
    DEFAULT_INTERVAL_SECONDS,
    ThreadPinWatchStore,
    build_thread_link,
    utc_now_iso,
)

PAGE_SIZE = 8
MAX_ARCHIVED_PAGES = 50
MAX_BULK_INPUT_LENGTH = 3500

ACTION_CHOICES = [
    app_commands.Choice(name="添加", value="add"),
    app_commands.Choice(name="批量添加", value="bulk_add"),
    app_commands.Choice(name="移除", value="remove"),
    app_commands.Choice(name="列表", value="list"),
    app_commands.Choice(name="扫描本服当前置顶帖", value="scan_server"),
    app_commands.Choice(name="导入本论坛当前置顶帖", value="import_forum"),
    app_commands.Choice(name="立即检查", value="check_now"),
    app_commands.Choice(name="设置报告频道", value="set_report_channel"),
    app_commands.Choice(name="清空报告频道", value="clear_report_channel"),
    app_commands.Choice(name="开启", value="enable"),
    app_commands.Choice(name="关闭", value="disable"),
]

STATUS_EMOJI = {
    "pending": "⏳",
    "already_pinned": "📌",
    "restored": "🔧",
    "missing": "⚠️",
    "invalid": "⚠️",
    "failed": "❌",
}

STATUS_LABEL = {
    "pending": "待检查",
    "already_pinned": "正常置顶",
    "restored": "已自动恢复",
    "missing": "找不到帖子",
    "invalid": "目标无效",
    "failed": "恢复失败",
}

SOURCE_LABEL = {
    "manual": "单帖添加",
    "bulk": "批量添加",
    "scan_server": "全服扫描导入",
    "scan_forum": "论坛扫描导入",
}

TRIGGER_LABEL = {
    "auto": "自动巡检",
    "manual": "手动检查",
    "manual_add": "添加后即时检查",
    "bulk_add": "批量添加后即时检查",
}

THREAD_ID_RE = re.compile(r"^\d{15,25}$")
THREAD_URL_RE = re.compile(r"channels/\d+/\d+/(\d+)")


def _truncate(text: Optional[str], limit: int = 60) -> str:
    value = (text or "").strip()
    if not value:
        return "未命名帖子"
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _format_iso_time(iso_str: Optional[str]) -> str:
    if not iso_str:
        return "暂无"
    try:
        dt = datetime.fromisoformat(iso_str)
        return f"<t:{int(dt.timestamp())}:F>"
    except Exception:
        return str(iso_str)


def _format_iso_relative(iso_str: Optional[str]) -> str:
    if not iso_str:
        return "暂无"
    try:
        dt = datetime.fromisoformat(iso_str)
        return f"<t:{int(dt.timestamp())}:R>"
    except Exception:
        return str(iso_str)


def _build_summary_text(summary: dict[str, Any]) -> str:
    return (
        f"总数 **{summary.get('total', 0)}** ｜ "
        f"正常 **{summary.get('already_pinned', 0)}** ｜ "
        f"恢复 **{summary.get('restored', 0)}** ｜ "
        f"缺失 **{summary.get('missing', 0)}** ｜ "
        f"无效 **{summary.get('invalid', 0)}** ｜ "
        f"失败 **{summary.get('failed', 0)}**"
    )


class ScanResultsView(discord.ui.View):
    def __init__(self, cog: "ThreadPinWatchCommands", user_id: int, guild_id: int, items: list[dict[str, Any]]):
        super().__init__(timeout=600)
        self.cog = cog
        self.user_id = user_id
        self.guild_id = guild_id
        self.items = items
        self.page = 0
        self.notice: Optional[str] = None
        self._refresh_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ 只有发起扫描的管理员可以操作这个面板。", ephemeral=True)
            return False
        if not await check_admin_permission(interaction):
            await interaction.response.send_message("❌ 您没有权限操作这个面板。", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    @property
    def total_pages(self) -> int:
        return max(1, math.ceil(len(self.items) / PAGE_SIZE))

    def _refresh_buttons(self) -> None:
        self.prev_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= self.total_pages - 1
        has_items = bool(self.items)
        has_new_items = any(not item.get("is_already_watched", False) for item in self.items)
        self.import_all.disabled = not has_items
        self.import_new.disabled = not has_new_items

    def _update_watched_flags(self) -> None:
        watched_ids = {record["thread_id"] for record in self.cog.store.get_threads(self.guild_id)}
        for item in self.items:
            item["is_already_watched"] = int(item["thread_id"]) in watched_ids
        self._refresh_buttons()

    def build_embed(self) -> discord.Embed:
        return self.cog.build_scan_results_embed(self.guild_id, self.items, self.page, self.notice)

    @discord.ui.button(label="上一页", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="下一页", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.total_pages - 1, self.page + 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="全部加入巡检列表", style=discord.ButtonStyle.success)
    async def import_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        result = self.cog.import_scan_items(self.guild_id, self.items, self.user_id, source="scan_server", only_new=False)
        self.notice = (
            f"已处理 {result['processed']} 项：新增 {result['added_count']}，已存在跳过 {result['skipped_count']}。"
        )
        self._update_watched_flags()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="仅加入未收录项", style=discord.ButtonStyle.primary)
    async def import_new(self, interaction: discord.Interaction, button: discord.ui.Button):
        result = self.cog.import_scan_items(self.guild_id, self.items, self.user_id, source="scan_server", only_new=True)
        self.notice = (
            f"已处理 {result['processed']} 项未收录帖子：新增 {result['added_count']}，已存在跳过 {result['skipped_count']}。"
        )
        self._update_watched_flags()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="刷新扫描", style=discord.ButtonStyle.secondary)
    async def refresh_scan(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild or self.cog.bot.get_guild(self.guild_id)
        if guild is None:
            await interaction.response.send_message("❌ 无法获取当前服务器对象，请稍后重试。", ephemeral=True)
            return
        self.items = await self.cog.scan_server_current_pinned_threads(guild)
        self.page = 0
        self.notice = "扫描结果已刷新。"
        self._update_watched_flags()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class WatchListView(discord.ui.View):
    def __init__(
        self,
        cog: "ThreadPinWatchCommands",
        user_id: int,
        guild_id: int,
        forum_id: Optional[int] = None,
        page: int = 0,
    ):
        super().__init__(timeout=600)
        self.cog = cog
        self.user_id = user_id
        self.guild_id = guild_id
        self.forum_id = forum_id
        self.page = page
        self.notice: Optional[str] = None
        self.items = self.cog.store.get_threads(self.guild_id, self.forum_id)
        self._clamp_page()
        self._refresh_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ 只有发起查看的管理员可以操作这个列表。", ephemeral=True)
            return False
        if not await check_admin_permission(interaction):
            await interaction.response.send_message("❌ 您没有权限操作这个列表。", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    @property
    def total_pages(self) -> int:
        return max(1, math.ceil(len(self.items) / PAGE_SIZE))

    def _clamp_page(self) -> None:
        self.page = max(0, min(self.page, self.total_pages - 1))

    def _refresh_buttons(self) -> None:
        self.prev_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= self.total_pages - 1

    def build_embed(self) -> discord.Embed:
        return self.cog.build_watch_list_embed(self.guild_id, self.items, self.page, self.notice, self.forum_id)

    @discord.ui.button(label="上一页", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="下一页", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.total_pages - 1, self.page + 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="刷新列表", style=discord.ButtonStyle.primary)
    async def refresh_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.items = self.cog.store.get_threads(self.guild_id, self.forum_id)
        self._clamp_page()
        self.notice = "巡检列表已刷新。"
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class ThreadPinWatchCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = getattr(bot, "logger", logging.getLogger("bot"))
        self.name = "帖子置顶巡检"
        self.store = ThreadPinWatchStore()
        self.thread_pin_watch_task: Optional[asyncio.Task] = None
        self._watch_lock = asyncio.Lock()
        self._watch_started = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self._watch_started:
            return
        self._watch_started = True
        if self.logger:
            self.logger.info("帖子置顶巡检模块已加载")
        self.thread_pin_watch_task = asyncio.create_task(self._pin_watch_loop())

    async def on_disable(self):
        self._watch_started = False
        if self.thread_pin_watch_task and not self.thread_pin_watch_task.done():
            self.thread_pin_watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.thread_pin_watch_task
        self.thread_pin_watch_task = None

    @app_commands.command(name="置顶巡检", description="管理论坛置顶巡检列表、批量导入与巡检任务")
    @app_commands.rename(
        action="操作",
        thread="帖子",
        thread_list="帖子列表",
        forum="论坛",
        report_channel="报告频道",
        page="页码",
    )
    @app_commands.describe(
        action="要执行的操作",
        thread="目标论坛帖子；不填时优先使用当前帖子",
        thread_list="批量添加时使用，支持多行链接/ID、空格或逗号分隔",
        forum="用于按论坛导入当前置顶帖或筛选列表",
        report_channel="巡检报告发送到这个频道",
        page="列表查看时可直接跳到指定页码",
    )
    @app_commands.choices(action=ACTION_CHOICES)
    @guild_only()
    @is_admin()
    async def thread_pin_watch(
        self,
        interaction: discord.Interaction,
        action: str,
        thread: Optional[discord.Thread] = None,
        thread_list: Optional[str] = None,
        forum: Optional[discord.ForumChannel] = None,
        report_channel: Optional[discord.TextChannel] = None,
        page: Optional[int] = 1,
    ):
        await interaction.response.defer(ephemeral=True)

        if action == "add":
            await self._handle_add(interaction, thread)
        elif action == "bulk_add":
            await self._handle_bulk_add(interaction, thread_list)
        elif action == "remove":
            await self._handle_remove(interaction, thread)
        elif action == "list":
            await self._handle_list(interaction, forum, page or 1)
        elif action == "scan_server":
            await self._handle_scan_server(interaction)
        elif action == "import_forum":
            await self._handle_import_forum(interaction, forum)
        elif action == "check_now":
            await self._handle_check_now(interaction)
        elif action == "set_report_channel":
            await self._handle_set_report_channel(interaction, report_channel)
        elif action == "clear_report_channel":
            await self._handle_clear_report_channel(interaction)
        elif action == "enable":
            await self._handle_set_enabled(interaction, True)
        elif action == "disable":
            await self._handle_set_enabled(interaction, False)
        else:
            await interaction.followup.send("❌ 未知操作。", ephemeral=True)

    async def _handle_add(self, interaction: discord.Interaction, thread: Optional[discord.Thread]):
        target_thread, error = self._resolve_target_thread(interaction, thread)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return

        record = self._thread_to_record(target_thread, interaction.user.id, source="manual")
        add_result = self.store.add_thread_records(interaction.guild.id, [record])
        check_result = await self._check_records_for_guild(
            interaction.guild,
            add_result["added"] or add_result["skipped"],
            trigger="manual_add",
            send_report=False,
            update_summary=False,
        )
        item_summary = _build_summary_text(check_result["summary"])
        watch_state = "新增成功" if add_result["added"] else "该帖子已在巡检列表中，已为你立即复查一次。"
        await interaction.followup.send(
            embed=discord.Embed(
                title="📌 帖子置顶巡检 - 添加完成",
                description=(
                    f"**处理结果**：{watch_state}\n"
                    f"**帖子**：[{_truncate(target_thread.name, 70)}]({self._build_thread_link_from_thread(target_thread)})\n"
                    f"**论坛**：{target_thread.parent.mention if isinstance(target_thread.parent, discord.ForumChannel) else '未知论坛'}\n"
                    f"**即时检查**：{item_summary}"
                ),
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    async def _handle_bulk_add(self, interaction: discord.Interaction, raw_thread_list: Optional[str]):
        if not raw_thread_list or not raw_thread_list.strip():
            await interaction.followup.send("❌ 批量添加时必须提供 `帖子列表` 参数。", ephemeral=True)
            return
        if len(raw_thread_list) > MAX_BULK_INPUT_LENGTH:
            await interaction.followup.send(
                f"❌ 帖子列表过长，请控制在 {MAX_BULK_INPUT_LENGTH} 个字符以内后分批提交。",
                ephemeral=True,
            )
            return

        thread_ids, invalid_tokens = self._parse_thread_list_input(raw_thread_list)
        if not thread_ids and invalid_tokens:
            await interaction.followup.send(
                f"❌ 没有解析出有效的帖子 ID / 链接。无效输入示例：{', '.join(invalid_tokens[:5])}",
                ephemeral=True,
            )
            return

        records: list[dict[str, Any]] = []
        failures: list[str] = []
        for thread_id in thread_ids:
            target_thread = await self._resolve_thread_by_id(interaction.guild, thread_id)
            if target_thread is None:
                failures.append(f"`{thread_id}`：找不到帖子或无权限访问")
                continue
            if not self._is_forum_thread(target_thread):
                failures.append(f"`{thread_id}`：目标不是论坛帖子")
                continue
            records.append(self._thread_to_record(target_thread, interaction.user.id, source="bulk"))

        add_result = self.store.add_thread_records(interaction.guild.id, records)
        records_to_check = add_result["added"] + add_result["skipped"]
        check_result = None
        if records_to_check:
            check_result = await self._check_records_for_guild(
                interaction.guild,
                records_to_check,
                trigger="bulk_add",
                send_report=False,
                update_summary=False,
            )

        embed = discord.Embed(title="📥 帖子置顶巡检 - 批量添加结果", color=discord.Color.blurple())
        embed.add_field(name="输入总数", value=str(len(thread_ids) + len(invalid_tokens)), inline=True)
        embed.add_field(name="解析有效", value=str(len(thread_ids)), inline=True)
        embed.add_field(name="成功新增", value=str(len(add_result["added"])), inline=True)
        embed.add_field(name="已存在跳过", value=str(len(add_result["skipped"])), inline=True)
        embed.add_field(name="失败 / 无效", value=str(len(failures) + len(invalid_tokens)), inline=True)
        if check_result:
            embed.add_field(name="即时检查", value=_build_summary_text(check_result["summary"]), inline=False)
        if add_result["added"]:
            added_preview = [
                f"• [{_truncate(item['title_snapshot'], 50)}]({item['thread_link_snapshot']})"
                for item in add_result["added"][:5]
            ]
            embed.add_field(name="新增示例", value="\n".join(added_preview), inline=False)
        detail_lines = []
        if invalid_tokens:
            detail_lines.append("无法识别：" + "，".join(f"`{token}`" for token in invalid_tokens[:5]))
        if failures:
            detail_lines.extend(failures[:5])
        if detail_lines:
            embed.add_field(name="失败详情", value="\n".join(detail_lines), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _handle_remove(self, interaction: discord.Interaction, thread: Optional[discord.Thread]):
        target_thread, error = self._resolve_target_thread(interaction, thread)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return

        removed = self.store.remove_thread(interaction.guild.id, target_thread.id)
        if removed is None:
            await interaction.followup.send("ℹ️ 该帖子当前不在巡检列表中。", ephemeral=True)
            return

        await interaction.followup.send(
            embed=discord.Embed(
                title="🗑️ 已移出巡检列表",
                description=(
                    f"**帖子**：[{_truncate(removed.get('title_snapshot'), 70)}]({removed.get('thread_link_snapshot')})\n"
                    f"**论坛**：{removed.get('parent_name_snapshot', '未知论坛')}"
                ),
                color=discord.Color.orange(),
            ),
            ephemeral=True,
        )

    async def _handle_list(self, interaction: discord.Interaction, forum: Optional[discord.ForumChannel], page: int):
        view = WatchListView(
            self,
            interaction.user.id,
            interaction.guild.id,
            forum_id=forum.id if forum else None,
            page=max(0, (page or 1) - 1),
        )
        if not view.items:
            description = "当前服务器还没有任何巡检帖子。"
            if forum:
                description = f"论坛 {forum.mention} 下还没有任何巡检帖子。"
            await interaction.followup.send(
                embed=discord.Embed(title="📚 置顶巡检列表", description=description, color=discord.Color.blurple()),
                ephemeral=True,
            )
            return
        await interaction.followup.send(embed=view.build_embed(), view=view, ephemeral=True)

    async def _handle_scan_server(self, interaction: discord.Interaction):
        items = await self.scan_server_current_pinned_threads(interaction.guild)
        if not items:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="🔎 当前没有扫描到全服置顶帖",
                    description="已遍历当前服务器中的论坛频道，但没有发现任何论坛置顶帖。",
                    color=discord.Color.orange(),
                ),
                ephemeral=True,
            )
            return

        view = ScanResultsView(self, interaction.user.id, interaction.guild.id, items)
        await interaction.followup.send(embed=view.build_embed(), view=view, ephemeral=True)

    async def _handle_import_forum(self, interaction: discord.Interaction, forum: Optional[discord.ForumChannel]):
        target_forum, error = self._resolve_forum(interaction, forum)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return

        items = await self.scan_forum_current_pinned_threads(interaction.guild, target_forum)
        if not items:
            await interaction.followup.send(
                f"ℹ️ 论坛 {target_forum.mention} 当前没有置顶帖可导入。",
                ephemeral=True,
            )
            return

        result = self.import_scan_items(interaction.guild.id, items, interaction.user.id, source="scan_forum", only_new=False)
        embed = discord.Embed(title="📥 论坛当前置顶帖导入完成", color=discord.Color.green())
        embed.description = (
            f"**论坛**：{target_forum.mention}\n"
            f"**扫描到置顶帖**：{len(items)}\n"
            f"**新增**：{result['added_count']}\n"
            f"**已存在跳过**：{result['skipped_count']}"
        )
        preview = [
            f"• [{_truncate(item['title_snapshot'], 50)}]({item['thread_link_snapshot']})"
            for item in items[:5]
        ]
        if preview:
            embed.add_field(name="导入示例", value="\n".join(preview), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _handle_check_now(self, interaction: discord.Interaction):
        config = self.store.load(interaction.guild.id)
        if not config.get("threads"):
            await interaction.followup.send("ℹ️ 当前服务器还没有巡检帖子，无法执行立即检查。", ephemeral=True)
            return

        results = await self._run_watch_check(guild_id=interaction.guild.id, trigger="manual", send_report=True, update_summary=True)
        result = results.get(interaction.guild.id)
        if result is None:
            await interaction.followup.send("ℹ️ 当前服务器没有可巡检的帖子，或巡检已被关闭。", ephemeral=True)
            return

        embed = discord.Embed(title="🧪 立即检查完成", color=discord.Color.green())
        embed.description = (
            f"**服务器**：{interaction.guild.name}\n"
            f"**时间**：{_format_iso_time(result['checked_at'])}\n"
            f"**结果汇总**：{_build_summary_text(result['summary'])}"
        )
        if result["results"]:
            preview_lines = [
                self._format_result_line(item, index)
                for index, item in enumerate(result["results"][:5], start=1)
            ]
            embed.add_field(name="明细预览", value="\n".join(preview_lines), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _handle_set_report_channel(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel]):
        if channel is None:
            await interaction.followup.send("❌ 请提供要发送巡检报告的文本频道。", ephemeral=True)
            return
        self.store.set_report_channel(interaction.guild.id, channel.id)
        await interaction.followup.send(
            f"✅ 巡检报告频道已设置为 {channel.mention}。之后自动巡检与手动立即检查都会尝试把报告发送到这里。",
            ephemeral=True,
        )

    async def _handle_clear_report_channel(self, interaction: discord.Interaction):
        self.store.clear_report_channel(interaction.guild.id)
        await interaction.followup.send("✅ 已清空巡检报告频道配置。", ephemeral=True)

    async def _handle_set_enabled(self, interaction: discord.Interaction, enabled: bool):
        self.store.set_enabled(interaction.guild.id, enabled)
        text = "开启" if enabled else "关闭"
        await interaction.followup.send(f"✅ 已{text}当前服务器的帖子置顶巡检。", ephemeral=True)

    def _resolve_target_thread(
        self,
        interaction: discord.Interaction,
        thread: Optional[discord.Thread],
    ) -> tuple[Optional[discord.Thread], Optional[str]]:
        target = thread
        if target is None and isinstance(interaction.channel, discord.Thread):
            target = interaction.channel
        if target is None:
            return None, "❌ 请指定一个论坛帖子，或者直接在论坛帖子里执行该命令。"
        if not self._is_forum_thread(target):
            return None, "❌ 目标必须是论坛频道中的帖子。"
        return target, None

    def _resolve_forum(
        self,
        interaction: discord.Interaction,
        forum: Optional[discord.ForumChannel],
    ) -> tuple[Optional[discord.ForumChannel], Optional[str]]:
        if forum is not None:
            return forum, None
        if isinstance(interaction.channel, discord.ForumChannel):
            return interaction.channel, None
        if isinstance(interaction.channel, discord.Thread) and isinstance(interaction.channel.parent, discord.ForumChannel):
            return interaction.channel.parent, None
        return None, "❌ 请指定一个论坛频道，或者直接在论坛频道/论坛帖子内执行该操作。"

    def _is_forum_thread(self, thread: Any) -> bool:
        return isinstance(thread, discord.Thread) and isinstance(getattr(thread, "parent", None), discord.ForumChannel)

    def _thread_to_record(self, thread: discord.Thread, added_by: int, source: str) -> dict[str, Any]:
        parent = thread.parent if isinstance(thread.parent, discord.ForumChannel) else None
        now_iso = utc_now_iso()
        return {
            "thread_id": int(thread.id),
            "title_snapshot": _truncate(thread.name, 200),
            "thread_link_snapshot": self._build_thread_link_from_thread(thread),
            "parent_id": parent.id if parent else None,
            "parent_name_snapshot": parent.name if parent else "未知论坛",
            "added_by": int(added_by),
            "added_at": now_iso,
            "source": source,
            "last_status": "pending",
            "last_checked_at": None,
            "last_error": None,
        }

    def _scan_item_to_record(self, item: dict[str, Any], added_by: int, source: str) -> dict[str, Any]:
        now_iso = utc_now_iso()
        return {
            "thread_id": int(item["thread_id"]),
            "title_snapshot": item["title_snapshot"],
            "thread_link_snapshot": item["thread_link_snapshot"],
            "parent_id": item.get("parent_id"),
            "parent_name_snapshot": item.get("parent_name_snapshot") or "未知论坛",
            "added_by": int(added_by),
            "added_at": now_iso,
            "source": source,
            "last_status": "already_pinned",
            "last_checked_at": now_iso,
            "last_error": None,
        }

    def _build_thread_link_from_thread(self, thread: discord.Thread) -> str:
        jump_url = getattr(thread, "jump_url", None)
        if jump_url:
            return jump_url
        guild_id = getattr(getattr(thread, "guild", None), "id", None)
        parent_id = getattr(getattr(thread, "parent", None), "id", None)
        return build_thread_link(guild_id, parent_id, int(thread.id))

    def _parse_thread_list_input(self, raw_text: str) -> tuple[list[int], list[str]]:
        tokens = [token.strip() for token in re.split(r"[\s,，]+", raw_text.strip()) if token.strip()]
        thread_ids: list[int] = []
        invalid: list[str] = []
        seen: set[int] = set()

        for token in tokens:
            match = THREAD_URL_RE.search(token)
            if match:
                thread_id = int(match.group(1))
            elif THREAD_ID_RE.match(token):
                thread_id = int(token)
            else:
                invalid.append(token)
                continue
            if thread_id not in seen:
                seen.add(thread_id)
                thread_ids.append(thread_id)
        return thread_ids, invalid

    async def _resolve_thread_by_id(self, guild: discord.Guild, thread_id: int) -> Optional[discord.Thread]:
        cached = guild.get_channel_or_thread(thread_id)
        if isinstance(cached, discord.Thread):
            try:
                fresh = await guild.fetch_channel(thread_id)
                if isinstance(fresh, discord.Thread):
                    return fresh
            except Exception:
                return cached

        try:
            fresh = await guild.fetch_channel(thread_id)
            if isinstance(fresh, discord.Thread):
                return fresh
        except Exception:
            pass

        return await self._find_thread_in_forums(guild, thread_id)

    async def _find_thread_in_forums(self, guild: discord.Guild, thread_id: int) -> Optional[discord.Thread]:
        for channel in getattr(guild, "channels", []):
            if not isinstance(channel, discord.ForumChannel):
                continue
            for active_thread in getattr(channel, "threads", []):
                if getattr(active_thread, "id", None) == thread_id:
                    return active_thread

            before = None
            page_count = 0
            while page_count < MAX_ARCHIVED_PAGES:
                try:
                    archived_threads = [
                        item async for item in channel.archived_threads(limit=100, before=before)
                    ]
                except Exception:
                    break
                if not archived_threads:
                    break
                for archived in archived_threads:
                    if getattr(archived, "id", None) == thread_id:
                        return archived
                before = archived_threads[-1].archive_timestamp
                page_count += 1
        return None

    async def _restore_thread_pin(self, thread: discord.Thread, reason: str) -> tuple[bool, Optional[str]]:
        try:
            await thread.edit(pinned=True, reason=reason)
            return True, None
        except Exception as first_error:
            if getattr(thread, "archived", False):
                try:
                    await thread.edit(archived=False, reason=f"{reason}（临时解除归档）")
                    await thread.edit(pinned=True, reason=reason)
                    await thread.edit(archived=True, reason=f"{reason}（恢复原归档状态）")
                    return True, None
                except Exception as second_error:
                    return False, str(second_error)
            return False, str(first_error)

    async def _check_records_for_guild(
        self,
        guild: discord.Guild,
        records: list[dict[str, Any]],
        *,
        trigger: str,
        send_report: bool,
        update_summary: bool,
    ) -> dict[str, Any]:
        checked_at = utc_now_iso()
        results: list[dict[str, Any]] = []
        summary = {
            "total": 0,
            "already_pinned": 0,
            "restored": 0,
            "missing": 0,
            "invalid": 0,
            "failed": 0,
        }

        for record in records:
            item = {
                "thread_id": int(record["thread_id"]),
                "title_snapshot": record.get("title_snapshot") or f"帖子 {record['thread_id']}",
                "thread_link_snapshot": record.get("thread_link_snapshot"),
                "parent_id": record.get("parent_id"),
                "parent_name_snapshot": record.get("parent_name_snapshot") or "未知论坛",
                "checked_at": checked_at,
                "status": "pending",
                "error": None,
            }
            summary["total"] += 1
            try:
                thread = await self._resolve_thread_by_id(guild, item["thread_id"])
                if thread is None:
                    item["status"] = "missing"
                    item["error"] = "找不到帖子或机器人无权限读取。"
                elif not self._is_forum_thread(thread):
                    item["status"] = "invalid"
                    item["error"] = "目标不再是论坛帖子。"
                else:
                    item["title_snapshot"] = _truncate(thread.name, 200)
                    item["thread_link_snapshot"] = self._build_thread_link_from_thread(thread)
                    item["parent_id"] = getattr(thread.parent, "id", None)
                    item["parent_name_snapshot"] = getattr(thread.parent, "name", "未知论坛")

                    if getattr(getattr(thread, "flags", None), "pinned", False):
                        item["status"] = "already_pinned"
                    else:
                        restored, error = await self._restore_thread_pin(
                            thread,
                            reason=f"帖子置顶巡检自动恢复（{TRIGGER_LABEL.get(trigger, trigger)}）",
                        )
                        item["status"] = "restored" if restored else "failed"
                        item["error"] = error
            except Exception as exc:
                item["status"] = "failed"
                item["error"] = str(exc)

            summary[item["status"]] += 1
            results.append(item)

        self.store.update_check_results(
            guild.id,
            results,
            checked_at=checked_at,
            summary=summary if update_summary else None,
        )

        result = {
            "guild_id": guild.id,
            "guild_name": guild.name,
            "checked_at": checked_at,
            "trigger": trigger,
            "summary": summary,
            "results": results,
        }

        if send_report:
            await self._send_report_for_guild(guild, result)
        return result

    async def _run_watch_check(
        self,
        *,
        guild_id: Optional[int] = None,
        trigger: str = "auto",
        send_report: bool = True,
        update_summary: bool = True,
    ) -> dict[int, dict[str, Any]]:
        results: dict[int, dict[str, Any]] = {}
        async with self._watch_lock:
            guild_ids = [guild_id] if guild_id is not None else self.store.list_guild_ids()
            for gid in guild_ids:
                guild = self.bot.get_guild(gid)
                if guild is None:
                    continue
                config = self.store.load(gid)
                records = config.get("threads", [])
                if not config.get("enabled", True) or not records:
                    continue
                try:
                    results[gid] = await self._check_records_for_guild(
                        guild,
                        records,
                        trigger=trigger,
                        send_report=send_report,
                        update_summary=update_summary,
                    )
                except Exception as exc:
                    if self.logger:
                        self.logger.error(f"[帖子置顶巡检] 处理服务器 {gid} 时出错: {exc}")
        return results

    async def _pin_watch_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await self._run_watch_check(trigger="auto", send_report=True, update_summary=True)
                await asyncio.sleep(DEFAULT_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.logger:
                    self.logger.error(f"[帖子置顶巡检] 后台巡检循环出错: {exc}")
                await asyncio.sleep(30)

    async def _send_report_for_guild(self, guild: discord.Guild, result: dict[str, Any]):
        config = self.store.load(guild.id)
        report_channel_id = config.get("report_channel_id")
        if not report_channel_id:
            return
        channel = guild.get_channel_or_thread(int(report_channel_id))
        if channel is None:
            try:
                channel = await guild.fetch_channel(int(report_channel_id))
            except Exception:
                channel = None
        if channel is None or not hasattr(channel, "send"):
            if self.logger:
                self.logger.warning(f"[帖子置顶巡检] 无法获取服务器 {guild.id} 的报告频道 {report_channel_id}")
            return
        try:
            await channel.send(embed=self.build_report_embed(guild, result))
        except Exception as exc:
            if self.logger:
                self.logger.error(f"[帖子置顶巡检] 发送巡检报告失败: {exc}")

    async def scan_forum_current_pinned_threads(
        self,
        guild: discord.Guild,
        forum: discord.ForumChannel,
    ) -> list[dict[str, Any]]:
        seen: set[int] = set()
        pinned_items: list[dict[str, Any]] = []
        watched_ids = {record["thread_id"] for record in self.store.get_threads(guild.id)}

        for thread in getattr(forum, "threads", []):
            if getattr(thread, "id", None) in seen:
                continue
            seen.add(thread.id)
            if getattr(getattr(thread, "flags", None), "pinned", False):
                pinned_items.append(self._thread_to_scan_item(guild, forum, thread, thread.id in watched_ids))

        before = None
        page_count = 0
        while page_count < MAX_ARCHIVED_PAGES:
            try:
                archived_threads = [
                    item async for item in forum.archived_threads(limit=100, before=before)
                ]
            except Exception:
                break
            if not archived_threads:
                break
            for thread in archived_threads:
                if getattr(thread, "id", None) in seen:
                    continue
                seen.add(thread.id)
                if getattr(getattr(thread, "flags", None), "pinned", False):
                    pinned_items.append(self._thread_to_scan_item(guild, forum, thread, thread.id in watched_ids))
            before = archived_threads[-1].archive_timestamp
            page_count += 1

        pinned_items.sort(key=lambda item: (item["parent_name_snapshot"].lower(), item["title_snapshot"].lower()))
        return pinned_items

    async def scan_server_current_pinned_threads(self, guild: discord.Guild) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for channel in getattr(guild, "channels", []):
            if isinstance(channel, discord.ForumChannel):
                items.extend(await self.scan_forum_current_pinned_threads(guild, channel))
        items.sort(key=lambda item: (item["parent_name_snapshot"].lower(), item["title_snapshot"].lower()))
        return items

    def _thread_to_scan_item(
        self,
        guild: discord.Guild,
        forum: discord.ForumChannel,
        thread: discord.Thread,
        is_already_watched: bool,
    ) -> dict[str, Any]:
        return {
            "thread_id": int(thread.id),
            "title_snapshot": _truncate(thread.name, 200),
            "thread_link_snapshot": self._build_thread_link_from_thread(thread),
            "parent_id": int(forum.id),
            "parent_name_snapshot": forum.name,
            "forum_mention": forum.mention,
            "is_already_watched": is_already_watched,
        }

    def import_scan_items(
        self,
        guild_id: int,
        items: list[dict[str, Any]],
        added_by: int,
        *,
        source: str,
        only_new: bool,
    ) -> dict[str, Any]:
        selected_items = [item for item in items if (not only_new or not item.get("is_already_watched", False))]
        records = [self._scan_item_to_record(item, added_by, source) for item in selected_items]
        add_result = self.store.add_thread_records(guild_id, records)
        checked_at = utc_now_iso()
        update_results = [
            {
                "thread_id": item["thread_id"],
                "title_snapshot": item["title_snapshot"],
                "thread_link_snapshot": item["thread_link_snapshot"],
                "parent_id": item.get("parent_id"),
                "parent_name_snapshot": item.get("parent_name_snapshot"),
                "checked_at": checked_at,
                "status": "already_pinned",
                "error": None,
            }
            for item in selected_items
        ]
        if update_results:
            self.store.update_check_results(guild_id, update_results, checked_at=checked_at, summary=None)

        return {
            "processed": len(selected_items),
            "added_count": len(add_result["added"]),
            "skipped_count": len(add_result["skipped"]),
            "added_ids": [record["thread_id"] for record in add_result["added"]],
            "skipped_ids": [record["thread_id"] for record in add_result["skipped"]],
        }

    def _format_result_line(self, item: dict[str, Any], index: int) -> str:
        emoji = STATUS_EMOJI.get(item.get("status"), "•")
        label = STATUS_LABEL.get(item.get("status"), "未知状态")
        title = _truncate(item.get("title_snapshot"), 40)
        link = item.get("thread_link_snapshot")
        if link:
            title = f"[{title}]({link})"
        return f"`{index:02d}` {emoji} {title} ｜ {label}"

    def build_scan_results_embed(
        self,
        guild_id: int,
        items: list[dict[str, Any]],
        page: int,
        notice: Optional[str] = None,
    ) -> discord.Embed:
        guild = self.bot.get_guild(guild_id)
        title = "🔎 本服务器当前论坛置顶帖扫描结果"
        embed = discord.Embed(title=title, color=discord.Color.blurple())
        if guild is not None:
            embed.description = f"**服务器**：{guild.name}\n**用途**：先查看当前所有置顶帖链接，再决定是否一键加入巡检列表。"
        else:
            embed.description = "先查看当前所有置顶帖链接，再决定是否一键加入巡检列表。"

        forum_count = len({item.get("parent_id") for item in items if item.get("parent_id") is not None})
        total = len(items)
        watched = sum(1 for item in items if item.get("is_already_watched", False))
        addable = total - watched
        total_pages = max(1, math.ceil(total / PAGE_SIZE))
        page = max(0, min(page, total_pages - 1))
        start = page * PAGE_SIZE
        page_items = items[start : start + PAGE_SIZE]

        embed.add_field(name="扫描论坛数", value=str(forum_count), inline=True)
        embed.add_field(name="扫描到置顶帖", value=str(total), inline=True)
        embed.add_field(name="已在巡检列表", value=str(watched), inline=True)
        embed.add_field(name="可新增", value=str(addable), inline=True)

        if notice:
            embed.add_field(name="最近操作", value=notice, inline=False)

        if not page_items:
            embed.add_field(name="本页内容", value="当前没有可显示的置顶帖。", inline=False)
        else:
            lines = []
            for index, item in enumerate(page_items, start=start + 1):
                mark = "✅" if item.get("is_already_watched", False) else "🆕"
                title_text = _truncate(item.get("title_snapshot"), 45)
                lines.append(
                    f"`{index:02d}` {mark} **论坛**：{item.get('forum_mention') or item.get('parent_name_snapshot', '未知论坛')}\n"
                    f"└ [{title_text}]({item.get('thread_link_snapshot')})"
                )
            embed.add_field(name="扫描明细", value="\n".join(lines), inline=False)

        embed.set_footer(text=f"第 {page + 1}/{total_pages} 页｜🆕 可导入｜✅ 已在巡检列表")
        return embed

    def build_watch_list_embed(
        self,
        guild_id: int,
        items: list[dict[str, Any]],
        page: int,
        notice: Optional[str] = None,
        forum_id: Optional[int] = None,
    ) -> discord.Embed:
        guild = self.bot.get_guild(guild_id)
        config = self.store.load(guild_id)
        title = "📚 置顶巡检列表"
        if forum_id and guild is not None:
            forum = guild.get_channel(forum_id)
            if forum:
                title = f"📚 置顶巡检列表 - {forum.name}"

        embed = discord.Embed(title=title, color=discord.Color.blurple())
        all_items = self.store.get_threads(guild_id)
        summary = config.get("last_summary", {})
        report_channel_id = config.get("report_channel_id")
        report_channel_text = f"<#{report_channel_id}>" if report_channel_id else "未设置"
        enable_text = "🟢 开启" if config.get("enabled", True) else "🔴 关闭"
        forum_count = len({item.get("parent_id") for item in all_items if item.get("parent_id") is not None})
        current_count_label = "当前视图帖子数" if forum_id else "监控帖子数"

        embed.add_field(name="巡检状态", value=enable_text, inline=True)
        embed.add_field(name="巡检周期", value="6 小时", inline=True)
        embed.add_field(name="报告频道", value=report_channel_text, inline=True)
        embed.add_field(name="监控总数", value=str(len(all_items)), inline=True)
        embed.add_field(name="涉及论坛数", value=str(forum_count), inline=True)
        embed.add_field(name=current_count_label, value=str(len(items)), inline=True)
        embed.add_field(name="最近巡检时间", value=_format_iso_time(config.get("last_check_at")), inline=True)
        embed.add_field(name="最近巡检摘要", value=_build_summary_text(summary), inline=False)

        if notice:
            embed.add_field(name="最近操作", value=notice, inline=False)

        total_pages = max(1, math.ceil(len(items) / PAGE_SIZE))
        page = max(0, min(page, total_pages - 1))
        start = page * PAGE_SIZE
        page_items = items[start : start + PAGE_SIZE]

        if not page_items:
            embed.add_field(name="本页内容", value="当前没有可显示的巡检帖子。", inline=False)
        else:
            lines = []
            for index, item in enumerate(page_items, start=start + 1):
                status = item.get("last_status") or "pending"
                emoji = STATUS_EMOJI.get(status, "⏳")
                status_text = STATUS_LABEL.get(status, "未知状态")
                source_text = SOURCE_LABEL.get(item.get("source"), item.get("source") or "未知来源")
                title_text = _truncate(item.get("title_snapshot"), 45)
                link = item.get("thread_link_snapshot")
                title_display = f"[{title_text}]({link})" if link else title_text
                lines.append(
                    f"`{index:02d}` {emoji} {title_display}\n"
                    f"└ 论坛：{item.get('parent_name_snapshot', '未知论坛')} ｜ 来源：{source_text} ｜ 最近：{status_text} ｜ {_format_iso_relative(item.get('last_checked_at'))}"
                )
            embed.add_field(name="巡检明细", value="\n".join(lines), inline=False)

        if guild is not None:
            embed.description = f"**服务器**：{guild.name}"
        embed.set_footer(text=f"第 {page + 1}/{total_pages} 页｜列表只展示最近一次巡检状态")
        return embed

    def build_report_embed(self, guild: discord.Guild, result: dict[str, Any]) -> discord.Embed:
        summary = result["summary"]
        trigger_text = TRIGGER_LABEL.get(result["trigger"], result["trigger"])
        embed = discord.Embed(title="📌 置顶巡检报告", color=discord.Color.blue())
        embed.description = (
            f"**服务器**：{guild.name}\n"
            f"**触发方式**：{trigger_text}\n"
            f"**巡检时间**：{_format_iso_time(result['checked_at'])}"
        )
        embed.add_field(name="汇总", value=_build_summary_text(summary), inline=False)

        preview_lines = [
            self._format_result_line(item, index)
            for index, item in enumerate(result["results"][:10], start=1)
        ]
        if preview_lines:
            embed.add_field(name="明细预览", value="\n".join(preview_lines), inline=False)
        if len(result["results"]) > 10:
            embed.set_footer(text=f"仅展示前 10 项，实际共 {len(result['results'])} 项")
        return embed


async def setup(bot: commands.Bot):
    await bot.add_cog(ThreadPinWatchCommands(bot))
