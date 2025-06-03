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
                    print(f"   - {member.display_name} ({member.name}) - ID: {member.id}")
            
            print(f"\n🎭 服务器角色:")
            for role in guild.roles:
                if role.name != "@everyone":  # 跳过@everyone角色
                    print(f"   - {role.name} - ID: {role.id}")
            
            print(f"\n💡 配置建议:")
            print(f"要将你自己设为管理员，请:")
            print(f"1. 在config.json中找到 'admins' 字段")
            print(f"2. 将你的用户ID或管理员角色ID添加到列表中")
            print(f"3. 例如: \"admins\": [{guild.owner_id}]  # 服务器所有者ID")
            
        await bot.close()
    
    try:
        await bot.start(config['token'])
    except Exception as e:
        print(f"❌ 连接失败: {e}")

if __name__ == "__main__":
    asyncio.run(get_ids()) 