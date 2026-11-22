"""反商业化（/aff 阶梯处罚、跨服累计、补录与撤销）Cog。"""
from __future__ import annotations

import asyncio
import datetime
import io
import json
import pathlib
import uuid
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from src.anticommercial.config import (
    SafeFormatDict,
    can_cross_guild_operate,
    format_punish_summary,
    get_anticommercial_config,
    parse_duration,
    resolve_scope_guild_ids,
    resolve_tier,
    validate_all_tiers,
)
from src.anticommercial.database import AffPunishDatabase
from src.utils.auth import guild_only, is_admin_member
from src.utils.config_helper import get_config_value
from src.utils.confirm_view import confirm_view_embed
from src.utils.dm import send_dm
from src.utils.punishment_announcement import (
    archive_punishment,
    send_punishment_announcement,
)

ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def sanitize_image_filename(original_name: Optional[str], prefix: str) -> str:
    """将附件文件名规范化为安全的纯 ASCII 文件名，防止 attachment:// URL 400 错误。"""
    ext = ".png"
    if original_name:
        suffix = pathlib.Path(original_name).suffix.lower()
        if suffix in ALLOWED_IMAGE_EXTS:
            ext = suffix
    return f"{prefix}{ext}"


def _parse_iso_utc(iso_str: str) -> datetime.datetime:
    normalized = str(iso_str).strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    dt = datetime.datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def cleanup_warn_files_for_punish(
    guild_id: int,
    user_id: int,
    punish_id: Optional[str],
    base_data_dir: pathlib.Path = pathlib.Path("data"),
) -> bool:
    """清理指定 punish_id 关联的定时警告文件，并返回该用户是否仍有其他未到期警告。"""
    warn_dir = base_data_dir / "warn" / str(guild_id)
    if not warn_dir.exists():
        return False

    now = datetime.datetime.now(datetime.timezone.utc)
    has_other_active_warn = False

    for file_path in warn_dir.glob("*.json"):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        if punish_id and str(data.get("punish_id", "")) == str(punish_id):
            file_path.unlink(missing_ok=True)
            continue

        if int(data.get("user_id", 0) or 0) == int(user_id) and data.get("until"):
            try:
                until_dt = _parse_iso_utc(data["until"])
                if until_dt > now:
                    has_other_active_warn = True
            except Exception:
                continue

    return has_other_active_warn


class AntiCommercialCommands(commands.Cog):
    """提供 /aff 命令组：处罚、查询、补录、撤销。"""

    def __init__(self, bot: discord.Client, data_dir: Optional[pathlib.Path] = None):
        self.bot = bot
        self.logger = getattr(bot, "logger", None)
        self.name = "aff处罚管理"
        self._data_dir = data_dir or pathlib.Path("data")
        self._db_instances: Dict[str, AffPunishDatabase] = {}
        self._user_locks: Dict[int, asyncio.Lock] = {}

    anticommercial = app_commands.Group(name="aff", description="合规渠道 aff 阶梯处罚与记录管理")

    def get_guild_config(self, key: str, guild_id: Optional[int] = None, default: Any = None) -> Any:
        return get_config_value(key, guild_id, default)

    def get_ac_config(self, guild_id: Optional[int] = None) -> Dict[str, Any]:
        return get_anticommercial_config(guild_id)

    def _get_db(self, cfg: Dict[str, Any]) -> AffPunishDatabase:
        db_path = str(cfg.get("db_path") or (self._data_dir / "anticommercial" / "aff_records.db"))
        db = self._db_instances.get(db_path)
        if db is None:
            db = AffPunishDatabase(db_path)
            self._db_instances[db_path] = db
        return db

    def _get_user_lock(self, user_id: int) -> asyncio.Lock:
        lock = self._user_locks.get(int(user_id))
        if lock is None:
            lock = asyncio.Lock()
            self._user_locks[int(user_id)] = lock
        return lock

    @staticmethod
    def _is_ac_admin(member: discord.Member, cfg: Dict[str, Any]) -> bool:
        if is_admin_member(member):
            return True
        admin_role_ids = set(cfg.get("admin_role_ids") or [])
        if not admin_role_ids:
            return False
        return any(role.id in admin_role_ids for role in getattr(member, "roles", ()))

    @staticmethod
    def _is_ac_committee(member: discord.Member, cfg: Dict[str, Any]) -> bool:
        role_ids = set(cfg.get("role_ids") or [])
        if not role_ids:
            return False
        return any(role.id in role_ids for role in getattr(member, "roles", ()))

    def _has_permission(self, member: discord.Member, cfg: Dict[str, Any]) -> bool:
        return self._is_ac_admin(member, cfg) or self._is_ac_committee(member, cfg)

    def _validate_target_member(
        self,
        operator: discord.Member,
        target: discord.Member,
        cfg: Dict[str, Any],
    ) -> Optional[str]:
        if target.id == operator.id:
            return "❌ 不能对自己执行此操作"
        if target.bot:
            return "❌ 不能对机器人执行此操作"
        if self._is_ac_admin(target, cfg):
            return "❌ 不能对管理组成员执行此操作"
        if not self._is_ac_admin(operator, cfg) and self._is_ac_committee(target, cfg):
            return "❌ 不能对反商业化委员执行此操作"
        return None

    @staticmethod
    def _precheck_bot_hierarchy(
        guild: discord.Guild,
        target: discord.Member,
        warned_role: Optional[discord.Role],
        mute_seconds: int,
    ) -> Optional[str]:
        if mute_seconds > 0:
            if getattr(guild, "owner_id", None) == target.id:
                return "❌ 无法对服务器所有者执行禁言"
            if getattr(getattr(target, "guild_permissions", None), "administrator", False):
                return "❌ 无法对拥有服务器管理员权限的成员执行禁言"

        me = getattr(guild, "me", None)
        me_pos = getattr(getattr(me, "top_role", None), "position", None)
        if isinstance(me_pos, int):
            if warned_role is not None:
                warn_pos = getattr(warned_role, "position", None)
                if isinstance(warn_pos, int) and me_pos <= warn_pos:
                    return "❌ 机器人身份组层级不高于警告身份组，无法发放警告身份组"
            if mute_seconds > 0:
                target_pos = getattr(getattr(target, "top_role", None), "position", None)
                if isinstance(target_pos, int) and me_pos <= target_pos:
                    return "❌ 机器人身份组层级不高于目标成员，无法执行禁言"
        return None

    def _save_punish_record(self, guild_id: int, record_id: str, record: Dict[str, Any]) -> None:
        payload = dict(record)
        payload["id"] = record_id
        payload["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        punish_dir = self._data_dir / "punish" / str(guild_id)
        punish_dir.mkdir(parents=True, exist_ok=True)
        with open(punish_dir / f"{record_id}.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _save_warn_record(self, guild_id: int, punish_id: str, record: Dict[str, Any]) -> str:
        warn_id = uuid.uuid4().hex[:8]
        payload = dict(record)
        payload["id"] = warn_id
        payload["punish_id"] = punish_id
        payload["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        warn_dir = self._data_dir / "warn" / str(guild_id)
        warn_dir.mkdir(parents=True, exist_ok=True)
        with open(warn_dir / f"{warn_id}.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return warn_id

    @staticmethod
    def _format_beijing_time(iso_str: Optional[str]) -> str:
        if not iso_str:
            return "未知时间"
        try:
            dt = _parse_iso_utc(iso_str)
            bj = dt.astimezone(datetime.timezone(datetime.timedelta(hours=8)))
            return bj.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(iso_str)[:16]

    @staticmethod
    def _build_dm_embed(
        cfg: Dict[str, Any],
        *,
        guild_name: str,
        user_id: int,
        reason: str,
        mute_seconds: int,
        punish_summary: str,
        offense_no: int,
        prior_count: int,
        record_id: str,
    ) -> discord.Embed:
        tiers = cfg.get("tiers") or []
        next_tier_tip = str(cfg.get("next_tier_tip_repeat") or "")
        if prior_count + 1 < len(tiers):
            try:
                _, _, _, next_summary = resolve_tier(tiers, prior_count + 1)
                next_tip_tpl = str(cfg.get("next_tier_tip_first") or "")
                next_tier_tip = next_tip_tpl.format_map(
                    SafeFormatDict(next_punish_summary=next_summary)
                )
            except ValueError:
                next_tier_tip = str(cfg.get("next_tier_tip_repeat") or "")

        title = (
            str(cfg.get("dm_title_mute") or "🔇 禁言与警告处罚")
            if mute_seconds > 0
            else str(cfg.get("dm_title_warn") or "⚠️ 警告处罚")
        )
        body = str(cfg.get("dm_template") or "").format_map(
            SafeFormatDict(
                guild_name=guild_name,
                user_mention=f"<@{user_id}>",
                reason=reason,
                punish_summary=punish_summary,
                offense_no=offense_no,
                next_tier_tip=next_tier_tip,
                rules_url=cfg.get("rules_url", ""),
                appeal_tip=cfg.get("appeal_tip", ""),
                record_id=record_id,
            )
        )
        embed = discord.Embed(
            title=title[:256],
            description=body[:4000],
            color=discord.Color.orange(),
        )
        embed.set_footer(text=f"来自服务器: {guild_name} | 处罚ID: {record_id}"[:2048])
        return embed

    async def mark_revoked_from_admin(
        self,
        punish_id: str,
        revoked_by: int,
        reason: Optional[str] = None,
        guild_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """供 /管理 撤销处罚 调用的联动钩子：若对应反商记录存在，在用户锁内同步标为已撤销。"""
        cfg = self.get_ac_config(guild_id)
        db = self._get_db(cfg)
        existing = await db.get_record(punish_id)
        if existing is None or existing.get("status") == "revoked":
            return None
        async with self._get_user_lock(int(existing["user_id"])):
            return await db.revoke_record(punish_id, revoked_by=revoked_by, revoke_reason=reason)

    async def _read_optional_image(
        self,
        att: Optional[discord.Attachment],
        prefix: str,
        label: str,
    ) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
        if att is None:
            return None, None, None
        try:
            data = await att.read()
            if not data:
                return None, None, f"⚠️ {label}为空文件，已跳过附图"
            safe_name = sanitize_image_filename(getattr(att, "filename", None), prefix)
            return data, safe_name, None
        except Exception as exc:
            if self.logger:
                self.logger.warning(f"读取{label}失败: {exc}")
            return None, None, f"⚠️ {label}读取失败，已跳过该图"

    # ---- 1. /aff 处罚 ----
    @anticommercial.command(name="处罚", description="按有效次数对违规发布 aff 的成员执行阶梯处罚")
    @app_commands.describe(
        member="要处罚的成员",
        reason="处罚原因说明（留空则使用配置的默认原因）",
        img="公示图片（打码证据图，发往公示区及同步服）",
        archive_img="留痕原图（未打码完整证据图，仅发往留痕区；不填则复用公示图片）",
    )
    @app_commands.rename(
        member="成员",
        reason="原因",
        img="公示图片",
        archive_img="留痕原图",
    )
    @guild_only()
    async def aff_punish(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: Optional[app_commands.Range[str, 1, 450]] = None,
        img: Optional[discord.Attachment] = None,
        archive_img: Optional[discord.Attachment] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ 此命令只能在服务器中使用", ephemeral=True)
            return

        cfg = self.get_ac_config(guild.id)
        if not self._has_permission(interaction.user, cfg):
            await interaction.response.send_message(
                "❌ 您不是反商业化委员或管理组成员，无权使用此命令", ephemeral=True
            )
            return

        target_err = self._validate_target_member(interaction.user, member, cfg)
        if target_err:
            await interaction.response.send_message(target_err, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            validate_all_tiers(cfg.get("tiers"))
        except ValueError as exc:
            await interaction.followup.send(f"❌ 反商档位配置无效: {exc}", ephemeral=True)
            return

        db = self._get_db(cfg)
        scope_guild_ids = resolve_scope_guild_ids(guild.id, cfg)
        active_records = await db.get_active_records(member.id, scope_guild_ids)
        prior_count = len(active_records)
        mute_seconds, mute_label, warn_days, punish_summary = resolve_tier(
            cfg.get("tiers"), prior_count
        )

        warned_role = None
        if warn_days > 0:
            warned_role_id = self.get_guild_config("warned_role_id", guild.id, 0)
            warned_role = guild.get_role(int(warned_role_id)) if warned_role_id else None
            if warned_role is None:
                await interaction.followup.send(
                    "❌ 当前服务器未正确配置警告身份组 (warned_role_id)，无法执行附带警告的处罚",
                    ephemeral=True,
                )
                return

        hierarchy_err = self._precheck_bot_hierarchy(guild, member, warned_role, mute_seconds)
        if hierarchy_err:
            await interaction.followup.send(hierarchy_err, ephemeral=True)
            return

        # 在弹出最长 120 秒确认框前预读附件字节，避免临时链接失效或 0 字节文件异常
        pub_bytes, pub_filename, pub_warn = await self._read_optional_image(
            img, "evidence_pub", "公示图片"
        )
        arch_bytes, arch_filename, arch_warn = await self._read_optional_image(
            archive_img, "evidence_archive", "留痕原图"
        )

        offense_no = prior_count + 1
        base_reason = ((reason or "").strip() or str(cfg.get("default_reason") or "违规发布 aff 推广内容"))[:450]
        final_reason = f"[第{offense_no}次aff] {base_reason}"[:500]

        confirm_embed = discord.Embed(
            title="⚖️ aff 处罚确认",
            description="请核对目标成员与历史记录，确认无误后点击「确认」执行。",
            color=discord.Color.orange() if mute_seconds == 0 else discord.Color.red(),
        )
        confirm_embed.set_thumbnail(url=member.display_avatar.url)
        confirm_embed.add_field(name="目标成员", value=f"{member.mention} (`{member.id}`)", inline=False)
        confirm_embed.add_field(name="历史有效记录", value=f"{prior_count} 次", inline=True)
        confirm_embed.add_field(name="本次定档", value=f"第 {offense_no} 次 → **{punish_summary}**", inline=True)
        confirm_embed.add_field(name="原因", value=final_reason[:1024], inline=False)
        if active_records:
            last_rec = active_records[-1]
            confirm_embed.add_field(
                name="最近一次记录",
                value=(
                    f"ID `{last_rec['punish_id']}` · {self._format_beijing_time(last_rec.get('created_at'))} "
                    f"· 服务器 `{last_rec['guild_id']}`\n原因：{last_rec.get('reason') or '无'}"
                )[:1024],
                inline=False,
            )

        if not await confirm_view_embed(interaction, confirm_embed, timeout=120):
            return

        async with self._get_user_lock(member.id):
            latest_active = await db.get_active_records(member.id, scope_guild_ids)
            if len(latest_active) != prior_count:
                await interaction.edit_original_response(
                    content=(
                        f"⚠️ 该成员的有效记录数刚刚发生变化（当前已为 {len(latest_active)} 次），"
                        "为避免重复或错档处罚，本次操作已中止，请重新发起命令。"
                    ),
                    embed=None,
                    view=None,
                )
                return

            did_timeout = False
            try:
                if mute_seconds > 0:
                    await member.timeout(
                        datetime.timedelta(seconds=mute_seconds),
                        reason=final_reason[:512],
                    )
                    did_timeout = True
                if warn_days > 0 and warned_role is not None:
                    sync_cog_ref = self.bot.get_cog("ServerSyncCommands")
                    if sync_cog_ref:
                        sync_cog_ref._mark_guard(guild.id, member.id, warned_role.id, "add")
                    await member.add_roles(warned_role, reason=f"反商aff处罚附加警告 {warn_days} 天")
            except discord.Forbidden:
                if did_timeout:
                    try:
                        await member.timeout(None, reason="回滚未完成的反商处罚")
                    except Exception:
                        pass
                await interaction.edit_original_response(
                    content="❌ 机器人权限不足，无法对该成员执行禁言或添加警告身份组",
                    embed=None,
                    view=None,
                )
                return
            except Exception as exc:
                if did_timeout:
                    try:
                        await member.timeout(None, reason="回滚未完成的反商处罚")
                    except Exception:
                        pass
                await interaction.edit_original_response(
                    content=f"❌ 执行处罚时发生错误: {exc}",
                    embed=None,
                    view=None,
                )
                return

            record_id = uuid.uuid4().hex[:8]
            self._save_punish_record(
                guild.id,
                record_id,
                {
                    "type": "mute",
                    "source": "anticommercial_aff",
                    "user_id": member.id,
                    "moderator_id": interaction.user.id,
                    "reason": final_reason,
                    "warn": warn_days,
                    "warn_days": warn_days,
                    "duration": mute_seconds,
                },
            )
            if warn_days > 0:
                until_iso = (
                    datetime.datetime.now(datetime.timezone.utc)
                    + datetime.timedelta(days=warn_days)
                ).isoformat()
                self._save_warn_record(
                    guild.id,
                    record_id,
                    {
                        "type": "warn",
                        "source": "anticommercial_aff",
                        "user_id": member.id,
                        "moderator_id": interaction.user.id,
                        "reason": final_reason,
                        "until": until_iso,
                    },
                )

            await db.add_record(
                punish_id=record_id,
                user_id=member.id,
                guild_id=guild.id,
                offense_no=offense_no,
                record_type="punish",
                mute_seconds=mute_seconds,
                warn_days=warn_days,
                moderator_id=interaction.user.id,
                reason=final_reason,
            )

        extra_notes: List[str] = [w for w in (pub_warn, arch_warn) if w]

        # 同步处罚到其他关联服务器
        sync_cog = self.bot.get_cog("ServerSyncCommands")
        if sync_cog:
            try:
                await sync_cog.sync_punishment(
                    guild=guild,
                    punishment_type="mute",
                    member=member,
                    moderator=interaction.user,
                    reason=final_reason,
                    duration=mute_seconds if mute_seconds > 0 else None,
                    warn_days=warn_days,
                    punishment_id=record_id,
                    img=img,
                )
            except Exception as exc:
                extra_notes.append("⚠️ 跨服同步发送异常")
                if self.logger:
                    self.logger.error(f"同步反商 aff 处罚失败: {exc}")

        # 发送可配置私信小作文
        try:
            dm_embed = self._build_dm_embed(
                cfg,
                guild_name=guild.name,
                user_id=member.id,
                reason=base_reason,
                mute_seconds=mute_seconds,
                punish_summary=punish_summary,
                offense_no=offense_no,
                prior_count=prior_count,
                record_id=record_id,
            )
            await send_dm(guild, member, embed=dm_embed)
        except discord.HTTPException as exc:
            if isinstance(exc, discord.Forbidden) or getattr(exc, "code", None) == 50007:
                extra_notes.append("⚠️ 用户已关闭私信，私信通知未能送达")
            else:
                extra_notes.append("⚠️ 私信通知发送异常")
                if self.logger:
                    self.logger.warning(f"反商 aff 处罚私信发送失败: {exc}")
        except Exception as exc:
            extra_notes.append("⚠️ 私信通知发送异常")
            if self.logger:
                self.logger.warning(f"反商 aff 处罚私信发送失败: {exc}")

        # 当前频道公示（默认开启，与其他处罚命令保持一致）
        if cfg.get("announce_in_command_channel", True):
            try:
                public_tip = discord.Embed(
                    title="🔇 禁言处罚" if mute_seconds > 0 else "⚠️ 警告处罚",
                    description=f"{member.mention} 因 {final_reason} 被处以 {punish_summary}。请注意遵守社区规则。",
                    color=discord.Color.orange(),
                )
                await interaction.followup.send(embed=public_tip, ephemeral=False)
            except Exception as exc:
                if self.logger:
                    self.logger.warning(f"当前频道发送 aff 处罚公示失败: {exc}")

        # 公示频道 + 留痕频道 + 管理日志（均包裹异常隔离，绝不淹没成功回执）
        announce_channel_id = self.get_guild_config("punish_announce_channel_id", guild.id, 0)
        announce_channel = guild.get_channel(int(announce_channel_id)) if announce_channel_id else None
        moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", guild.id, 0)
        moderation_log_channel = (
            guild.get_channel_or_thread(int(moderation_log_channel_id))
            if moderation_log_channel_id
            else None
        )

        def _make_announce_embed(img_name: Optional[str]) -> discord.Embed:
            emb = discord.Embed(
                title="🔇 禁言处罚" if mute_seconds > 0 else "⚠️ 警告处罚",
                color=discord.Color.orange(),
            )
            if mute_seconds > 0:
                emb.add_field(name="时长", value=mute_label)
            emb.add_field(name="成员", value=f"{member.mention} ({member.id})")
            emb.add_field(name="执行人", value=interaction.user.mention)
            emb.set_thumbnail(url=member.display_avatar.url)
            emb.add_field(name="原因", value=final_reason[:1024], inline=False)
            if warn_days > 0:
                emb.add_field(name="警告", value=f"{warn_days}天", inline=False)
            if img_name:
                emb.set_image(url=f"attachment://{img_name}")
            emb.set_footer(text=f"处罚ID: {record_id}")
            return emb

        pub_embed = _make_announce_embed(pub_filename if pub_bytes else None)
        effective_arch_bytes = arch_bytes if arch_bytes else pub_bytes
        effective_arch_name = arch_filename if arch_bytes else pub_filename
        arch_embed = _make_announce_embed(effective_arch_name if effective_arch_bytes else None)

        if announce_channel is not None:
            pub_file = (
                discord.File(io.BytesIO(pub_bytes), filename=pub_filename)
                if pub_bytes and pub_filename
                else None
            )
            override_arch_file = (
                discord.File(io.BytesIO(arch_bytes), filename=arch_filename)
                if arch_bytes and arch_filename
                else None
            )
            try:
                await send_punishment_announcement(
                    self.bot,
                    guild.id,
                    announce_channel,
                    from_sync=False,
                    get_config=self.get_guild_config,
                    embed=pub_embed,
                    archive_embed=arch_embed if override_arch_file is not None else None,
                    archive_file_override=override_arch_file,
                    file=pub_file,
                )
            except Exception as exc:
                extra_notes.append("⚠️ 公示频道发送失败（处罚与留痕已保存）")
                if self.logger:
                    self.logger.warning(f"发送反商公示失败: {exc}")
        else:
            only_arch_file = (
                discord.File(io.BytesIO(effective_arch_bytes), filename=effective_arch_name)
                if effective_arch_bytes and effective_arch_name
                else None
            )
            await archive_punishment(
                self.bot,
                guild.id,
                None,
                embed=arch_embed,
                file=only_arch_file,
                get_config=self.get_guild_config,
            )

        if moderation_log_channel is not None:
            log_file = (
                discord.File(io.BytesIO(effective_arch_bytes), filename=effective_arch_name)
                if effective_arch_bytes and effective_arch_name
                else None
            )
            try:
                await moderation_log_channel.send(embed=arch_embed, file=log_file)
            except Exception as exc:
                extra_notes.append("⚠️ 管理日志频道发送失败")
                if self.logger:
                    self.logger.warning(f"发送反商管理日志失败: {exc}")
            finally:
                if log_file is not None:
                    log_file.close()

        notes_suffix = ("\n" + "\n".join(f"（{n}）" for n in extra_notes)) if extra_notes else ""
        await interaction.edit_original_response(
            content=(
                f"✅ 已对 {member.mention} 执行 **第 {offense_no} 次** aff 处罚"
                f"（**{punish_summary}**）。处罚ID: `{record_id}`{notes_suffix}"
            ),
            embed=None,
            view=None,
        )

    # ---- 2. /aff 查询 ----
    @anticommercial.command(name="查询", description="查询指定成员的跨服 aff 处罚与补录记录")
    @app_commands.describe(
        member="要查询的服务器成员（与用户ID二选一）",
        user_id="用户ID（成员不在本服时可直接填数字ID）",
    )
    @app_commands.rename(member="成员", user_id="用户id")
    @guild_only()
    async def aff_query(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
        user_id: Optional[str] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ 此命令只能在服务器中使用", ephemeral=True)
            return

        cfg = self.get_ac_config(guild.id)
        if not self._has_permission(interaction.user, cfg):
            await interaction.response.send_message(
                "❌ 您不是反商业化委员或管理组成员，无权使用此命令", ephemeral=True
            )
            return

        target_id, err = self._resolve_target_id(member, user_id)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        db = self._get_db(cfg)
        scope_guild_ids = resolve_scope_guild_ids(guild.id, cfg)
        all_records = await db.get_all_records(target_id, scope_guild_ids)
        active_records = [r for r in all_records if r.get("status") == "active"]
        revoked_records = [r for r in all_records if r.get("status") == "revoked"]

        try:
            _, _, _, next_summary = resolve_tier(cfg.get("tiers"), len(active_records))
        except ValueError:
            next_summary = "配置异常"

        embed = discord.Embed(
            title="📋 aff 记录查询",
            description=(
                f"目标用户：<@{target_id}> (`{target_id}`)\n"
                f"当前有效累计：**{len(active_records)} 次**（已撤销：{len(revoked_records)} 次）\n"
                f"若下次触发处罚将执行：**{next_summary}**"
            ),
            color=discord.Color.blue(),
        )

        if active_records:
            lines = []
            for rec in active_records[:10]:
                tag = "📝补录" if rec.get("record_type") == "backfill" else "⚖️处罚"
                _, m_label = parse_duration(rec.get("mute_seconds", 0))
                summary = (
                    "仅计次"
                    if rec.get("record_type") == "backfill"
                    else format_punish_summary(
                        int(rec.get("mute_seconds", 0) or 0),
                        m_label,
                        int(rec.get("warn_days", 0) or 0),
                    )
                )
                lines.append(
                    f"• `{rec['punish_id']}` [{tag}·{summary}] {self._format_beijing_time(rec.get('created_at'))} "
                    f"(服 `{rec['guild_id']}`)\n  原因：{rec.get('reason') or '无'}"
                )
            embed.add_field(name="✅ 有效记录（新→旧）", value="\n".join(lines)[:1024], inline=False)
        else:
            embed.add_field(name="✅ 有效记录", value="暂无有效记录", inline=False)

        if revoked_records:
            rev_lines = []
            for rec in revoked_records[:5]:
                rev_lines.append(
                    f"• ↩️ `{rec['punish_id']}` 原原因：{rec.get('reason') or '无'}\n"
                    f"  撤销于 {self._format_beijing_time(rec.get('revoked_at'))} · "
                    f"撤销说明：{rec.get('revoke_reason') or '未填写'}"
                )
            embed.add_field(name="↩️ 已撤销记录（最近5条）", value="\n".join(rev_lines)[:1024], inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---- 3. /aff 补录 ----
    @anticommercial.command(name="补录", description="补录历史 aff 违规次数（仅记次，不执行禁言或挂警告组）")
    @app_commands.describe(
        member="要补录的成员（与用户ID二选一）",
        user_id="用户ID（成员不在本服时可直接填数字ID）",
        reason="补录原因或历史证据说明",
    )
    @app_commands.rename(member="成员", user_id="用户id", reason="原因")
    @guild_only()
    async def aff_backfill(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
        user_id: Optional[str] = None,
        reason: Optional[app_commands.Range[str, 1, 450]] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ 此命令只能在服务器中使用", ephemeral=True)
            return

        cfg = self.get_ac_config(guild.id)
        if not self._has_permission(interaction.user, cfg):
            await interaction.response.send_message(
                "❌ 您不是反商业化委员或管理组成员，无权使用此命令", ephemeral=True
            )
            return

        target_id, err = self._resolve_target_id(member, user_id)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        if target_id == interaction.user.id:
            await interaction.response.send_message("❌ 不能对自己执行此操作", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # 对本服及共享计次关联服做完整的成员缓存+API拉取保护校验，防通过 user_id 绕过防误伤
        check_guild_ids = resolve_scope_guild_ids(guild.id, cfg) or [guild.id]
        if guild.id not in check_guild_ids:
            check_guild_ids = [guild.id, *check_guild_ids]

        found_in_any_guild = False
        operator_is_admin = self._is_ac_admin(interaction.user, cfg)
        for gid in check_guild_ids:
            g_obj = guild if int(gid) == guild.id else self.bot.get_guild(int(gid))
            if g_obj is None:
                continue
            m_obj = (member if (member is not None and int(gid) == guild.id) else g_obj.get_member(target_id))
            if m_obj is None:
                try:
                    m_obj = await g_obj.fetch_member(target_id)
                except Exception:
                    m_obj = None
            if m_obj is not None:
                found_in_any_guild = True
                g_cfg = cfg if int(gid) == guild.id else self.get_ac_config(int(gid))
                if m_obj.bot:
                    await interaction.followup.send("❌ 不能对机器人执行此操作", ephemeral=True)
                    return
                if self._is_ac_admin(m_obj, g_cfg):
                    await interaction.followup.send("❌ 不能对管理组成员执行此操作", ephemeral=True)
                    return
                if not operator_is_admin and self._is_ac_committee(m_obj, g_cfg):
                    await interaction.followup.send("❌ 不能对反商业化委员执行此操作", ephemeral=True)
                    return

        if not found_in_any_guild:
            try:
                fetched_user = await self.bot.fetch_user(target_id)
            except discord.NotFound:
                await interaction.followup.send("❌ 未找到该 Discord 用户ID，请核对是否误填了消息ID或频道ID", ephemeral=True)
                return
            except Exception:
                fetched_user = None
            if fetched_user is not None and getattr(fetched_user, "bot", False):
                await interaction.followup.send("❌ 不能对机器人执行此操作", ephemeral=True)
                return

        db = self._get_db(cfg)
        scope_guild_ids = resolve_scope_guild_ids(guild.id, cfg)
        active_records = await db.get_active_records(target_id, scope_guild_ids)
        prior_count = len(active_records)
        offense_no = prior_count + 1
        final_reason = f"[历史补录] {((reason or '').strip() or '补录历史 aff 违规记录')[:450]}"[:500]

        confirm_embed = discord.Embed(
            title="📝 aff 历史次数补录确认",
            description=(
                "补录仅增加该用户的有效 aff 违规计数，**不会**触发实时禁言、警告身份组或公开处罚公示。"
            ),
            color=discord.Color.blue(),
        )
        confirm_embed.add_field(name="目标用户", value=f"<@{target_id}> (`{target_id}`)", inline=False)
        confirm_embed.add_field(name="补录前有效次数", value=f"{prior_count} 次", inline=True)
        confirm_embed.add_field(name="补录后有效次数", value=f"**{offense_no} 次**", inline=True)
        confirm_embed.add_field(name="补录说明", value=final_reason[:1024], inline=False)

        if not await confirm_view_embed(interaction, confirm_embed, timeout=120):
            return

        record_id = uuid.uuid4().hex[:8]
        async with self._get_user_lock(target_id):
            latest_active = await db.get_active_records(target_id, scope_guild_ids)
            if len(latest_active) != prior_count:
                await interaction.edit_original_response(
                    content=(
                        f"⚠️ 该用户的有效记录数刚刚发生变化（当前已为 {len(latest_active)} 次），"
                        "为避免重复补录，本次操作已中止，请重新核对后再试。"
                    ),
                    embed=None,
                    view=None,
                )
                return
            await db.add_record(
                punish_id=record_id,
                user_id=target_id,
                guild_id=guild.id,
                offense_no=offense_no,
                record_type="backfill",
                mute_seconds=0,
                warn_days=0,
                moderator_id=interaction.user.id,
                reason=final_reason,
            )

        moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", guild.id, 0)
        if moderation_log_channel_id:
            log_ch = guild.get_channel_or_thread(int(moderation_log_channel_id))
            if log_ch is not None:
                log_embed = discord.Embed(title="📝 aff 历史记录补录", color=discord.Color.blue())
                log_embed.add_field(name="目标用户", value=f"<@{target_id}> (`{target_id}`)")
                log_embed.add_field(name="操作人", value=interaction.user.mention)
                log_embed.add_field(name="当前有效次数", value=f"{offense_no} 次")
                log_embed.add_field(name="说明", value=final_reason[:1024], inline=False)
                log_embed.set_footer(text=f"记录ID: {record_id}")
                try:
                    await log_ch.send(embed=log_embed)
                except Exception:
                    pass

        await interaction.edit_original_response(
            content=(
                f"✅ 已为 <@{target_id}> 补录 1 次 aff 记录（当前有效累计：**{offense_no} 次**）。"
                f"记录ID: `{record_id}`"
            ),
            embed=None,
            view=None,
        )

    # ---- 4. /aff 撤销 ----
    @anticommercial.command(name="撤销", description="按处罚ID撤销 aff 处罚或补录记录")
    @app_commands.describe(
        punish_id="要撤销的 aff 处罚ID或补录记录ID",
        reason="撤销原因（可选）",
    )
    @app_commands.rename(punish_id="处罚id", reason="原因")
    @guild_only()
    async def aff_revoke(
        self,
        interaction: discord.Interaction,
        punish_id: str,
        reason: Optional[app_commands.Range[str, 1, 450]] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ 此命令只能在服务器中使用", ephemeral=True)
            return

        cfg = self.get_ac_config(guild.id)
        if not self._has_permission(interaction.user, cfg):
            await interaction.response.send_message(
                "❌ 您不是反商业化委员或管理组成员，无权使用此命令", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        pid = punish_id.strip()
        db = self._get_db(cfg)
        record = await db.get_record(pid)
        if record is None:
            await interaction.followup.send(
                "❌ 未找到对应的 aff 记录（此命令仅可撤销 `/aff` 产生的处罚或补录记录）",
                ephemeral=True,
            )
            return

        record_guild_id = int(record["guild_id"])
        if not can_cross_guild_operate(guild.id, record_guild_id, cfg):
            await interaction.followup.send("❌ 无权跨未关联的服务器撤销该记录", ephemeral=True)
            return

        if record.get("status") == "revoked":
            await interaction.followup.send(
                f"⚠️ 记录 `{pid}` 此前已被撤销，无需重复操作", ephemeral=True
            )
            return

        target_user_id = int(record["user_id"])
        is_backfill = record.get("record_type") == "backfill"
        revoke_reason = ((reason or "").strip() or "aff 记录撤销")[:450]

        origin_guild: Optional[discord.Guild] = (
            guild if record_guild_id == guild.id else self.bot.get_guild(record_guild_id)
        )
        if not is_backfill and origin_guild is None:
            await interaction.followup.send(
                f"❌ 无法访问原处罚所在服务器 (`{record_guild_id}`)，无法安全解除对应身份组与禁言",
                ephemeral=True,
            )
            return

        scope_guild_ids = resolve_scope_guild_ids(guild.id, cfg)

        confirm_embed = discord.Embed(
            title="🔓 撤销 aff 记录确认",
            description=(
                "撤销后该笔记录将不再计入有效次数。"
                + ("" if is_backfill else "同时将自动在主副服解除对应禁言与警告身份组，并发布撤销公示。")
            ),
            color=discord.Color.green(),
        )
        confirm_embed.add_field(name="记录ID", value=f"`{pid}`", inline=True)
        confirm_embed.add_field(name="类型", value="手动补录" if is_backfill else "正式处罚", inline=True)
        confirm_embed.add_field(name="目标用户", value=f"<@{target_user_id}> (`{target_user_id}`)", inline=False)
        confirm_embed.add_field(name="原记录原因", value=str(record.get("reason") or "无")[:1024], inline=False)
        confirm_embed.add_field(name="撤销原因", value=revoke_reason[:1024], inline=False)

        if not await confirm_view_embed(interaction, confirm_embed, timeout=120):
            return

        async with self._get_user_lock(target_user_id):
            latest = await db.get_record(pid)
            if latest is None or latest.get("status") == "revoked":
                await interaction.edit_original_response(
                    content=f"⚠️ 记录 `{pid}` 已被其他操作员撤销。",
                    embed=None,
                    view=None,
                )
                return

            if not is_backfill and origin_guild is not None:
                await self._undo_guild_punishment(
                    origin_guild=origin_guild,
                    invoking_guild=guild,
                    user_id=target_user_id,
                    punish_id=pid,
                    warn_days=int(record.get("warn_days", 0) or 0),
                    mute_seconds=int(record.get("mute_seconds", 0) or 0),
                    moderator=interaction.user,
                    revoke_reason=revoke_reason,
                )

            await db.revoke_record(pid, revoked_by=interaction.user.id, revoke_reason=revoke_reason)
            remaining = await db.get_active_records(target_user_id, scope_guild_ids)

        if is_backfill:
            moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", guild.id, 0)
            if moderation_log_channel_id:
                log_ch = guild.get_channel_or_thread(int(moderation_log_channel_id))
                if log_ch is not None:
                    emb = discord.Embed(title="↩️ 撤销 aff 补录记录", color=discord.Color.green())
                    emb.add_field(name="记录ID", value=f"`{pid}`")
                    emb.add_field(name="目标用户", value=f"<@{target_user_id}> (`{target_user_id}`)")
                    emb.add_field(name="操作人", value=interaction.user.mention)
                    emb.add_field(name="原因", value=revoke_reason[:1024], inline=False)
                    try:
                        await log_ch.send(embed=emb)
                    except Exception:
                        pass

        await interaction.edit_original_response(
            content=(
                f"✅ 已撤销 aff {'补录记录' if is_backfill else '处罚'} `{pid}`。"
                f"用户 <@{target_user_id}> 当前剩余有效记录：**{len(remaining)} 次**。"
            ),
            embed=None,
            view=None,
        )

    async def _undo_guild_punishment(
        self,
        *,
        origin_guild: discord.Guild,
        invoking_guild: discord.Guild,
        user_id: int,
        punish_id: str,
        warn_days: int,
        mute_seconds: int,
        moderator: discord.Member,
        revoke_reason: str,
    ) -> None:
        has_other_warn = cleanup_warn_files_for_punish(
            origin_guild.id, user_id, punish_id, base_data_dir=self._data_dir
        )

        member_obj: Optional[discord.Member] = origin_guild.get_member(user_id)
        if member_obj is None:
            try:
                member_obj = await origin_guild.fetch_member(user_id)
            except Exception:
                member_obj = None

        if member_obj is not None:
            if mute_seconds > 0:
                try:
                    await member_obj.timeout(None, reason=f"撤销aff处罚: {revoke_reason}"[:512])
                except Exception as exc:
                    if self.logger:
                        self.logger.warning(f"解除禁言失败 (uid={user_id}): {exc}")

            if warn_days > 0 and not has_other_warn:
                warned_role_id = self.get_guild_config("warned_role_id", origin_guild.id, 0)
                warned_role = origin_guild.get_role(int(warned_role_id)) if warned_role_id else None
                if warned_role is not None and warned_role in member_obj.roles:
                    try:
                        sync_cog_ref = self.bot.get_cog("ServerSyncCommands")
                        if sync_cog_ref:
                            sync_cog_ref._mark_guard(origin_guild.id, member_obj.id, warned_role.id, "remove")
                        await member_obj.remove_roles(
                            warned_role, reason=f"撤销aff处罚: {revoke_reason}"[:512]
                        )
                    except Exception as exc:
                        if self.logger:
                            self.logger.warning(f"移除警告身份组失败 (uid={user_id}): {exc}")

        punish_file = self._data_dir / "punish" / str(origin_guild.id) / f"{punish_id}.json"
        punish_file.unlink(missing_ok=True)

        sync_cog = self.bot.get_cog("ServerSyncCommands")
        if sync_cog:
            try:
                await sync_cog.sync_revoke_punishment(origin_guild, punish_id, moderator, revoke_reason)
            except Exception as exc:
                if self.logger:
                    self.logger.error(f"同步撤销反商处罚失败: {exc}")

        announce_channel_id = self.get_guild_config("punish_announce_channel_id", origin_guild.id, 0)
        announce_channel = origin_guild.get_channel(int(announce_channel_id)) if announce_channel_id else None
        moderation_log_channel_id = self.get_guild_config("moderation_log_channel_id", origin_guild.id, 0)
        moderation_log_channel = (
            origin_guild.get_channel_or_thread(int(moderation_log_channel_id))
            if moderation_log_channel_id
            else None
        )

        revoke_embed = discord.Embed(title="🔓 撤销处罚", color=discord.Color.green())
        revoke_embed.add_field(name="处罚ID", value=punish_id)
        revoke_embed.add_field(name="成员", value=f"<@{user_id}>")
        revoke_embed.add_field(name="操作人", value=moderator.mention)
        revoke_embed.add_field(name="原因", value=revoke_reason[:1024], inline=False)

        if announce_channel is not None:
            try:
                await send_punishment_announcement(
                    self.bot,
                    origin_guild.id,
                    announce_channel,
                    from_sync=False,
                    get_config=self.get_guild_config,
                    embed=revoke_embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception as exc:
                if self.logger:
                    self.logger.warning(f"发送反商撤销公示失败: {exc}")
        else:
            await archive_punishment(
                self.bot,
                origin_guild.id,
                None,
                embed=revoke_embed,
                get_config=self.get_guild_config,
            )

        if moderation_log_channel is not None:
            try:
                await moderation_log_channel.send(
                    embed=revoke_embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                pass

        # 若从关联服跨服发起撤销，同时在发起服的管理日志频道留存一份审计记录
        if invoking_guild.id != origin_guild.id:
            inv_log_id = self.get_guild_config("moderation_log_channel_id", invoking_guild.id, 0)
            if inv_log_id:
                inv_log_ch = invoking_guild.get_channel_or_thread(int(inv_log_id))
                if inv_log_ch is not None:
                    try:
                        await inv_log_ch.send(
                            embed=revoke_embed,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                    except Exception:
                        pass

    @staticmethod
    def _resolve_target_id(
        member: Optional[discord.Member],
        user_id: Optional[str],
    ) -> Tuple[int, Optional[str]]:
        if member is not None:
            return member.id, None
        if user_id is not None and user_id.strip():
            raw = user_id.strip()
            if raw.startswith("<@") and raw.endswith(">"):
                raw = raw[2:-1].lstrip("!")
            if raw.isdigit() and int(raw) > 0:
                return int(raw), None
            return 0, "❌ 无效的用户ID格式，请填写纯数字用户ID或直接选择成员"
        return 0, "❌ 请至少指定「成员」或「用户id」其中一项"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiCommercialCommands(bot))
