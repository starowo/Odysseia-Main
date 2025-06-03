#!/usr/bin/env python3
"""
获取新服务器信息的脚本
运行后会显示机器人所在所有服务器的信息
"""

import asyncio
import json
import discord
from discord.ext import commands

def load_config():
    """加载配置文件"""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print("❌ 找不到config.json文件")
        return None
    except json.JSONDecodeError:
        print("❌ config.json格式错误")
        return None

async def get_all_servers_info():
    """获取所有服务器信息"""
    print("🔍 正在获取所有服务器信息...")
    
    config = load_config()
    if not config:
        return
    
    # 创建机器人实例
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.members = True
    
    bot = commands.Bot(command_prefix='!', intents=intents)
    
    @bot.event
    async def on_ready():
        print(f"✅ 机器人已连接: {bot.user}")
        print(f"📊 机器人在 {len(bot.guilds)} 个服务器中\n")
        
        for i, guild in enumerate(bot.guilds, 1):
            print(f"{'='*50}")
            print(f"🏰 服务器 {i}: {guild.name}")
            print(f"   服务器ID: {guild.id}")
            print(f"   服务器所有者: {guild.owner.display_name} (ID: {guild.owner.id})")
            print(f"   成员数量: {guild.member_count}")
            
            print(f"\n👥 服务器成员:")
            admin_members = []
            for member in guild.members:
                if not member.bot:  # 只显示真实用户
                    # 检查是否有管理员权限
                    if member.guild_permissions.administrator:
                        admin_members.append(member)
                        print(f"   👑 {member.display_name} ({member.name}) - ID: {member.id} [管理员]")
                    else:
                        print(f"   👤 {member.display_name} ({member.name}) - ID: {member.id}")
            
            print(f"\n🎭 服务器角色:")
            admin_roles = []
            for role in guild.roles:
                if role.name != "@everyone":  # 跳过@everyone角色
                    if role.permissions.administrator:
                        admin_roles.append(role)
                        print(f"   👑 {role.name} - ID: {role.id} [管理员角色]")
                    else:
                        print(f"   🏷️ {role.name} - ID: {role.id}")
            
            print(f"\n🔧 建议的管理员配置:")
            suggested_admins = []
            
            # 添加服务器所有者
            suggested_admins.append(guild.owner.id)
            
            # 添加其他管理员成员
            for member in admin_members:
                if member.id != guild.owner.id:
                    suggested_admins.append(member.id)
            
            # 或者使用管理员角色（推荐）
            if admin_roles:
                print(f"   方案1 - 使用管理员角色: {[role.id for role in admin_roles]}")
            
            print(f"   方案2 - 使用用户ID: {suggested_admins}")
            
            print(f"\n📋 配置示例:")
            print(f'   "admins": {suggested_admins},')
            
            print(f"\n📢 日志频道建议:")
            text_channels = [ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages]
            if text_channels:
                log_channel = text_channels[0]  # 推荐第一个可用频道
                print(f'   "logging": {{')
                print(f'       "enabled": true,')
                print(f'       "guild_id": {guild.id},')
                print(f'       "channel_id": {log_channel.id},')
                print(f'       "level": "INFO"')
                print(f'   }},')
            
            print()
        
        await bot.close()
    
    try:
        await bot.start(config['token'])
    except Exception as e:
        print(f"❌ 连接失败: {e}")

if __name__ == "__main__":
    asyncio.run(get_all_servers_info()) 