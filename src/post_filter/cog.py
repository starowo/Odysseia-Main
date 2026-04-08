"""论坛新帖违禁词：近 N 天内通过 verify 的用户，扫描楼主首若干条消息；命中则锁帖归档并转发管理频道。
未配置监视论坛列表时，默认监听本服务器所有论坛频道。
新开贴由 on_thread_create（可配扫描延迟）与楼主后续发言 on_message 共同覆盖，避免漏检。"""

from __future__ import annotations

import asyncio
import datetime
from typing import List, Sequence

import discord
from discord import app_commands
from discord.ext import commands

from src.post_filter.storage import load_guild_config, save_guild_config
from src.utils.auth import guild_only, is_admin
from src.verify.database import VerifyDatabase


def _message_plain_text(message: discord.Message) -> str:
    parts: List[str] = []
    if message.content:
        parts.append(message.content)
    for em in message.embeds:
        if em.title:
            parts.append(em.title)
        if em.description:
            parts.append(em.description)
        if em.footer and em.footer.text:
            parts.append(em.footer.text)
    return "\n".join(parts)


def _find_matches(text: str, keywords: Sequence[str]) -> List[str]:
    if not text or not keywords:
        return []
    folded = text.casefold()
    found: List[str] = []
    for kw in keywords:
        if not str(kw).strip():
            continue
        if str(kw).casefold() in folded:
            found.append(str(kw))
    return found


class PostFilterCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = getattr(bot, "logger", None)
        self.name = "新帖违禁过滤"
        self.verify_db = VerifyDatabase()

    post_filter = app_commands.Group(
        name="新帖违禁过滤",
        description="联动验证记录，检查新验证用户楼主消息违禁词（管理员）",
    )

    @commands.Cog.listener()
    async def on_ready(self):
        await self.verify_db.init()
        if self.logger:
            self.logger.info("新帖违禁过滤：数据库已就绪")

    async def _collect_op_messages(
        self, thread: discord.Thread, owner_id: int, total_limit: int
    ) -> List[discord.Message]:
        out: List[discord.Message] = []
        async for msg in thread.history(limit=100, oldest_first=True):
            if msg.author.id != owner_id:
                continue
            out.append(msg)
            if len(out) >= total_limit:
                break
        return out

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        asyncio.create_task(self._scan_forum_thread(thread, initial_delay=True))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        ch = message.channel
        if not isinstance(ch, discord.Thread):
            return
        parent = ch.parent
        if not isinstance(parent, discord.ForumChannel):
            return
        if ch.owner_id != message.author.id:
            return

        cfg = load_guild_config(message.guild.id)
        if not cfg.get("enabled"):
            return
        forum_ids = cfg.get("forum_channel_ids") or []
        if forum_ids:
            watched = {int(x) for x in forum_ids}
            if parent.id not in watched:
                return

        keywords = [str(k).strip() for k in cfg.get("keywords", []) if str(k).strip()]
        if not keywords:
            return

        asyncio.create_task(self._scan_forum_thread(ch, initial_delay=False))

    async def _scan_forum_thread(self, thread: discord.Thread, *, initial_delay: bool):
        try:
            guild = thread.guild
            if not guild:
                return

            cfg = load_guild_config(guild.id)
            if not cfg.get("enabled"):
                return

            parent = thread.parent
            if not isinstance(parent, discord.ForumChannel):
                return

            forum_ids = cfg.get("forum_channel_ids") or []
            if forum_ids:
                watched = {int(x) for x in forum_ids}
                if parent.id not in watched:
                    return

            owner_id = thread.owner_id
            if not owner_id:
                return

            keywords = [str(k).strip() for k in cfg.get("keywords", []) if str(k).strip()]
            if not keywords:
                return

            if initial_delay:
                delay = max(0, min(60, int(cfg.get("scan_delay_seconds", 4))))
                if delay:
                    await asyncio.sleep(delay)

            try:
                ch = await guild.fetch_channel(thread.id)
            except (discord.NotFound, discord.Forbidden):
                return
            if not isinstance(ch, discord.Thread):
                return
            thread = ch
            parent = thread.parent
            if not isinstance(parent, discord.ForumChannel):
                return

            if thread.archived and thread.locked:
                return

            user_data = await self.verify_db.get_user_data(guild.id, owner_id)
            last_success = user_data.get("last_success")
            if not last_success:
                return

            try:
                passed_at = datetime.datetime.fromisoformat(last_success)
            except ValueError:
                return
            if passed_at.tzinfo is None:
                passed_at = passed_at.replace(tzinfo=datetime.timezone.utc)

            now = datetime.datetime.now(datetime.timezone.utc)
            window_days = max(1, min(365, int(cfg.get("verify_window_days", 14))))
            if (now - passed_at) >= datetime.timedelta(days=window_days):
                return

            op_limit = max(1, min(30, int(cfg.get("op_message_limit", 5))))
            messages = await self._collect_op_messages(thread, owner_id, op_limit)

            hit_keywords: List[str] = []
            for msg in messages:
                plain = _message_plain_text(msg)
                hit_keywords.extend(_find_matches(plain, keywords))

            if not hit_keywords:
                return

            uniq_kw = list(dict.fromkeys(hit_keywords))

            try:
                fresh = await guild.fetch_channel(thread.id)
            except (discord.NotFound, discord.Forbidden):
                return
            if isinstance(fresh, discord.Thread):
                thread = fresh
                parent = thread.parent if isinstance(thread.parent, discord.ForumChannel) else parent
            if thread.archived and thread.locked:
                return

            try:
                await thread.edit(
                    locked=True,
                    archived=True,
                    reason="新验证用户发帖违禁词自动处置",
                )
            except discord.HTTPException as e:
                if self.logger:
                    self.logger.warning("post_filter: 锁帖归档失败 thread=%s: %s", thread.id, e)
                return

            admin_ch_id = cfg.get("admin_channel_id")
            if not admin_ch_id:
                return

            dest = guild.get_channel(int(admin_ch_id))
            if not dest or not isinstance(dest, discord.abc.Messageable):
                return

            first_id = messages[0].id if messages else thread.id
            jump = f"https://discord.com/channels/{guild.id}/{thread.id}/{first_id}"
            emb = discord.Embed(
                title="新帖违禁过滤 · 已锁帖归档",
                description=(
                    f"论坛: {getattr(parent, 'mention', None) or parent.name}\n"
                    f"子区: {thread.mention}\n"
                    f"作者: <@{owner_id}>\n"
                    f"验证通过: `{passed_at.isoformat()}`"
                ),
                color=discord.Color.orange(),
            )
            emb.add_field(
                name="命中关键词",
                value=", ".join(uniq_kw)[:1024] or "—",
                inline=False,
            )
            emb.add_field(name="跳转", value=jump, inline=False)

            send_kw: dict = {
                "embed": emb,
                "allowed_mentions": discord.AllowedMentions.none(),
            }
            try:
                await dest.send(**send_kw, silent=True)
            except TypeError:
                await dest.send(**send_kw)
            except discord.HTTPException:
                pass

        except asyncio.CancelledError:
            raise
        except Exception:
            if self.logger:
                self.logger.exception("post_filter 扫描异常")

    def _save(self, guild_id: int, cfg: dict) -> None:
        save_guild_config(guild_id, cfg)

    @post_filter.command(name="查看配置", description="查看当前服务器过滤配置")
    @is_admin()
    @guild_only()
    async def pf_status(self, interaction: discord.Interaction):
        cfg = load_guild_config(interaction.guild.id)
        forums = cfg.get("forum_channel_ids") or []
        lines = [
            f"**启用:** {cfg.get('enabled')}",
            f"**验证窗口:** {cfg.get('verify_window_days')} 天",
            f"**扫描延迟:** {cfg.get('scan_delay_seconds')} 秒",
            f"**楼主消息条数:** {cfg.get('op_message_limit')}",
            f"**管理频道 ID:** {cfg.get('admin_channel_id')}",
            f"**监视论坛:** {', '.join(str(i) for i in forums) or '全部（未配置列表时默认监听本服务器所有论坛）'}",
            f"**关键词数量:** {len(cfg.get('keywords') or [])}",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @post_filter.command(name="启用", description="开启本服务器新帖过滤")
    @is_admin()
    @guild_only()
    async def pf_enable(self, interaction: discord.Interaction):
        cfg = load_guild_config(interaction.guild.id)
        cfg["enabled"] = True
        self._save(interaction.guild.id, cfg)
        await interaction.response.send_message("✅ 已启用新帖违禁过滤。", ephemeral=True)

    @post_filter.command(name="禁用", description="关闭本服务器新帖过滤")
    @is_admin()
    @guild_only()
    async def pf_disable(self, interaction: discord.Interaction):
        cfg = load_guild_config(interaction.guild.id)
        cfg["enabled"] = False
        self._save(interaction.guild.id, cfg)
        await interaction.response.send_message("✅ 已禁用新帖违禁过滤。", ephemeral=True)

    @post_filter.command(name="管理频道", description="设置转发告警的文字频道")
    @is_admin()
    @guild_only()
    async def pf_admin_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        cfg = load_guild_config(interaction.guild.id)
        cfg["admin_channel_id"] = channel.id
        self._save(interaction.guild.id, cfg)
        await interaction.response.send_message(
            f"✅ 管理频道已设为 {channel.mention}",
            ephemeral=True,
        )

    @post_filter.command(
        name="添加监视论坛",
        description="仅监视列出的论坛；若列表为空则默认监听本服务器全部论坛",
    )
    @is_admin()
    @guild_only()
    async def pf_add_forum(
        self,
        interaction: discord.Interaction,
        forum: discord.ForumChannel,
    ):
        cfg = load_guild_config(interaction.guild.id)
        ids: List[int] = [int(x) for x in cfg.get("forum_channel_ids", [])]
        if forum.id not in ids:
            ids.append(forum.id)
        cfg["forum_channel_ids"] = ids
        self._save(interaction.guild.id, cfg)
        await interaction.response.send_message(
            f"✅ 已监视论坛 {forum.mention}",
            ephemeral=True,
        )

    @post_filter.command(
        name="移除监视论坛",
        description="从监视列表移除；移除至列表为空后恢复为监听全部论坛",
    )
    @is_admin()
    @guild_only()
    async def pf_remove_forum(
        self,
        interaction: discord.Interaction,
        forum: discord.ForumChannel,
    ):
        cfg = load_guild_config(interaction.guild.id)
        ids = [int(x) for x in cfg.get("forum_channel_ids", []) if int(x) != forum.id]
        cfg["forum_channel_ids"] = ids
        self._save(interaction.guild.id, cfg)
        await interaction.response.send_message(
            f"✅ 已移除 {forum.mention}",
            ephemeral=True,
        )

    @post_filter.command(name="添加关键词", description="增加违禁关键词（子串匹配，不区分大小写）")
    @is_admin()
    @guild_only()
    async def pf_add_keyword(self, interaction: discord.Interaction, keyword: str):
        kw = keyword.strip()
        if not kw:
            await interaction.response.send_message("❌ 关键词不能为空", ephemeral=True)
            return
        cfg = load_guild_config(interaction.guild.id)
        existing: List[str] = list(cfg.get("keywords", []))
        if any(e.casefold() == kw.casefold() for e in existing):
            await interaction.response.send_message("⚠️ 已存在同名关键词", ephemeral=True)
            return
        existing.append(kw)
        cfg["keywords"] = existing
        self._save(interaction.guild.id, cfg)
        await interaction.response.send_message(f"✅ 已添加：`{kw}`", ephemeral=True)

    @post_filter.command(name="移除关键词", description="移除违禁关键词")
    @is_admin()
    @guild_only()
    async def pf_remove_keyword(self, interaction: discord.Interaction, keyword: str):
        kw = keyword.strip()
        cfg = load_guild_config(interaction.guild.id)
        existing: List[str] = list(cfg.get("keywords", []))
        new_list = [e for e in existing if e.casefold() != kw.casefold()]
        if len(new_list) == len(existing):
            await interaction.response.send_message("❌ 未找到该关键词", ephemeral=True)
            return
        cfg["keywords"] = new_list
        self._save(interaction.guild.id, cfg)
        await interaction.response.send_message("✅ 已移除。", ephemeral=True)

    @post_filter.command(name="关键词列表", description="列出当前违禁关键词")
    @is_admin()
    @guild_only()
    async def pf_list_keywords(self, interaction: discord.Interaction):
        cfg = load_guild_config(interaction.guild.id)
        kws: List[str] = list(cfg.get("keywords", []))
        if not kws:
            await interaction.response.send_message("（无）", ephemeral=True)
            return
        text = "\n".join(f"- `{k}`" for k in kws)
        if len(text) > 3800:
            text = text[:3800] + "\n…"
        await interaction.response.send_message(text, ephemeral=True)

    @post_filter.command(name="验证窗口", description="通过验证后多少天内发帖会触发检查（默认 14）")
    @is_admin()
    @guild_only()
    @app_commands.describe(days="天数，1–365")
    async def pf_verify_window(
        self,
        interaction: discord.Interaction,
        days: app_commands.Range[int, 1, 365],
    ):
        cfg = load_guild_config(interaction.guild.id)
        cfg["verify_window_days"] = int(days)
        self._save(interaction.guild.id, cfg)
        await interaction.response.send_message(f"✅ 验证窗口已设为 **{days}** 天。", ephemeral=True)

    @post_filter.command(
        name="扫描延迟",
        description="仅作用于新开贴(on_thread_create)：等待几秒再扫，合并楼主首条连发；后续发言靠消息事件即时扫描",
    )
    @is_admin()
    @guild_only()
    @app_commands.describe(seconds="秒，0–60")
    async def pf_scan_delay(
        self,
        interaction: discord.Interaction,
        seconds: app_commands.Range[int, 0, 60],
    ):
        cfg = load_guild_config(interaction.guild.id)
        cfg["scan_delay_seconds"] = int(seconds)
        self._save(interaction.guild.id, cfg)
        await interaction.response.send_message(f"✅ 扫描延迟已设为 **{seconds}** 秒。", ephemeral=True)

    @post_filter.command(name="楼主消息条数", description="扫描楼主按时间最早的前几条消息")
    @is_admin()
    @guild_only()
    @app_commands.describe(count="条数，1–30")
    async def pf_op_limit(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, 30],
    ):
        cfg = load_guild_config(interaction.guild.id)
        cfg["op_message_limit"] = int(count)
        self._save(interaction.guild.id, cfg)
        await interaction.response.send_message(f"✅ 楼主扫描条数已设为 **{count}**。", ephemeral=True)