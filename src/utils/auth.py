import json
import pathlib
from functools import wraps
import discord
from discord import app_commands

def _load_config():
    """加载配置文件"""
    try:
        path = pathlib.Path('config.json')
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

async def check_senior_admin_permission(interaction: discord.Interaction) -> bool:
    """检查用户是否为高级管理员"""
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False

    member: discord.Member = interaction.user
    if member.guild_permissions.administrator:
        return True
    
    config = _load_config()
    senior_admin_roles = config.get('senior_admins', [])
    if not senior_admin_roles:
        return False

    for role_id in senior_admin_roles:
        role = interaction.guild.get_role(role_id)
        if role and role in member.roles:
            return True
            
    return False

async def check_admin_permission(interaction: discord.Interaction) -> bool:
    """检查用户是否为管理员"""
    if await check_senior_admin_permission(interaction):
        return True
    
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False
        
    config = _load_config()
    admin_roles = config.get('admins', [])
    if not admin_roles:
        return False

    member: discord.Member = interaction.user
    for role_id in admin_roles:
        role = interaction.guild.get_role(role_id)
        if role and role in member.roles:
            return True
            
    return False

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