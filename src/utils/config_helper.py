"""配置辅助工具，支持服务器特定配置覆盖"""
import json
import pathlib
from typing import Any, Optional
import discord


_config_cache = {}
_config_cache_mtime = None


def _load_config():
    """加载配置文件并缓存"""
    global _config_cache, _config_cache_mtime
    try:
        path = pathlib.Path('config.json')
        mtime = path.stat().st_mtime
        if _config_cache_mtime != mtime:
            with open(path, 'r', encoding='utf-8') as f:
                _config_cache = json.load(f)
            _config_cache_mtime = mtime
        return _config_cache
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get_config_value(key: str, guild_id: Optional[int] = None, default: Any = None) -> Any:
    """
    获取配置值，支持服务器特定覆盖
    
    Args:
        key: 配置键名
        guild_id: 服务器ID，如果提供则会查找服务器特定配置
        default: 默认值
    
    Returns:
        配置值，优先返回服务器特定配置，否则返回全局配置
    """
    config = _load_config()
    
    # 如果提供了服务器ID，先尝试获取服务器特定配置
    if guild_id is not None:
        server_override = config.get('server_override', {})
        guild_config = server_override.get(str(guild_id), {})
        if key in guild_config:
            return guild_config[key]
    
    # 否则返回全局配置
    return config.get(key, default)


def get_config_for_guild(guild_id: Optional[int] = None) -> dict:
    """
    获取完整的配置字典，包含服务器特定覆盖
    
    Args:
        guild_id: 服务器ID
    
    Returns:
        合并后的配置字典（服务器特定配置会覆盖全局配置）
    """
    config = _load_config()
    
    # 复制全局配置
    result = config.copy()
    
    # 如果提供了服务器ID，合并服务器特定配置
    if guild_id is not None:
        server_override = config.get('server_override', {})
        guild_config = server_override.get(str(guild_id), {})
        result.update(guild_config)
    
    return result


def get_guild_id_from_interaction(interaction: discord.Interaction) -> Optional[int]:
    """从交互中获取服务器ID"""
    return interaction.guild.id if interaction.guild else None


def get_guild_id_from_member(member: discord.Member) -> Optional[int]:
    """从成员中获取服务器ID"""
    return member.guild.id if member.guild else None

