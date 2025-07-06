#!/usr/bin/env python3
"""
获取用户ID和角色ID的辅助脚本
运行后会显示服务器中的用户和角色信息
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

async def get_ids():
    """获取ID信息"""
    print("🔍 正在获取用户和角色ID信息...")
    
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
        
        # 获取第一个服务器的信息
        if bot.guilds:
            guild = bot.guilds[0]
            print(f"\n📍 服务器信息: {guild.name} (ID: {guild.id})")
            
            print(f"\n👥 服务器成员:")
            for member in guild.members:
                if not member.bot:  # 只显示真实用户
                    status = "👑 所有者" if member.id == guild.owner_id else "👤 成员"
                    print(f"   - {member.display_name} ({member.name}) - ID: {member.id} {status}")
            
            print(f"\n🎭 服务器角色:")
            for role in guild.roles:
                if role.name != "@everyone":  # 跳过@everyone角色
                    perms_info = ""
                    if role.permissions.administrator:
                        perms_info = " [管理员]"
                    elif role.permissions.manage_guild:
                        perms_info = " [管理服务器]"
                    print(f"   - {role.name} - ID: {role.id}{perms_info}")
            
            # 新增：检查论坛频道（匿名反馈专用）
            forum_channels = [ch for ch in guild.channels if isinstance(ch, discord.ForumChannel)]
            if forum_channels:
                print(f"\n📋 论坛频道 (匿名反馈系统可用):")
                for channel in forum_channels:
                    print(f"   - 💬 {channel.name} - ID: {channel.id}")
            else:
                print(f"\n📋 论坛频道:")
                print(f"   ⚠️ 未检测到论坛频道")
                print(f"   💡 匿名反馈系统需要论坛频道才能工作")
            
            print(f"\n💡 配置建议:")
            print(f"【管理员配置】")
            print(f"在config.json中设置 'admins' 字段:")
            print(f"\"admins\": [{guild.owner_id}],  # 服务器所有者ID")
            
            print(f"\n【赛事管理配置】")
            print(f"如需使用赛事管理功能，设置:")
            print(f"\"event_managers\": [],  # 赛事管理员用户ID")
            print(f"\"highest_role_available\": 0,  # 最高可管理身份组ID (0=无限制)")
            
            if forum_channels:
                print(f"\n【匿名反馈配置】")
                print(f"✅ 检测到论坛频道，匿名反馈系统可用")
                print(f"📋 在cogs配置中启用:")
                print(f"\"anonymous_feedback\": {{\"enabled\": true}}")
            else:
                print(f"\n【匿名反馈配置】")
                print(f"⚠️ 需要先创建论坛频道来使用匿名反馈功能")
                print(f"💡 在Discord服务器设置中创建一个论坛频道")
            
        await bot.close()
    
    try:
        await bot.start(config['token'])
    except Exception as e:
        print(f"❌ 连接失败: {e}")

if __name__ == "__main__":
    asyncio.run(get_ids()) 