"""反商业化模块配置加载、深合并与时长格式化工具。"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from src.utils.config_helper import _load_config

MAX_TIMEOUT_SECONDS = 28 * 86400

DEFAULT_ANTICOMMERCIAL_CONFIG: Dict[str, Any] = {
    "role_ids": [],
    "admin_role_ids": [],
    "shared_guild_ids": [],
    "global_count": True,
    "announce_in_command_channel": True,
    "db_path": "data/anticommercial/aff_records.db",
    "default_reason": "违规发布带推广/返利性质（aff）的内容",
    "tiers": [
        {"mute": "0d", "warn_days": 3},
        {"mute": "3d", "warn_days": 30},
    ],
    "dm_title_warn": "⚠️ 警告处罚（推广/aff 违规）",
    "dm_title_mute": "🔇 禁言与警告处罚（推广/aff 违规）",
    "dm_template": (
        "您在 **{guild_name}** 因 **{reason}** 被处以 **{punish_summary}**"
        "（当前累计有效记录：**第 {offense_no} 次**）。\n\n"
        "📌 **说明**\n"
        "社区允许正常交流合规平台与工具，但严禁附带任何个人邀请码、返利后缀"
        "（如 `aff` / `ref` 等）变相获利。后续分享合规渠道时，请去除推广参数、仅发送纯净链接。\n\n"
        "⚠️ **累犯提示**\n"
        "{next_tier_tip}\n\n"
        "请遵守[社区规则]({rules_url})（{appeal_tip} 处罚ID：`{record_id}`）。"
    ),
    "next_tier_tip_first": "若后续再次发布带 aff/返利性质的内容，处罚将升级为：**{next_punish_summary}**。",
    "next_tier_tip_repeat": "您已多次触发相关违规记录；若继续违规，管理团队将视情节介入从重处理。",
    "rules_url": "https://discord.com/channels/1134557553011998840/1554734640147861564/1554744768196780057",
    "appeal_tip": "请勿直接回复此私信；如认为存在事实性误判，请阅读规则后前往 `📪|意见与投诉投稿` 申诉。",
}


class SafeFormatDict(dict):
    """格式化模板时保留未知占位符，避免配置笔误导致运行期异常。"""

    def __missing__(self, key: str) -> str:
        return "{" + str(key) + "}"


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _normalize_id_list(raw: Any) -> List[int]:
    if raw is None:
        return []
    if isinstance(raw, (int, str)):
        raw = [raw]
    result: List[int] = []
    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            try:
                val = int(str(item).strip())
                if val > 0 and val not in result:
                    result.append(val)
            except (ValueError, TypeError):
                continue
    return result


def get_anticommercial_config(
    guild_id: Optional[int] = None,
    raw_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """合并默认配置、全局 anticommercial 配置与 server_override[guild_id] 配置。"""
    root = raw_config if raw_config is not None else _load_config()
    cfg = copy.deepcopy(DEFAULT_ANTICOMMERCIAL_CONFIG)

    global_ac = root.get("anticommercial")
    if isinstance(global_ac, dict):
        cfg = _deep_merge(cfg, global_ac)

    if guild_id is not None:
        guild_override = root.get("server_override", {}).get(str(guild_id), {})
        if isinstance(guild_override, dict):
            guild_ac = guild_override.get("anticommercial")
            if isinstance(guild_ac, dict):
                cfg = _deep_merge(cfg, guild_ac)
            # 兼容直接在 server_override 下写扁平角色配置
            for flat_key in ("anticommercial_role_ids", "anticommercial_role_id"):
                if flat_key in guild_override and (
                    not isinstance(guild_ac, dict) or "role_ids" not in guild_ac
                ):
                    cfg["role_ids"] = guild_override[flat_key]
                    break

    cfg["role_ids"] = _normalize_id_list(cfg.get("role_ids"))
    cfg["admin_role_ids"] = _normalize_id_list(cfg.get("admin_role_ids"))
    cfg["shared_guild_ids"] = _normalize_id_list(cfg.get("shared_guild_ids"))
    cfg["global_count"] = bool(cfg.get("global_count", True))
    cfg["announce_in_command_channel"] = bool(cfg.get("announce_in_command_channel", True))
    return cfg


def resolve_scope_guild_ids(guild_id: int, cfg: Dict[str, Any]) -> Optional[List[int]]:
    """计算累计次数查询覆盖的服务器范围。

    返回 None 表示全库跨服统计；返回列表表示仅统计列表内的服务器。
    """
    shared = cfg.get("shared_guild_ids") or []
    if shared:
        if int(guild_id) in shared:
            return list(shared)
        return [int(guild_id)]
    if cfg.get("global_count", True):
        return None
    return [int(guild_id)]


def can_cross_guild_operate(current_guild_id: int, target_guild_id: int, cfg: Dict[str, Any]) -> bool:
    """校验当前服务器是否允许跨服操作目标服务器的记录。"""
    if int(current_guild_id) == int(target_guild_id):
        return True
    shared = cfg.get("shared_guild_ids") or []
    return bool(shared and int(current_guild_id) in shared and int(target_guild_id) in shared)


def parse_duration(time_str: Any) -> Tuple[int, str]:
    """解析时长字符串（支持 0, Ns, Nm, Nh, Nd），失败返回 (-1, '未知时间')。"""
    if time_str is None:
        return 0, "0秒"
    if isinstance(time_str, int) and not isinstance(time_str, bool):
        if time_str < 0 or time_str > MAX_TIMEOUT_SECONDS:
            return -1, "未知时间"
        if time_str == 0:
            return 0, "0秒"
        if time_str % 86400 == 0:
            return time_str, f"{time_str // 86400}天"
        if time_str % 3600 == 0:
            return time_str, f"{time_str // 3600}小时"
        if time_str % 60 == 0:
            return time_str, f"{time_str // 60}分钟"
        return time_str, f"{time_str}秒"

    raw = str(time_str).strip().lower()
    if raw in ("", "0", "0s", "0m", "0h", "0d"):
        return 0, "0秒"

    units = {
        "s": (1, "秒"),
        "m": (60, "分钟"),
        "h": (3600, "小时"),
        "d": (86400, "天"),
    }
    suffix = raw[-1]
    if suffix not in units:
        return -1, "未知时间"
    num_part = raw[:-1].strip()
    if not num_part.isdigit():
        return -1, "未知时间"

    amount = int(num_part)
    seconds = amount * units[suffix][0]
    if seconds < 0 or seconds > MAX_TIMEOUT_SECONDS:
        return -1, "未知时间"
    if seconds == 0:
        return 0, "0秒"
    return seconds, f"{amount}{units[suffix][1]}"


def format_punish_summary(mute_seconds: int, mute_label: str, warn_days: int) -> str:
    """生成人类可读的处罚摘要，例如 '警告 3 天' 或 '禁言 3天 + 警告 30 天'。"""
    parts: List[str] = []
    if mute_seconds > 0:
        parts.append(f"禁言 {mute_label}")
    if warn_days > 0:
        parts.append(f"警告 {warn_days} 天")
    return " + ".join(parts) if parts else "无附加动作"


def validate_all_tiers(tiers: Any) -> None:
    """校验全部阶梯档位配置，防止后续档位存在隐藏格式错误。"""
    if not isinstance(tiers, list) or not tiers:
        raise ValueError("anticommercial.tiers 配置为空或格式错误")
    for idx in range(len(tiers)):
        resolve_tier(tiers, idx)


def resolve_tier(tiers: Any, prior_active_count: int) -> Tuple[int, str, int, str]:
    """根据当前有效前科数匹配档位，返回 (mute_seconds, mute_label, warn_days, summary)。"""
    if not isinstance(tiers, list) or not tiers:
        raise ValueError("anticommercial.tiers 配置为空或格式错误")

    idx = min(max(0, int(prior_active_count)), len(tiers) - 1)
    entry = tiers[idx]
    if not isinstance(entry, dict):
        raise ValueError(f"anticommercial.tiers[{idx}] 必须为对象")

    mute_seconds, mute_label = parse_duration(entry.get("mute", "0d"))
    if mute_seconds < 0:
        raise ValueError(f"anticommercial.tiers[{idx}].mute 时长无效（需在 0~28d 之间）")

    raw_warn = entry.get("warn_days", 0)
    if isinstance(raw_warn, bool):
        raise ValueError(f"anticommercial.tiers[{idx}].warn_days 必须为整数")
    try:
        warn_days = int(raw_warn)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"anticommercial.tiers[{idx}].warn_days 必须为整数") from exc

    if warn_days < 0:
        raise ValueError(f"anticommercial.tiers[{idx}].warn_days 不能为负数")
    if mute_seconds == 0 and warn_days == 0:
        raise ValueError(f"anticommercial.tiers[{idx}] 禁言时长与警告天数不能同时为 0")

    summary = format_punish_summary(mute_seconds, mute_label, warn_days)
    return mute_seconds, mute_label, warn_days, summary
