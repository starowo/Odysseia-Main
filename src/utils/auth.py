import json
import logging
import pathlib
from functools import wraps
import discord
from discord import app_commands

logger = logging.getLogger('bot')

def _load_config():
    """加载配置文件"""
    try:
        path = pathlib.Path('config.json')
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def is_senior_admin_member(member: discord.Member) -> bool:
    """检查成员是否为高级管理员"""
    if not member.guild:
        return False

    if member.guild_permissions.administrator:
        return True
    
    config = _load_config()
    senior_admin_roles = config.get('senior_admins', [])
    if not senior_admin_roles:
        return False

    for role_id in senior_admin_roles:
        role = member.guild.get_role(int(role_id))
        if role and role in member.roles:
            return True
            
    return False

def is_admin_member(member: discord.Member) -> bool:
    """检查成员是否为管理员"""
    if is_senior_admin_member(member):
        return True
    
    if not member.guild:
        return False
        
    config = _load_config()
    admin_roles = config.get('admins', [])
    if not admin_roles:
        return False

    for role_id in admin_roles:
        role = member.guild.get_role(int(role_id))
        if role and role in member.roles:
            return True
        
    return False

async def check_senior_admin_permission(interaction: discord.Interaction) -> bool:
    """检查用户是否为高级管理员"""
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False

    return is_senior_admin_member(interaction.user)

async def check_admin_permission(interaction: discord.Interaction) -> bool:
    """检查用户是否为管理员"""
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        logger.info(f"user 不是discord.Member")
        return False
        
    return is_admin_member(interaction.user)

def is_senior_admin():
    """一个 app_commands.check 装饰器，用于验证用户是否为高级管理员。"""
    async def predicate(interaction: discord.Interaction) -> bool:
        if await check_senior_admin_permission(interaction):
            return True
        else:
            await interaction.response.send_message("❌ 您没有权限使用此命令", ephemeral=True)
            return False
    return app_commands.check(predicate)

def is_admin():
    """一个 app_commands.check 装饰器，用于验证用户是否为管理员。"""
    async def predicate(interaction: discord.Interaction) -> bool:
        if await check_admin_permission(interaction):
            return True
        else:
            await interaction.response.send_message("❌ 您没有权限使用此命令", ephemeral=True)
            return False
    return app_commands.check(predicate)

def guild_only():
    """一个 app_commands.check 装饰器，用于验证命令是否在服务器中使用。"""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is not None:
            return True
        else:
            await interaction.response.send_message("❌ 此命令只能在服务器中使用", ephemeral=True)
            return False
    return app_commands.check(predicate)