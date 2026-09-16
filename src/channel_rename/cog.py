"""/改改的名：普通成员可用，频道访问范围由 Discord 集成权限控制。"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from src.channel_rename.core import (
    MAX_RENAMES,
    ChannelRenameStore,
    build_channel_name,
)


class ChannelRenameCommands(commands.Cog):
    def __init__(self, bot, *, store: Optional[ChannelRenameStore] = None):
        self.bot = bot
        self.logger = getattr(bot, "logger", None) or logging.getLogger("bot")
        self.name = "频道改名"
        self.store = store if store is not None else ChannelRenameStore()
        # Keep locks and in-flight work across this Cog's hot reload on one bot.
        states = bot.__dict__.setdefault("_channel_rename_states", {})
        self._state = states.setdefault(
            str(self.store.db_path.resolve()),
            {"locks": {}, "pending": {}, "tasks": set()},
        )

    @app_commands.command(
        name="改改的名", description="修改当前频道名称，同频道 10 分钟内最多成功 2 次"
    )
    @app_commands.guild_only()
    @app_commands.describe(
        新名称="新的频道名称（填写 Emoji 时自动添加丨分隔符）",
        emoji="可选：1 个 Unicode Emoji，显示为 Emoji丨新名称",
    )
    async def rename_channel(
        self, interaction: discord.Interaction, 新名称: str, emoji: Optional[str] = None
    ):
        if isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "❌ 此命令不支持修改子区或帖子名称，请在普通频道中使用。",
                ephemeral=True,
            )
            return
        if interaction.guild is None or not isinstance(
            interaction.channel, discord.abc.GuildChannel
        ):
            await interaction.response.send_message(
                "❌ 请在服务器频道中使用此命令。", ephemeral=True
            )
            return
        try:
            final_name = build_channel_name(
                新名称,
                emoji,
                lowercase=isinstance(interaction.channel, discord.TextChannel),
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        # Acknowledge before waiting on a busy channel or Discord's own limiter.
        await interaction.response.defer(ephemeral=True, thinking=True)
        task = asyncio.create_task(
            self._rename_and_notify(interaction, final_name, emoji)
        )
        self._state["tasks"].add(task)
        task.add_done_callback(self._operation_done)
        # Cancellation of an Interaction/Cog must not release the lock between
        # the successful API response and committing its success record.
        await asyncio.shield(task)

    def _operation_done(self, task):
        self._state["tasks"].discard(task)
        if not task.cancelled() and task.exception() is not None:
            exc = task.exception()
            self.logger.error(
                "频道改名任务异常", exc_info=(type(exc), exc, exc.__traceback__)
            )

    async def _reply(self, interaction, message):
        try:
            await interaction.followup.send(message, ephemeral=True)
        except Exception:
            self.logger.exception(
                "频道改名：发送操作者提示失败，channel_id=%s", interaction.channel.id
            )

    async def _rename_and_notify(self, interaction, final_name, emoji):
        guild_id, channel_id = interaction.guild.id, interaction.channel.id
        lock = self._state["locks"].setdefault(channel_id, asyncio.Lock())
        async with lock:
            try:
                # A confirmed edit with a failed DB write must be persisted
                # before this channel can accept any further changes.
                pending = self._state["pending"].get(channel_id)
                if pending is not None:
                    await self.store.record_success(
                        guild_id, channel_id, pending[1], event_id=pending[0]
                    )
                    del self._state["pending"][channel_id]
                if final_name == interaction.channel.name:
                    await self._reply(
                        interaction, "ℹ️ 新名称与当前频道名称完全相同，本次不占用次数。"
                    )
                    return
                window = await self.store.get_window(guild_id, channel_id)
            except Exception:
                self.logger.exception(
                    "频道改名：数据库读取或恢复失败，channel_id=%s", channel_id
                )
                await self._reply(
                    interaction, "❌ 改名数据库暂时不可用，请稍后再试；本次未修改频道。"
                )
                return
            if window.count >= MAX_RENAMES:
                minutes, seconds = divmod(window.retry_after, 60)
                await self._reply(
                    interaction,
                    f"⏳ 这个频道在最近 10 分钟内已经成功改名 2 次，请等待 {minutes}分{seconds}秒 后再试。",
                )
                return
            try:
                # discord.py edit() returns a new channel object. Fetch the
                # current state inside the lock instead of trusting a queued
                # Interaction's possibly stale gateway cache.
                channel = await interaction.guild.fetch_channel(channel_id)
                old_name = channel.name
                if final_name == old_name:
                    await self._reply(
                        interaction, "ℹ️ 新名称与当前频道名称完全相同，本次不占用次数。"
                    )
                    return
                updated = await channel.edit(
                    name=final_name,
                    reason=f"/改改的名 操作者 ID: {interaction.user.id}",
                )
            except discord.Forbidden:
                self.logger.warning(
                    "频道改名权限不足，channel_id=%s", channel_id, exc_info=True
                )
                await self._reply(
                    interaction,
                    "❌ 机器人缺少“管理频道”权限或无法管理该频道，本次不占用次数。",
                )
                return
            except discord.HTTPException:
                self.logger.exception(
                    "Discord API 修改频道失败，channel_id=%s", channel_id
                )
                await self._reply(
                    interaction, "❌ Discord API 修改失败，本次不占用次数。"
                )
                return
            except Exception:
                self.logger.exception("频道改名失败，channel_id=%s", channel_id)
                await self._reply(
                    interaction, "❌ 频道改名失败，未确认成功，本次不占用次数。"
                )
                return
            actual_name = getattr(updated, "name", None)
            if not isinstance(actual_name, str) or not actual_name:
                await self._reply(
                    interaction, "ℹ️ Discord 未确认频道名称发生变化，本次不占用次数。"
                )
                return
            if actual_name == old_name:
                await self._reply(
                    interaction,
                    "ℹ️ Discord 将输入规范化后与当前频道名称相同，本次不占用次数。",
                )
                return

            renamed_at = self.store.clock()
            event_id = uuid.uuid4().hex
            self._state["pending"][channel_id] = (event_id, renamed_at)
            database_failed = False
            try:
                await self.store.record_success(
                    guild_id, channel_id, renamed_at, event_id=event_id
                )
                del self._state["pending"][channel_id]
            except Exception:
                database_failed = True
                self.logger.exception(
                    "频道名称已修改但成功记录写入失败，暂停该频道后续改名；"
                    "guild_id=%s channel_id=%s renamed_at=%s",
                    guild_id,
                    channel_id,
                    renamed_at,
                )

        # Notification failure cannot undo an already successful rename.
        embed = discord.Embed(
            title="✅ 频道名称已修改",
            colour=discord.Colour.green(),
            timestamp=datetime.fromtimestamp(renamed_at, tz=timezone.utc),
        )
        embed.add_field(name="📋 原名称", value=old_name, inline=False)
        embed.add_field(name="✏️ 新名称", value=actual_name, inline=False)
        embed.add_field(name="👤 操作者", value=interaction.user.mention, inline=False)
        embed.add_field(name="😀 自定义 Emoji", value=emoji or "未设置", inline=False)
        embed.add_field(
            name="⚠️ 注意事项",
            value="同一频道 10 分钟内最多成功修改 2 次。\nDiscord API 仍可能存在额外的速率限制。",
            inline=False,
        )
        embed.set_footer(text="频道修改时间")
        message = "✅ 频道名称已修改。"
        try:
            await channel.send(
                embed=embed, allowed_mentions=discord.AllowedMentions.none()
            )
        except Exception:
            self.logger.exception(
                "频道改名成功，但公开面板发送失败，channel_id=%s", channel_id
            )
            message = "频道名称已修改，但机器人没能发送公开的改名面板。"
        if actual_name != final_name:
            message += f"\nℹ️ Discord 已将名称规范化为「{actual_name}」。"
        if database_failed:
            message += "\n⚠️ 数据库记录写入失败，该频道后续改名已暂停，请联系维护者检查日志和数据库。"
        await self._reply(interaction, message)


async def setup(bot):
    await bot.add_cog(ChannelRenameCommands(bot))
