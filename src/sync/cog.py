import asyncio
import datetime
import json
import pathlib
import time
import uuid
from typing import Any, Dict, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from src.utils.auth import guild_only
from src.utils.confirm_view import confirm_view


def is_sync_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        cog = interaction.client.get_cog("ServerSyncCommands")
        if not cog:
            await interaction.response.send_message("❌ 同步模块未加载", ephemeral=True)
            return False
        allowed_ids = cog.config.get("sync_admins", [])
        if interaction.user.id in allowed_ids:
            return True
        await interaction.response.send_message("❌ 您没有同步管理权限", ephemeral=True)
        return False
    return app_commands.check(predicate)


class ServerSyncCommands(commands.Cog):
    sync = app_commands.Group(name="同步", description="服务器同步相关指令")
    sync_manage = app_commands.Group(name="同步管理", description="同步管理相关指令")

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger
        self.name = "服务器同步"
        self._config_cache: Dict = {}
        self._config_cache_mtime: Optional[float] = None
        self._persistent_views_registered = False
        self._role_sync_guard: Dict[str, float] = {}
        self._role_event_guard: Dict[str, float] = {}

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._persistent_views_registered:
            self.bot.add_view(ManualRoleSyncView())
            self._persistent_views_registered = True
        if self.logger:
            self.logger.info("服务器同步模块已加载")

    @property
    def config(self) -> Dict:
        try:
            path = pathlib.Path("config/server_sync/config.json")
            if not path.exists():
                self._config_cache = self._default_config()
                self._save_config()
                return self._config_cache
            mtime = path.stat().st_mtime
            if self._config_cache_mtime != mtime:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self._config_cache, changed = self._normalize_config(loaded)
                self._config_cache_mtime = mtime
                if changed:
                    self._save_config()
            return self._config_cache
        except Exception as e:
            if self.logger:
                self.logger.error(f"加载同步配置文件失败: {e}")
            return self._default_config()

    def _default_config(self) -> Dict:
        return {
            "enabled": True,
            "sync_admins": [],
            "server_groups": {},
            "servers": {},
            "role_mapping": {},
        }

    def _normalize_server_cfg(self, data: Dict) -> Tuple[Dict, bool]:
        changed = False
        cfg = dict(data or {})
        defaults = {
            "name": "",
            "roles": {},
            "punishment_sync": False,
            "punishment_announce_channel": None,
        }
        for key, value in defaults.items():
            if key not in cfg:
                cfg[key] = value
                changed = True
        if "punishment_confirm_channel" in cfg:
            cfg.pop("punishment_confirm_channel", None)
            changed = True
        return cfg, changed

    def _normalize_config(self, data: Dict) -> Tuple[Dict, bool]:
        changed = False
        cfg = dict(data or {})
        if "enabled" not in cfg:
            cfg["enabled"] = True
            changed = True
        if "sync_admins" not in cfg:
            cfg["sync_admins"] = []
            changed = True
        if "server_groups" not in cfg:
            cfg["server_groups"] = {}
            changed = True

        # 兼容旧结构：servers 直接扁平化存储
        if cfg.get("servers") and not cfg["server_groups"]:
            group_name = "default"
            cfg["server_groups"][group_name] = {"main_server_id": None, "servers": {}}
            for gid, server_cfg in cfg.get("servers", {}).items():
                normalized_server_cfg, _ = self._normalize_server_cfg(server_cfg)
                cfg["server_groups"][group_name]["servers"][str(gid)] = normalized_server_cfg
            if cfg["server_groups"][group_name]["servers"]:
                first_gid = next(iter(cfg["server_groups"][group_name]["servers"]))
                cfg["server_groups"][group_name]["main_server_id"] = first_gid
            changed = True

        for group_name, group_cfg in list(cfg["server_groups"].items()):
            if not isinstance(group_cfg, dict):
                cfg["server_groups"][group_name] = {"main_server_id": None, "servers": {}}
                changed = True
                continue
            if "main_server_id" not in group_cfg:
                group_cfg["main_server_id"] = None
                changed = True
            if "servers" not in group_cfg:
                group_cfg["servers"] = {}
                changed = True
            for gid, server_cfg in list(group_cfg["servers"].items()):
                normalized_server_cfg, server_changed = self._normalize_server_cfg(server_cfg)
                group_cfg["servers"][str(gid)] = normalized_server_cfg
                changed = changed or server_changed

            main_id = group_cfg.get("main_server_id")
            if main_id is not None:
                group_cfg["main_server_id"] = str(main_id)
            if not group_cfg.get("main_server_id") and group_cfg["servers"]:
                group_cfg["main_server_id"] = next(iter(group_cfg["servers"]))
                changed = True
            if group_cfg.get("main_server_id") not in group_cfg["servers"] and group_cfg["servers"]:
                group_cfg["main_server_id"] = next(iter(group_cfg["servers"]))
                changed = True

        self._refresh_legacy_views(cfg)
        return cfg, changed

    def _refresh_legacy_views(self, cfg: Dict) -> None:
        legacy_servers = {}
        role_mapping: Dict[str, Dict[str, int]] = {}
        for group_cfg in cfg.get("server_groups", {}).values():
            for gid, server_cfg in group_cfg.get("servers", {}).items():
                legacy_servers[gid] = server_cfg
                for role_alias, role_id in server_cfg.get("roles", {}).items():
                    role_mapping.setdefault(role_alias, {})[gid] = role_id
        cfg["servers"] = legacy_servers
        cfg["role_mapping"] = role_mapping

    def _save_config(self) -> None:
        try:
            path = pathlib.Path("config/server_sync/config.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            self._refresh_legacy_views(self._config_cache)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._config_cache, f, ensure_ascii=False, indent=2)
            self._config_cache_mtime = path.stat().st_mtime
        except Exception as e:
            if self.logger:
                self.logger.error(f"保存同步配置文件失败: {e}")

    def _get_group_and_server_cfg(self, guild_id: str) -> Tuple[Optional[str], Optional[Dict]]:
        for group_name, group_cfg in self.config.get("server_groups", {}).items():
            server_cfg = group_cfg.get("servers", {}).get(guild_id)
            if server_cfg:
                return group_name, server_cfg
        return None, None

    def _get_group_as_main(self, guild_id: str) -> Tuple[Optional[str], Optional[Dict]]:
        """返回 guild 作为主服务器所在的 (组名, 组配置)，否则 (None, None)"""
        for group_name, group_cfg in self.config.get("server_groups", {}).items():
            if str(group_cfg.get("main_server_id")) == guild_id:
                return group_name, group_cfg
        return None, None

    def _ensure_group(self, config: Dict, group_name: str) -> Dict:
        groups = config.setdefault("server_groups", {})
        if group_name not in groups:
            groups[group_name] = {"main_server_id": None, "servers": {}}
        return groups[group_name]

    def _ensure_server_in_group(self, config: Dict, group_name: str, guild: discord.Guild) -> Dict:
        group_cfg = self._ensure_group(config, group_name)
        servers = group_cfg.setdefault("servers", {})
        server_cfg = servers.get(str(guild.id))
        if not server_cfg:
            server_cfg = {
                "name": guild.name,
                "roles": {},
                "punishment_sync": False,
                "punishment_announce_channel": None,
            }
            servers[str(guild.id)] = server_cfg
        else:
            server_cfg["name"] = guild.name
        return server_cfg

    def _get_role_alias_for_source(self, source_server_cfg: Dict, role: discord.Role) -> Optional[str]:
        for alias, role_id in source_server_cfg.get("roles", {}).items():
            if int(role_id) == role.id:
                return alias
        if role.name in source_server_cfg.get("roles", {}):
            return role.name
        return None

    async def _safe_fetch_member(self, guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
        member = guild.get_member(user_id)
        if member:
            return member
        try:
            return await guild.fetch_member(user_id)
        except Exception:
            return None

    def _bot_can_manage_roles(self, guild: discord.Guild) -> bool:
        me = guild.me
        return bool(me and me.guild_permissions.manage_roles)

    def _is_manageable_role(self, guild: discord.Guild, role: discord.Role) -> bool:
        me = guild.me
        if not me:
            return False
        if role.is_default() or role.managed:
            return False
        return role.position < me.top_role.position

    async def _read_role_icon(self, role: discord.Role) -> Optional[bytes]:
        if not getattr(role, "icon", None):
            return None
        try:
            return await role.icon.read()
        except Exception:
            return None

    async def _create_role_with_optional_icon(self, guild: discord.Guild, kwargs: Dict):
        try:
            return await guild.create_role(**kwargs)
        except TypeError:
            fallback_kwargs = dict(kwargs)
            if "display_icon" in kwargs:
                fallback_kwargs["icon"] = fallback_kwargs.pop("display_icon")
            # 兼容旧版 discord.py：不支持 secondary/tertiary 参数时，先创建再走 API 兜底
            fallback_kwargs.pop("secondary_color", None)
            fallback_kwargs.pop("tertiary_color", None)
            return await guild.create_role(**fallback_kwargs)

    async def _edit_role_with_optional_icon(self, role: discord.Role, kwargs: Dict):
        try:
            return await role.edit(**kwargs)
        except TypeError:
            fallback_kwargs = dict(kwargs)
            if "display_icon" in kwargs:
                fallback_kwargs["icon"] = fallback_kwargs.pop("display_icon")
            fallback_kwargs.pop("secondary_color", None)
            fallback_kwargs.pop("tertiary_color", None)
            return await role.edit(**fallback_kwargs)

    def _role_colors_payload(self, role: discord.Role) -> Dict[str, Optional[int]]:
        secondary_obj = getattr(role, "secondary_color", None)
        tertiary_obj = getattr(role, "tertiary_color", None)
        secondary_value = secondary_obj.value if secondary_obj is not None else None
        tertiary_value = tertiary_obj.value if tertiary_obj is not None else None
        return {
            "primary_color": role.colour.value,
            "secondary_color": secondary_value,
            "tertiary_color": tertiary_value,
        }

    async def _patch_role_colors_via_api(
        self,
        guild: discord.Guild,
        role: discord.Role,
        colors_payload: Dict[str, Optional[int]],
        reason: str,
    ) -> None:
        try:
            # 直接调用 Discord API，确保 colors 对象（primary/secondary/tertiary）按文档完整写入
            await self.bot.http.edit_role(
                guild.id,
                role.id,
                reason=reason,
                colors=colors_payload,
            )
        except Exception as e:
            if self.logger:
                self.logger.warning(f"补丁同步角色扩展颜色失败 {guild.name}/{role.name}: {e}")

    async def _upsert_role_from_main(
        self,
        source_role: discord.Role,
        target_guild: discord.Guild,
        existing_role_id: Optional[int] = None,
    ) -> Optional[discord.Role]:
        existing: Optional[discord.Role] = None
        if existing_role_id:
            existing = target_guild.get_role(existing_role_id)
        if not existing:
            existing = discord.utils.get(target_guild.roles, name=source_role.name)

        icon_bytes = await self._read_role_icon(source_role)
        colors_payload = self._role_colors_payload(source_role)
        reason = f"主服务器身份组同步: {source_role.name}"
        base_kwargs = {
            "name": source_role.name,
            "permissions": source_role.permissions,
            "colour": source_role.colour,
            "secondary_color": colors_payload["secondary_color"],
            "tertiary_color": colors_payload["tertiary_color"],
            "hoist": source_role.hoist,
            "mentionable": source_role.mentionable,
            "reason": reason,
        }
        unicode_emoji = getattr(source_role, "unicode_emoji", None)
        if unicode_emoji:
            base_kwargs["display_icon"] = unicode_emoji
        elif icon_bytes:
            base_kwargs["display_icon"] = icon_bytes

        if existing:
            if not self._is_manageable_role(target_guild, existing):
                return existing
            result_role = await self._edit_role_with_optional_icon(existing, base_kwargs)
        else:
            result_role = await self._create_role_with_optional_icon(target_guild, base_kwargs)

        await self._patch_role_colors_via_api(target_guild, result_role, colors_payload, reason)
        return result_role

    async def _apply_punishment_in_guild(self, target_guild: discord.Guild, record: Dict, server_cfg: Dict) -> None:
        punishment_type = record.get("type")
        user_id = int(record["user_id"])
        reason = record.get("reason") or "同步处罚"
        duration = record.get("duration")
        warn_days = int(record.get("warn_days") or 0)

        if punishment_type == "mute":
            user_obj = await self._safe_fetch_member(target_guild, user_id)
            if not user_obj:
                return
            if duration and duration > 0:
                await user_obj.timeout(datetime.timedelta(seconds=int(duration)), reason=f"同步处罚: {reason}")
            if warn_days > 0:
                warned_role_id = self._get_warned_role_id(target_guild.id)
                if warned_role_id:
                    warned_role = target_guild.get_role(int(warned_role_id))
                    if warned_role:
                        await user_obj.add_roles(warned_role, reason=f"同步处罚警告 {warn_days} 天")
        elif punishment_type == "ban":
            await target_guild.ban(discord.Object(id=user_id), reason=f"同步处罚: {reason}", delete_message_days=0)
        else:
            return

        punish_dir = pathlib.Path("data") / "punish" / str(target_guild.id)
        punish_dir.mkdir(parents=True, exist_ok=True)
        record_file = punish_dir / f"{record['id']}.json"
        with open(record_file, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        announce_channel_id = server_cfg.get("punishment_announce_channel")
        if announce_channel_id:
            announce_channel = target_guild.get_channel(int(announce_channel_id))
            if announce_channel:
                embed = discord.Embed(
                    title="🚨 同步处罚执行",
                    color=discord.Color.red(),
                    timestamp=datetime.datetime.now(datetime.timezone.utc),
                )
                embed.add_field(name="来源服务器", value=record["source_guild_name"], inline=True)
                embed.add_field(name="处罚类型", value=punishment_type, inline=True)
                embed.add_field(name="用户", value=f"<@{user_id}> ({record.get('user_name', user_id)})", inline=True)
                embed.add_field(name="原管理员", value=record.get("moderator_name", "系统"), inline=True)
                embed.add_field(name="原因", value=reason, inline=False)
                if record.get("img_url"):
                    embed.set_image(url=record["img_url"])
                embed.set_footer(text=f"处罚ID: {record['id']}")
                await announce_channel.send(embed=embed)

    def _get_warned_role_id(self, guild_id: int) -> Optional[int]:
        guild_configs = getattr(self.bot, "config", {}).get("guild_configs", {})
        guild_config = guild_configs.get(str(guild_id), {})
        warned_role_id = guild_config.get("warned_role_id")
        return int(warned_role_id) if warned_role_id else None

    def _format_progress_bar(self, current: int, total: int, width: int = 20) -> str:
        if total <= 0:
            return "░" * width
        ratio = min(max(current / total, 0), 1)
        filled = int(round(ratio * width))
        return ("█" * filled) + ("░" * (width - filled))

    async def _sync_member_roles_from_main_to_sub(
        self,
        member_in_sub: discord.Member,
        group_name: str,
    ) -> int:
        guild_id = str(member_in_sub.guild.id)
        group_cfg = self.config.get("server_groups", {}).get(group_name, {})
        main_server_id = str(group_cfg.get("main_server_id") or "")
        if not main_server_id or main_server_id == guild_id:
            return 0

        source_guild = self.bot.get_guild(int(main_server_id))
        if not source_guild:
            return 0

        source_member = await self._safe_fetch_member(source_guild, member_in_sub.id)
        if not source_member:
            return 0

        source_server_cfg = group_cfg.get("servers", {}).get(main_server_id, {})
        sub_server_cfg = group_cfg.get("servers", {}).get(guild_id, {})
        if not source_server_cfg or not sub_server_cfg:
            return 0

        source_role_ids = {role.id for role in source_member.roles if not role.is_default() and not role.managed}
        added_count = 0
        for alias, source_role_id in source_server_cfg.get("roles", {}).items():
            if int(source_role_id) not in source_role_ids:
                continue

            target_role_id = sub_server_cfg.get("roles", {}).get(alias)
            if not target_role_id:
                continue

            target_role = member_in_sub.guild.get_role(int(target_role_id))
            if not target_role or target_role.managed or target_role in member_in_sub.roles:
                continue

            try:
                await member_in_sub.add_roles(
                    target_role,
                    reason=f"入服自动同步主服身份组（组: {group_name}）",
                )
                added_count += 1
            except discord.Forbidden:
                if self.logger:
                    self.logger.warning(
                        f"自动同步身份组权限不足: {member_in_sub.guild.name}/{member_in_sub.id}/{alias}"
                    )
            except Exception as e:
                if self.logger:
                    self.logger.error(
                        f"自动同步身份组失败 {member_in_sub.guild.name}/{member_in_sub.id}/{alias}: {e}"
                    )
        return added_count

    def _build_guard_key(self, guild_id: int, user_id: int, role_id: int, action: str) -> str:
        return f"{guild_id}:{user_id}:{role_id}:{action}"

    def _mark_guard(self, guild_id: int, user_id: int, role_id: int, action: str, ttl: float = 20.0) -> None:
        self._role_sync_guard[self._build_guard_key(guild_id, user_id, role_id, action)] = time.monotonic() + ttl

    def _consume_guard(self, guild_id: int, user_id: int, role_id: int, action: str) -> bool:
        key = self._build_guard_key(guild_id, user_id, role_id, action)
        expires_at = self._role_sync_guard.get(key)
        now = time.monotonic()
        if expires_at is None:
            return False
        if expires_at < now:
            self._role_sync_guard.pop(key, None)
            return False
        self._role_sync_guard.pop(key, None)
        return True

    def _mark_role_event_guard(self, guild_id: int, role_id: int, action: str, ttl: float = 30.0) -> None:
        key = f"re:{guild_id}:{role_id}:{action}"
        self._role_event_guard[key] = time.monotonic() + ttl

    def _consume_role_event_guard(self, guild_id: int, role_id: int, action: str) -> bool:
        key = f"re:{guild_id}:{role_id}:{action}"
        expires_at = self._role_event_guard.get(key)
        now = time.monotonic()
        if expires_at is None:
            return False
        if expires_at < now:
            self._role_event_guard.pop(key, None)
            return False
        self._role_event_guard.pop(key, None)
        return True

    async def _propagate_role_add(self, guild: discord.Guild, member: discord.Member, role: discord.Role, reason: str = None):
        if role.managed:
            return
        guild_id = str(guild.id)
        group_name, source_server_cfg = self._get_group_and_server_cfg(guild_id)
        if not group_name or not source_server_cfg:
            return
        role_alias = self._get_role_alias_for_source(source_server_cfg, role)
        if not role_alias:
            return

        group_servers = self.config["server_groups"][group_name]["servers"]
        for target_guild_id, target_server_cfg in group_servers.items():
            if target_guild_id == guild_id:
                continue
            target_guild = self.bot.get_guild(int(target_guild_id))
            if not target_guild:
                continue
            target_member = target_guild.get_member(member.id)
            if not target_member:
                continue
            target_role_id = target_server_cfg.get("roles", {}).get(role_alias)
            if not target_role_id:
                continue
            target_role = target_guild.get_role(int(target_role_id))
            if not target_role or target_role.managed or target_role in target_member.roles:
                continue
            try:
                self._mark_guard(target_guild.id, target_member.id, target_role.id, "add")
                await target_member.add_roles(target_role, reason=f"身份组同步: {reason or '监听到身份组变更'}")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"同步添加身份组失败 {target_guild.name}/{role_alias}: {e}")

    async def _propagate_role_remove(self, guild: discord.Guild, member: discord.Member, role: discord.Role, reason: str = None):
        if role.managed:
            return
        guild_id = str(guild.id)
        group_name, source_server_cfg = self._get_group_and_server_cfg(guild_id)
        if not group_name or not source_server_cfg:
            return
        role_alias = self._get_role_alias_for_source(source_server_cfg, role)
        if not role_alias:
            return

        group_servers = self.config["server_groups"][group_name]["servers"]
        for target_guild_id, target_server_cfg in group_servers.items():
            if target_guild_id == guild_id:
                continue
            target_guild = self.bot.get_guild(int(target_guild_id))
            if not target_guild:
                continue
            target_member = target_guild.get_member(member.id)
            if not target_member:
                continue
            target_role_id = target_server_cfg.get("roles", {}).get(role_alias)
            if not target_role_id:
                continue
            target_role = target_guild.get_role(int(target_role_id))
            if not target_role or target_role.managed or target_role not in target_member.roles:
                continue
            try:
                self._mark_guard(target_guild.id, target_member.id, target_role.id, "remove")
                await target_member.remove_roles(target_role, reason=f"身份组同步: {reason or '监听到身份组变更'}")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"同步移除身份组失败 {target_guild.name}/{role_alias}: {e}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot or not self.config.get("enabled", False):
            return

        guild_id = str(member.guild.id)
        group_name, _ = self._get_group_and_server_cfg(guild_id)
        if not group_name:
            return

        group_cfg = self.config.get("server_groups", {}).get(group_name, {})
        main_server_id = str(group_cfg.get("main_server_id") or "")
        if not main_server_id or main_server_id == guild_id:
            return

        # 等待 Discord 侧 member 状态稳定，降低刚入服立即改身份组失败概率
        await asyncio.sleep(1.0)
        added_count = await self._sync_member_roles_from_main_to_sub(member, group_name)
        if added_count > 0 and self.logger:
            self.logger.info(
                f"已自动同步入服用户身份组: group={group_name}, guild={member.guild.id}, user={member.id}, added={added_count}"
            )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not self.config.get("enabled", False):
            return
        if before.guild.id != after.guild.id:
            return

        guild_id = str(after.guild.id)
        group_name, _ = self._get_group_and_server_cfg(guild_id)
        if not group_name:
            return

        before_role_ids = {role.id for role in before.roles if not role.is_default()}
        after_role_ids = {role.id for role in after.roles if not role.is_default()}
        if before_role_ids == after_role_ids:
            return

        added_role_ids = after_role_ids - before_role_ids
        removed_role_ids = before_role_ids - after_role_ids

        for role_id in added_role_ids:
            if self._consume_guard(after.guild.id, after.id, role_id, "add"):
                continue
            role_obj = after.guild.get_role(role_id)
            if not role_obj:
                continue
            await self._propagate_role_add(after.guild, after, role_obj, "监听同步（身份组新增）")

        for role_id in removed_role_ids:
            if self._consume_guard(after.guild.id, after.id, role_id, "remove"):
                continue
            role_obj = after.guild.get_role(role_id)
            if not role_obj:
                continue
            await self._propagate_role_remove(after.guild, after, role_obj, "监听同步（身份组移除）")

    # ====== 身份组结构变更监听（主服 → 子服） ======
    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        if not self.config.get("enabled", False):
            return
        if role.managed or role.is_default():
            return
        if self._consume_role_event_guard(role.guild.id, role.id, "create"):
            return

        guild_id = str(role.guild.id)
        group_name, group_cfg = self._get_group_as_main(guild_id)
        if not group_name or not group_cfg:
            return
        if not self._is_manageable_role(role.guild, role):
            return

        main_server_cfg = group_cfg["servers"].get(guild_id, {})
        main_server_cfg.setdefault("roles", {})[role.name] = role.id

        for sub_guild_id, sub_server_cfg in group_cfg["servers"].items():
            if sub_guild_id == guild_id:
                continue
            sub_guild = self.bot.get_guild(int(sub_guild_id))
            if not sub_guild or not self._bot_can_manage_roles(sub_guild):
                continue
            try:
                new_role = await self._upsert_role_from_main(role, sub_guild)
                if new_role:
                    self._mark_role_event_guard(sub_guild.id, new_role.id, "create")
                    sub_server_cfg.setdefault("roles", {})[role.name] = new_role.id
            except Exception as e:
                if self.logger:
                    self.logger.error(f"监听同步创建身份组失败 {sub_guild.name}/{role.name}: {e}")

        self._config_cache = self.config
        self._save_config()
        if self.logger:
            self.logger.info(f"监听主服身份组创建并同步: group={group_name}, role={role.name}")

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        if not self.config.get("enabled", False):
            return
        if after.managed or after.is_default():
            return

        guild_id = str(after.guild.id)

        if self._consume_role_event_guard(after.guild.id, after.id, "update"):
            return

        group_name, group_cfg = self._get_group_as_main(guild_id)
        if not group_name or not group_cfg:
            return
        if not self._is_manageable_role(after.guild, after):
            return

        main_server_cfg = group_cfg["servers"].get(guild_id, {})
        old_alias = None
        for alias, rid in list(main_server_cfg.get("roles", {}).items()):
            if int(rid) == before.id:
                old_alias = alias
                break

        if not old_alias:
            return

        if before.name != after.name:
            main_server_cfg["roles"].pop(old_alias, None)
            main_server_cfg["roles"][after.name] = after.id

        for sub_guild_id, sub_server_cfg in group_cfg["servers"].items():
            if sub_guild_id == guild_id:
                continue
            sub_guild = self.bot.get_guild(int(sub_guild_id))
            if not sub_guild or not self._bot_can_manage_roles(sub_guild):
                continue
            target_role_id = sub_server_cfg.get("roles", {}).get(old_alias)
            if not target_role_id:
                continue
            try:
                updated_role = await self._upsert_role_from_main(
                    after, sub_guild, existing_role_id=int(target_role_id)
                )
                if updated_role:
                    self._mark_role_event_guard(sub_guild.id, updated_role.id, "update")
                    if before.name != after.name:
                        sub_server_cfg["roles"].pop(old_alias, None)
                    sub_server_cfg["roles"][after.name] = updated_role.id
            except Exception as e:
                if self.logger:
                    self.logger.error(f"监听同步更新身份组失败 {sub_guild.name}/{after.name}: {e}")

        self._config_cache = self.config
        self._save_config()
        if self.logger:
            self.logger.info(f"监听主服身份组更新并同步: group={group_name}, role={after.name}")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        if not self.config.get("enabled", False):
            return
        if role.managed or role.is_default():
            return
        if self._consume_role_event_guard(role.guild.id, role.id, "delete"):
            return

        guild_id = str(role.guild.id)
        group_name, group_cfg = self._get_group_as_main(guild_id)
        if not group_name or not group_cfg:
            return

        main_server_cfg = group_cfg["servers"].get(guild_id, {})
        alias = None
        for a, rid in list(main_server_cfg.get("roles", {}).items()):
            if int(rid) == role.id:
                alias = a
                break

        if not alias:
            return

        main_server_cfg["roles"].pop(alias, None)

        for sub_guild_id, sub_server_cfg in group_cfg["servers"].items():
            if sub_guild_id == guild_id:
                continue
            sub_guild = self.bot.get_guild(int(sub_guild_id))
            if not sub_guild:
                continue
            target_role_id = sub_server_cfg.get("roles", {}).get(alias)
            if not target_role_id:
                continue
            target_role = sub_guild.get_role(int(target_role_id))
            if target_role:
                try:
                    self._mark_role_event_guard(sub_guild.id, target_role.id, "delete")
                    await target_role.delete(reason=f"主服身份组同步删除: {alias}")
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"监听同步删除身份组失败 {sub_guild.name}/{alias}: {e}")
            sub_server_cfg["roles"].pop(alias, None)

        self._config_cache = self.config
        self._save_config()
        if self.logger:
            self.logger.info(f"监听主服身份组删除并同步: group={group_name}, role={role.name}")

    # ====== 用户同步指令 ======
    @sync.command(name="身份组同步", description="将当前账号在本组中的身份组映射同步到同组其它服务器")
    @guild_only()
    async def sync_roles(self, interaction: discord.Interaction):
        if not self.config.get("enabled", False):
            await interaction.response.send_message("❌ 同步功能未启用", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild.id)
        group_name, source_server_cfg = self._get_group_and_server_cfg(guild_id)
        if not group_name or not source_server_cfg:
            await interaction.followup.send("❌ 当前服务器未加入任何同步服务器组", ephemeral=True)
            return

        syncable = []
        for role in interaction.user.roles:
            if role.is_default() or role.managed:
                continue
            alias = self._get_role_alias_for_source(source_server_cfg, role)
            if alias:
                syncable.append((alias, role))

        if not syncable:
            await interaction.followup.send("❌ 您没有可同步的身份组映射", ephemeral=True)
            return

        results = []
        group_servers = self.config["server_groups"][group_name]["servers"]
        for target_guild_id, target_server_cfg in group_servers.items():
            if target_guild_id == guild_id:
                continue
            target_guild = self.bot.get_guild(int(target_guild_id))
            if not target_guild:
                results.append(f"❌ 无法访问服务器 {target_guild_id}")
                continue
            target_member = target_guild.get_member(interaction.user.id)
            if not target_member:
                results.append(f"❌ 您不在服务器 {target_guild.name} 中")
                continue

            success = 0
            for alias, _ in syncable:
                target_role_id = target_server_cfg.get("roles", {}).get(alias)
                if not target_role_id:
                    continue
                target_role = target_guild.get_role(int(target_role_id))
                if not target_role or target_role.managed:
                    continue
                try:
                    if target_role not in target_member.roles:
                        await target_member.add_roles(target_role, reason=f"身份组同步 from {interaction.guild.name}")
                        success += 1
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"身份组同步失败 {target_guild.name}/{alias}: {e}")
            if success > 0:
                results.append(f"✅ {target_guild.name} 同步 {success} 个身份组")

        await interaction.followup.send(
            "身份组同步结果：\n" + ("\n".join(results) if results else "✅ 无需变更"),
            ephemeral=True,
        )

    @sync.command(name="发布手动同步按钮", description="在当前频道发送手动同步面板（Embed + 持久化按钮）")
    @guild_only()
    @is_sync_admin()
    async def post_manual_sync_panel(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🔄 主服身份组手动同步",
            description=(
                "点击下方按钮，可将你在主服务器已有的映射身份组同步到当前服务器。\n"
                "仅在当前服务器为子服务器时生效。"
            ),
            color=discord.Color.blurple(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_footer(text="按钮为持久化组件，机器人重启后仍可使用")
        await interaction.channel.send(embed=embed, view=ManualRoleSyncView())
        await interaction.response.send_message("✅ 已在当前频道发送手动同步面板", ephemeral=True)

    @sync.command(name="主服全量身份组同步", description="从指定组主服务器全量同步可管理身份组到当前子服务器")
    @guild_only()
    @is_sync_admin()
    @app_commands.describe(组名="服务器组名称")
    async def sync_all_roles_from_main(self, interaction: discord.Interaction, 组名: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild.id)
        group_cfg = self.config.get("server_groups", {}).get(组名)
        if not group_cfg:
            await interaction.followup.send(f"❌ 服务器组 `{组名}` 不存在", ephemeral=True)
            return
        if guild_id not in group_cfg.get("servers", {}):
            await interaction.followup.send("❌ 当前服务器不在该组中", ephemeral=True)
            return

        main_server_id = str(group_cfg.get("main_server_id") or "")
        if not main_server_id:
            await interaction.followup.send("❌ 该组未设置主服务器", ephemeral=True)
            return
        if guild_id == main_server_id:
            await interaction.followup.send("❌ 当前服务器是主服务器，无法执行“从主服同步到当前子服”", ephemeral=True)
            return

        source_guild = self.bot.get_guild(int(main_server_id))
        target_guild = interaction.guild
        if not source_guild:
            await interaction.followup.send("❌ 无法访问主服务器", ephemeral=True)
            return
        if not self._bot_can_manage_roles(source_guild) or not self._bot_can_manage_roles(target_guild):
            await interaction.followup.send("❌ Bot 在主服或当前服务器缺少“管理身份组”权限", ephemeral=True)
            return

        source_roles = [r for r in source_guild.roles if self._is_manageable_role(source_guild, r)]
        source_roles.sort(key=lambda r: r.position)
        if not source_roles:
            await interaction.followup.send("❌ 主服务器没有可由 Bot 操作的身份组", ephemeral=True)
            return

        server_cfg = group_cfg["servers"][guild_id]
        main_server_cfg = group_cfg["servers"].get(main_server_id, {})
        updated = 0
        skipped = 0
        role_map: Dict[discord.Role, int] = {}
        total = len(source_roles)
        step = max(1, total // 20)
        progress_msg = await interaction.followup.send(
            (
                f"🔄 正在从主服同步身份组（组: `{组名}`）\n"
                f"`{self._format_progress_bar(0, total)}` 0/{total} (0%)\n"
                "成功 0 | 跳过/失败 0"
            ),
            ephemeral=True,
            wait=True,
        )

        existing_mappings = server_cfg.get("roles", {})
        for idx, source_role in enumerate(source_roles, start=1):
            try:
                mapped_role_id = existing_mappings.get(source_role.name)
                target_role = await self._upsert_role_from_main(
                    source_role, target_guild,
                    existing_role_id=int(mapped_role_id) if mapped_role_id else None,
                )
                if not target_role:
                    skipped += 1
                else:
                    self._mark_role_event_guard(target_guild.id, target_role.id, "create")
                    self._mark_role_event_guard(target_guild.id, target_role.id, "update")
                    main_server_cfg.setdefault("roles", {})[source_role.name] = source_role.id
                    server_cfg["roles"][source_role.name] = target_role.id
                    role_map[target_role] = source_role.position
                    updated += 1
            except discord.Forbidden:
                skipped += 1
            except Exception as e:
                skipped += 1
                if self.logger:
                    self.logger.error(f"全量同步身份组失败 {source_role.name}: {e}")

            if idx == 1 or idx % step == 0 or idx == total:
                percent = int(idx * 100 / total)
                await progress_msg.edit(
                    content=(
                        f"🔄 正在从主服同步身份组（组: `{组名}`）\n"
                        f"`{self._format_progress_bar(idx, total)}` {idx}/{total} ({percent}%)\n"
                        f"成功 {updated} | 跳过/失败 {skipped}"
                    )
                )
            await asyncio.sleep(0.1)

        if role_map:
            try:
                sorted_pairs = sorted(role_map.items(), key=lambda pair: pair[1])
                bot_top = target_guild.me.top_role.position if target_guild.me else 1
                positions = {}
                for i, (role_obj, _) in enumerate(sorted_pairs, start=1):
                    pos = min(i, bot_top - 1)
                    if pos < 1:
                        pos = 1
                    positions[role_obj] = pos
                await target_guild.edit_role_positions(positions=positions, reason=f"主服务器身份组全量同步: {组名}")
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"更新身份组排序失败: {e}")

        self._config_cache = self.config
        self._save_config()
        await progress_msg.edit(
            content=(
                f"✅ 主服身份组同步完成（组: `{组名}`）\n"
                f"`{self._format_progress_bar(total, total)}` {total}/{total} (100%)\n"
                f"成功 {updated} | 跳过/失败 {skipped}"
            )
        )

    @sync.command(name="删除同步身份组", description="一键删除当前子服务器中所有从主服同步过来的身份组（不可在主服使用）")
    @guild_only()
    @is_sync_admin()
    @app_commands.describe(组名="服务器组名称")
    async def delete_synced_roles(self, interaction: discord.Interaction, 组名: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild.id)
        group_cfg = self.config.get("server_groups", {}).get(组名)
        if not group_cfg:
            await interaction.response.send_message(f"❌ 服务器组 `{组名}` 不存在", ephemeral=True)
            return
        if guild_id not in group_cfg.get("servers", {}):
            await interaction.response.send_message("❌ 当前服务器不在该组中", ephemeral=True)
            return

        main_server_id = str(group_cfg.get("main_server_id") or "")
        if guild_id == main_server_id:
            await interaction.response.send_message("❌ 绝对禁止在主服务器执行此操作", ephemeral=True)
            return

        server_cfg = group_cfg["servers"][guild_id]
        role_entries = server_cfg.get("roles", {})
        if not role_entries:
            await interaction.response.send_message("❌ 当前服务器在该组中没有已映射的同步身份组", ephemeral=True)
            return

        target_roles = []
        for alias, role_id in role_entries.items():
            role_obj = interaction.guild.get_role(int(role_id))
            if role_obj and self._is_manageable_role(interaction.guild, role_obj):
                target_roles.append((alias, role_obj))

        if not target_roles:
            await interaction.response.send_message("❌ 没有可删除的同步身份组（可能已被删除或 Bot 权限不足）", ephemeral=True)
            return

        role_list_text = "\n".join(f"- {alias} ({role_obj.name}, {len(role_obj.members)} 人持有)" for alias, role_obj in target_roles)
        confirmed = await confirm_view(
            interaction,
            title="⚠️ 危险操作：删除全部同步身份组",
            description=(
                f"即将在当前服务器删除 **{len(target_roles)}** 个从主服同步的身份组：\n\n"
                f"{role_list_text}\n\n"
                "**此操作不可逆，所有持有这些身份组的用户都会失去对应权限。**\n"
                "确定要继续吗？"
            ),
            colour=discord.Colour.red(),
            timeout=60,
        )
        if not confirmed:
            return

        deleted = 0
        failed = 0
        for alias, role_obj in target_roles:
            try:
                self._mark_role_event_guard(interaction.guild.id, role_obj.id, "delete")
                await role_obj.delete(reason=f"一键删除同步身份组（组: {组名}）")
                deleted += 1
                await asyncio.sleep(0.2)
            except Exception as e:
                failed += 1
                if self.logger:
                    self.logger.error(f"删除同步身份组失败 {role_obj.name}: {e}")

        server_cfg["roles"] = {}
        self._config_cache = self.config
        self._save_config()

        await interaction.edit_original_response(
            content=f"✅ 已删除 {deleted} 个同步身份组，失败 {failed} 个。映射配置已清空。",
        )

    # ====== 同步管理指令 ======
    @sync_manage.command(name="创建组", description="创建一个新的同步服务器组")
    @guild_only()
    @is_sync_admin()
    @app_commands.describe(组名="服务器组名称")
    async def create_group(self, interaction: discord.Interaction, 组名: str):
        config = self.config
        if 组名 in config.get("server_groups", {}):
            await interaction.response.send_message("❌ 该服务器组已存在", ephemeral=True)
            return
        self._ensure_group(config, 组名)
        self._config_cache = config
        self._save_config()
        await interaction.response.send_message(f"✅ 已创建服务器组 `{组名}`", ephemeral=True)

    @sync_manage.command(name="删除组", description="删除一个同步服务器组")
    @guild_only()
    @is_sync_admin()
    @app_commands.describe(组名="服务器组名称")
    async def delete_group(self, interaction: discord.Interaction, 组名: str):
        config = self.config
        if 组名 not in config.get("server_groups", {}):
            await interaction.response.send_message("❌ 该服务器组不存在", ephemeral=True)
            return
        del config["server_groups"][组名]
        self._config_cache = config
        self._save_config()
        await interaction.response.send_message(f"✅ 已删除服务器组 `{组名}`", ephemeral=True)

    @sync_manage.command(name="设置主服务器", description="将当前服务器设置为指定组的主服务器")
    @guild_only()
    @is_sync_admin()
    @app_commands.describe(组名="服务器组名称")
    async def set_main_server(self, interaction: discord.Interaction, 组名: str):
        config = self.config
        guild = interaction.guild
        group_cfg = self._ensure_group(config, 组名)
        self._ensure_server_in_group(config, 组名, guild)
        group_cfg["main_server_id"] = str(guild.id)
        self._config_cache = config
        self._save_config()
        await interaction.response.send_message(
            f"✅ 当前服务器已设置为 `{组名}` 的主服务器",
            ephemeral=True,
        )

    @sync_manage.command(name="设置子服务器", description="将当前服务器设置为指定组的子服务器")
    @guild_only()
    @is_sync_admin()
    @app_commands.describe(组名="服务器组名称")
    async def set_sub_server(self, interaction: discord.Interaction, 组名: str):
        config = self.config
        guild = interaction.guild
        group_cfg = self._ensure_group(config, 组名)
        self._ensure_server_in_group(config, 组名, guild)
        if not group_cfg.get("main_server_id"):
            await interaction.response.send_message(
                "❌ 该组还没有主服务器，请先在主服执行“设置主服务器”",
                ephemeral=True,
            )
            return
        self._config_cache = config
        self._save_config()
        await interaction.response.send_message(
            f"✅ 当前服务器已加入 `{组名}` 作为子服务器",
            ephemeral=True,
        )

    @sync_manage.command(name="移出当前服务器", description="将当前服务器从所在服务器组中移除")
    @guild_only()
    @is_sync_admin()
    async def remove_current_server(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        config = self.config
        group_name, _ = self._get_group_and_server_cfg(guild_id)
        if not group_name:
            await interaction.response.send_message("❌ 当前服务器不在任何同步组中", ephemeral=True)
            return
        group_cfg = config["server_groups"][group_name]
        group_cfg["servers"].pop(guild_id, None)
        if group_cfg.get("main_server_id") == guild_id:
            group_cfg["main_server_id"] = next(iter(group_cfg["servers"]), None)
        self._config_cache = config
        self._save_config()
        await interaction.response.send_message(f"✅ 已将当前服务器移出 `{group_name}`", ephemeral=True)

    @sync_manage.command(name="查看组", description="查看全部同步服务器组及主子服务器信息")
    @guild_only()
    @is_sync_admin()
    async def list_groups(self, interaction: discord.Interaction):
        groups = self.config.get("server_groups", {})
        if not groups:
            await interaction.response.send_message("暂无同步服务器组", ephemeral=True)
            return
        lines = []
        for group_name, group_cfg in groups.items():
            main_id = str(group_cfg.get("main_server_id") or "未设置")
            lines.append(f"【{group_name}】主服务器: {main_id}")
            for gid, server_cfg in group_cfg.get("servers", {}).items():
                role_text = f"身份组映射 {len(server_cfg.get('roles', {}))} 项"
                tag = "主" if gid == main_id else "子"
                lines.append(f"  - ({tag}) {server_cfg.get('name') or gid} [{gid}]，{role_text}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @sync_manage.command(name="身份组", description="设置当前服务器可同步身份组映射")
    @guild_only()
    @is_sync_admin()
    @app_commands.describe(名字="映射名称（建议与主服身份组同名）", role="当前服务器中的身份组")
    async def add_role_mapping(self, interaction: discord.Interaction, 名字: str, role: discord.Role):
        if role.managed:
            await interaction.response.send_message(
                "❌ 该身份组由 App/Bot/集成 管理，无法参与同步映射", ephemeral=True
            )
            return
        guild_id = str(interaction.guild.id)
        config = self.config
        group_name, server_cfg = self._get_group_and_server_cfg(guild_id)
        if not group_name or not server_cfg:
            await interaction.response.send_message("❌ 当前服务器未加入任何同步服务器组", ephemeral=True)
            return
        server_cfg.setdefault("roles", {})[名字] = role.id
        self._config_cache = config
        self._save_config()
        await interaction.response.send_message(
            f"✅ 已在 `{group_name}` 中添加映射：`{名字}` -> {role.mention}",
            ephemeral=True,
        )

    @sync_manage.command(name="一键写入主服映射", description="将主服务器所有可管理身份组一键写入映射配置")
    @guild_only()
    @is_sync_admin()
    async def bulk_map_main_roles(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        config = self.config
        group_name, server_cfg = self._get_group_and_server_cfg(guild_id)
        if not group_name or not server_cfg:
            await interaction.response.send_message("❌ 当前服务器未加入任何同步服务器组", ephemeral=True)
            return

        group_cfg = config["server_groups"][group_name]
        if str(group_cfg.get("main_server_id")) != guild_id:
            await interaction.response.send_message("❌ 该指令只能在主服务器使用", ephemeral=True)
            return

        manageable = [r for r in interaction.guild.roles if self._is_manageable_role(interaction.guild, r)]
        if not manageable:
            await interaction.response.send_message("❌ 没有 Bot 可管理的身份组", ephemeral=True)
            return

        existing = server_cfg.get("roles", {})
        added = 0
        for role in manageable:
            if role.name not in existing:
                existing[role.name] = role.id
                added += 1
            elif existing[role.name] != role.id:
                existing[role.name] = role.id
                added += 1
        server_cfg["roles"] = existing
        self._config_cache = config
        self._save_config()

        msg = (
            f"✅ 已写入/更新 {added} 个映射，当前共 {len(existing)} 项\n"
            "（映射名 = 身份组名，后续子服可用「一键智能对应子服映射」自动匹配）"
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @sync_manage.command(name="一键智能对应子服映射", description="根据主服映射名，自动匹配当前子服务器的同名身份组写入映射")
    @guild_only()
    @is_sync_admin()
    async def bulk_map_sub_roles(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        config = self.config
        group_name, server_cfg = self._get_group_and_server_cfg(guild_id)
        if not group_name or not server_cfg:
            await interaction.response.send_message("❌ 当前服务器未加入任何同步服务器组", ephemeral=True)
            return

        group_cfg = config["server_groups"][group_name]
        main_server_id = str(group_cfg.get("main_server_id") or "")
        if not main_server_id:
            await interaction.response.send_message("❌ 该组未设置主服务器", ephemeral=True)
            return
        if guild_id == main_server_id:
            await interaction.response.send_message("❌ 该指令只能在子服务器使用", ephemeral=True)
            return

        main_server_cfg = group_cfg["servers"].get(main_server_id, {})
        main_aliases = set(main_server_cfg.get("roles", {}).keys())
        if not main_aliases:
            await interaction.response.send_message("❌ 主服务器还没有映射配置，请先在主服执行「一键写入主服映射」", ephemeral=True)
            return

        local_roles_by_name: Dict[str, discord.Role] = {}
        for role in interaction.guild.roles:
            if not role.is_default() and not role.managed:
                local_roles_by_name[role.name] = role

        existing = server_cfg.get("roles", {})
        matched = 0
        unmatched = []
        for alias in sorted(main_aliases):
            local_role = local_roles_by_name.get(alias)
            if local_role:
                existing[alias] = local_role.id
                matched += 1
            else:
                unmatched.append(alias)

        server_cfg["roles"] = existing
        self._config_cache = config
        self._save_config()

        lines = [f"✅ 已自动匹配 {matched} 个映射，当前共 {len(existing)} 项"]
        if unmatched:
            preview = unmatched[:20]
            lines.append(f"⚠️ 以下 {len(unmatched)} 个主服映射在当前服务器无同名身份组：")
            lines.append("```")
            lines.extend(preview)
            if len(unmatched) > 20:
                lines.append(f"... 及另外 {len(unmatched) - 20} 个")
            lines.append("```")
            lines.append("提示：可手动用 `/同步管理 身份组` 逐个指定，或先执行「主服全量身份组同步」创建身份组后再试。")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @sync_manage.command(name="处罚同步", description="开启或关闭当前服务器在同组中的处罚同步")
    @guild_only()
    @is_sync_admin()
    @app_commands.describe(状态="开启或关闭")
    @app_commands.choices(
        状态=[
            app_commands.Choice(name="开", value="on"),
            app_commands.Choice(name="关", value="off"),
        ]
    )
    async def toggle_punishment_sync(self, interaction: discord.Interaction, 状态: str):
        guild_id = str(interaction.guild.id)
        config = self.config
        group_name, server_cfg = self._get_group_and_server_cfg(guild_id)
        if not group_name or not server_cfg:
            await interaction.response.send_message("❌ 当前服务器未加入任何同步服务器组", ephemeral=True)
            return
        enabled = 状态 == "on"
        server_cfg["punishment_sync"] = enabled
        self._config_cache = config
        self._save_config()
        await interaction.response.send_message(
            f"✅ 已{'开启' if enabled else '关闭'} `{group_name}` 内处罚同步",
            ephemeral=True,
        )

    @sync_manage.command(name="处罚公示频道", description="设置当前服务器的处罚同步公示频道")
    @guild_only()
    @is_sync_admin()
    @app_commands.describe(频道="公示频道")
    async def set_punishment_announce_channel(self, interaction: discord.Interaction, 频道: discord.TextChannel):
        guild_id = str(interaction.guild.id)
        config = self.config
        _, server_cfg = self._get_group_and_server_cfg(guild_id)
        if not server_cfg:
            await interaction.response.send_message("❌ 当前服务器未加入任何同步服务器组", ephemeral=True)
            return
        server_cfg["punishment_announce_channel"] = 频道.id
        self._config_cache = config
        self._save_config()
        await interaction.response.send_message(f"✅ 已设置处罚公示频道为 {频道.mention}", ephemeral=True)

    # ====== 对外接口：身份组操作 ======
    async def sync_add_role(self, guild: discord.Guild, member: discord.Member, role: discord.Role, reason: str = None):
        if not self.config.get("enabled", False):
            await member.add_roles(role, reason=reason)
            return

        guild_id = str(guild.id)
        group_name, source_server_cfg = self._get_group_and_server_cfg(guild_id)
        if not group_name or not source_server_cfg:
            await member.add_roles(role, reason=reason)
            return

        # 标记当前服本地变更，避免本次操作再次被 on_member_update 捕获并重复广播
        self._mark_guard(guild.id, member.id, role.id, "add")
        await member.add_roles(role, reason=reason)
        await self._propagate_role_add(guild, member, role, reason)

    async def sync_remove_role(self, guild: discord.Guild, member: discord.Member, role: discord.Role, reason: str = None):
        if not self.config.get("enabled", False):
            await member.remove_roles(role, reason=reason)
            return

        guild_id = str(guild.id)
        group_name, source_server_cfg = self._get_group_and_server_cfg(guild_id)
        if not group_name or not source_server_cfg:
            await member.remove_roles(role, reason=reason)
            return

        # 标记当前服本地变更，避免本次操作再次被 on_member_update 捕获并重复广播
        self._mark_guard(guild.id, member.id, role.id, "remove")
        await member.remove_roles(role, reason=reason)
        await self._propagate_role_remove(guild, member, role, reason)

    # ====== 对外接口：处罚同步 ======
    async def sync_punishment(
        self,
        guild: discord.Guild,
        punishment_type: str,
        member: discord.Member = None,
        moderator: discord.Member = None,
        reason: str = None,
        duration: int = None,
        warn_days: int = 0,
        punishment_id: str = None,
        img: discord.Attachment = None,
        user_id: int = None,
    ):
        guild_id = str(guild.id)
        group_name, source_server_cfg = self._get_group_and_server_cfg(guild_id)
        if not group_name or not source_server_cfg:
            return
        if not source_server_cfg.get("punishment_sync", False):
            return

        if member is not None:
            target_user_id = member.id
            target_user_name = f"{member.display_name}#{member.discriminator}"
        elif user_id is not None:
            target_user_id = int(user_id)
            target_user_name = f"用户 {target_user_id}"
            try:
                fetched_user = await self.bot.fetch_user(target_user_id)
                target_user_name = f"{fetched_user.display_name}#{fetched_user.discriminator}"
            except Exception:
                pass
        else:
            if self.logger:
                self.logger.error("sync_punishment: 必须提供 member 或 user_id 参数")
            return

        record = {
            "id": punishment_id or uuid.uuid4().hex[:8],
            "type": punishment_type,
            "source_guild": guild.id,
            "source_guild_name": guild.name,
            "user_id": target_user_id,
            "user_name": target_user_name,
            "moderator_id": moderator.id if moderator else None,
            "moderator_name": f"{moderator.display_name}#{moderator.discriminator}" if moderator else "系统",
            "reason": reason,
            "duration": int(duration) if duration else None,
            "warn_days": int(warn_days or 0),
            "img_url": img.url if img else None,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        group_servers = self.config["server_groups"][group_name]["servers"]
        for target_guild_id, target_server_cfg in group_servers.items():
            if target_guild_id == guild_id:
                continue
            if not target_server_cfg.get("punishment_sync", False):
                continue
            target_guild = self.bot.get_guild(int(target_guild_id))
            if not target_guild:
                continue
            try:
                await self._apply_punishment_in_guild(target_guild, record, target_server_cfg)
            except discord.Forbidden:
                if self.logger:
                    self.logger.warning(f"处罚同步权限不足: {target_guild.name}")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"处罚同步失败 {target_guild.name}: {e}")

    async def sync_revoke_punishment(
        self,
        guild: discord.Guild,
        punishment_id: str,
        moderator: discord.Member,
        reason: str = None,
    ):
        guild_id = str(guild.id)
        group_name, source_server_cfg = self._get_group_and_server_cfg(guild_id)
        if not group_name or not source_server_cfg:
            return
        if not source_server_cfg.get("punishment_sync", False):
            return

        group_servers = self.config["server_groups"][group_name]["servers"]
        for target_guild_id, target_server_cfg in group_servers.items():
            if target_guild_id == guild_id:
                continue
            if not target_server_cfg.get("punishment_sync", False):
                continue
            target_guild = self.bot.get_guild(int(target_guild_id))
            if not target_guild:
                continue
            await self._revoke_punishment_in_guild(target_guild, punishment_id, moderator, reason, target_server_cfg)

    async def _revoke_punishment_in_guild(
        self,
        guild: discord.Guild,
        punishment_id: str,
        moderator: discord.Member,
        reason: str = None,
        server_cfg: Optional[Dict] = None,
    ):
        punish_dir = pathlib.Path("data") / "punish" / str(guild.id)
        if not punish_dir.exists():
            return
        record_file = punish_dir / f"{punishment_id}.json"
        if not record_file.exists():
            return

        try:
            with open(record_file, "r", encoding="utf-8") as f:
                record = json.load(f)

            user_id = int(record["user_id"])
            user_obj = await self._safe_fetch_member(guild, user_id)

            if record["type"] == "mute" and user_obj:
                try:
                    await user_obj.timeout(None, reason=f"同步撤销处罚: {reason}")
                    if record.get("warn_days", 0) > 0:
                        warned_role_id = self._get_warned_role_id(guild.id)
                        if warned_role_id:
                            warned_role = guild.get_role(int(warned_role_id))
                            if warned_role and warned_role in user_obj.roles:
                                await user_obj.remove_roles(warned_role, reason="同步撤销处罚")
                except discord.Forbidden:
                    pass
            elif record["type"] == "ban":
                try:
                    await guild.unban(discord.Object(id=user_id), reason=f"同步撤销处罚: {reason}")
                except discord.Forbidden:
                    pass
                except discord.NotFound:
                    pass

            record_file.unlink(missing_ok=True)
            if not server_cfg:
                _, server_cfg = self._get_group_and_server_cfg(str(guild.id))
            announce_channel_id = server_cfg.get("punishment_announce_channel") if server_cfg else None
            if announce_channel_id:
                announce_channel = guild.get_channel(int(announce_channel_id))
                if announce_channel:
                    embed = discord.Embed(title="🔓 撤销处罚", color=discord.Color.green())
                    embed.add_field(name="处罚ID", value=punishment_id)
                    embed.add_field(name="用户", value=f"<@{user_id}>")
                    embed.add_field(name="操作者", value=moderator.mention)
                    embed.add_field(name="原因", value=reason or "同步撤销", inline=False)
                    await announce_channel.send(embed=embed)
        except Exception as e:
            if self.logger:
                self.logger.error(f"撤销处罚失败 {guild.name}: {e}")


async def setup(bot):
    await bot.add_cog(ServerSyncCommands(bot))


class ManualRoleSyncView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="手动同步我的主服身份组",
        style=discord.ButtonStyle.primary,
        emoji="🔄",
        custom_id="sync:manual_main_to_sub",
    )
    async def manual_sync(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ 该按钮只能在服务器中使用", ephemeral=True)
            return

        sync_cog = interaction.client.get_cog("ServerSyncCommands")
        if not sync_cog:
            await interaction.response.send_message("❌ 同步模块未加载", ephemeral=True)
            return
        if not sync_cog.config.get("enabled", False):
            await interaction.response.send_message("❌ 同步功能未启用", ephemeral=True)
            return

        guild_id = str(interaction.guild.id)
        group_name, _ = sync_cog._get_group_and_server_cfg(guild_id)
        if not group_name:
            await interaction.response.send_message("❌ 当前服务器未加入任何同步服务器组", ephemeral=True)
            return

        group_cfg = sync_cog.config.get("server_groups", {}).get(group_name, {})
        main_server_id = str(group_cfg.get("main_server_id") or "")
        if not main_server_id or main_server_id == guild_id:
            await interaction.response.send_message("❌ 当前服务器不是可同步的子服务器", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        added_count = await sync_cog._sync_member_roles_from_main_to_sub(interaction.user, group_name)
        if added_count > 0:
            await interaction.followup.send(f"✅ 同步完成，已新增 {added_count} 个身份组", ephemeral=True)
        else:
            await interaction.followup.send("ℹ️ 同步完成，无可新增的身份组", ephemeral=True)

