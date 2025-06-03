#!/usr/bin/env python3
"""
获取服务器信息的脚本
运行后会显示机器人所在服务器的信息，并提供配置建议
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

async def get_server_info():
    """获取服务器信息并提供配置建议"""
    print("🔍 正在获取服务器信息...")
    
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
        
        if len(bot.guilds) == 0:
            print("❌ 机器人未加入任何服务器")
            await bot.close()
            return
        
        # 显示所有服务器信息
        for i, guild in enumerate(bot.guilds, 1):
            print(f"{'='*50}")
            print(f"🏰 服务器 {i}: {guild.name}")
            print(f"   服务器ID: {guild.id}")
            print(f"   服务器所有者: {guild.owner.display_name} (ID: {guild.owner.id})")
            print(f"   成员数量: {guild.member_count}")
            
            print(f"\n👥 管理员成员:")
            admin_members = []
            for member in guild.members:
                if not member.bot and member.guild_permissions.administrator:
                    admin_members.append(member)
                    print(f"   👑 {member.display_name} ({member.name}) - ID: {member.id}")
            
            print(f"\n🎭 重要身份组:")
            important_roles = []
            for role in guild.roles:
                if role.name != "@everyone":
                    # 显示有用的身份组
                    if (role.permissions.administrator or 
                        role.permissions.manage_guild or
                        role.permissions.manage_channels or
                        any(keyword in role.name.lower() for keyword in 
                            ['verified', 'member', 'admin', 'mod', 'quiz', 'warn', 'buffer'])):
                        important_roles.append(role)
                        permissions = []
                        if role.permissions.administrator:
                            permissions.append("管理员")
                        if role.permissions.manage_guild:
                            permissions.append("管理服务器")
                        if role.permissions.manage_channels:
                            permissions.append("管理频道")
                        
                        perm_str = f" [{', '.join(permissions)}]" if permissions else ""
                        print(f"   🏷️ {role.name} - ID: {role.id}{perm_str}")
            
            print(f"\n📢 可用频道:")
            text_channels = [ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages]
            forum_channels = [ch for ch in guild.channels if isinstance(ch, discord.ForumChannel)]
            
            # 显示文字频道
            for channel in text_channels[:10]:  # 只显示前10个
                print(f"   📺 {channel.name} - ID: {channel.id}")
            
            if len(text_channels) > 10:
                print(f"   ... 还有 {len(text_channels) - 10} 个频道")
            
            # 显示论坛频道（匿名反馈专用）
            if forum_channels:
                print(f"\n📋 论坛频道 (匿名反馈系统可用):")
                for channel in forum_channels[:5]:
                    print(f"   💬 {channel.name} - ID: {channel.id}")
                if len(forum_channels) > 5:
                    print(f"   ... 还有 {len(forum_channels) - 5} 个论坛频道")
        
        # 如果有多个服务器，显示所有服务器但说明配置结构
        if len(bot.guilds) > 1:
            print(f"\n💡 检测到机器人在多个服务器中")
            print(f"📝 当前使用单一配置文件架构")
            print(f"🔗 可使用频道同步功能在服务器间同步身份组和处罚记录")
            print(f"📖 详细配置请参考：docs/sync_guide.md")
        
        # 为第一个服务器提供配置建议
        guild = bot.guilds[0]
        print(f"\n{'='*50}")
        print(f"🔧 针对服务器 '{guild.name}' 的配置建议:")
        print(f"{'='*50}")
        
        # 管理员配置建议
        print(f"\n👑 管理员配置建议:")
        suggested_admins = [guild.owner.id]
        for member in admin_members:
            if member.id != guild.owner.id:
                suggested_admins.append(member.id)
        
        print(f'   "admins": {suggested_admins},')
        
        # 身份组配置建议
        print(f"\n🎭 身份组配置建议:")
        print(f"   // 请根据您的服务器实际身份组进行配置")
        print(f'   "verified_role_id": 0,  // 已验证用户身份组')
        print(f'   "buffer_role_id": 0,    // 验证缓冲身份组')
        print(f'   "quiz_role_id": 0,      // 答题验证身份组')
        print(f'   "warned_role_id": 0,    // 警告状态身份组')
        
        # 频道配置建议
        print(f"\n📺 频道配置建议:")
        if text_channels:
            log_channel = text_channels[0]
            print(f'   "punish_announce_channel_id": 0,  // 处罚公示频道')
            print(f'   "logging": {{')
            print(f'       "enabled": true,')
            print(f'       "guild_id": {guild.id},')
            print(f'       "channel_id": {log_channel.id},  // 建议使用: {log_channel.name}')
            print(f'       "level": "INFO"')
            print(f'   }},')
        
        # 赛事管理配置建议
        print(f"\n🏆 赛事管理配置建议:")
        print(f'   "event_managers": [],  // 赛事管理员用户ID')
        print(f'   "highest_role_available": 0,  // 最高可管理身份组ID (0=无限制)')
        
        # 新增：功能模块配置建议
        print(f"\n🧩 功能模块配置建议:")
        print(f'   "cogs": {{')
        print(f'       "thread_manage": {{"enabled": true, "description": "子区自助管理功能"}},')
        print(f'       "admin": {{"enabled": true, "description": "管理员功能"}},')
        print(f'       "anonymous_feedback": {{"enabled": true, "description": "匿名反馈系统"}},')
        print(f'       "verify": {{"enabled": true, "description": "验证系统"}},')
        print(f'       "misc": {{"enabled": true, "description": "杂项功能"}},')
        print(f'       "event": {{"enabled": false, "description": "赛事管理功能"}},')
        print(f'       "bot_manage": {{"enabled": true, "description": "机器人管理功能"}},')
        print(f'       "sync": {{"enabled": false, "description": "服务器同步功能"}}')
        print(f'   }},')
        
        # 新增：匿名反馈系统说明
        if forum_channels:
            print(f"\n📫 匿名反馈系统说明:")
            print(f"   ✅ 自动化系统，无需额外配置")
            print(f"   📋 仅在论坛频道的帖子内可用")
            print(f"   🔐 基于加密cookie的完全匿名机制") 
            print(f"   ⚠️ 三次警告制：用户在特定帖主下累计3次警告将被封禁")
            print(f"   👤 帖主溯源：帖主可查看匿名反馈者身份")
            print(f"   🎯 支持多媒体：文字、图片、文件反馈")
            print(f"   👎 自动处理：收到10个👎自动删除并警告用户")
            print(f"   🛡️ 管理工具：管理员和帖主都有相应管理功能")
        else:
            print(f"\n📫 匿名反馈系统说明:")
            print(f"   ⚠️ 需要论坛频道才能使用匿名反馈功能")
            print(f"   💡 建议创建至少一个论坛频道来启用此功能")
        
        print(f"\n💡 配置提示:")
        print(f"📝 请复制上述建议到您的 config.json 文件中")
        print(f"🔧 使用 快速部署.py 脚本可以自动生成完整配置")
        print(f"✅ 使用 config_validator.py 可以验证配置的正确性")
        print(f"🔍 使用 check_permissions.py 可以检查机器人权限")
        
        print(f"\n🆕 新功能亮点:")
        print(f"📢 匿名反馈系统：论坛专用，完全自动化的匿名互动功能")
        print(f"🛡️ 三次警告制：智能的用户管理和封禁机制")
        print(f"👤 帖主权限：帖主可管理自己帖子的匿名反馈")
        print(f"🔧 配置优化：智能缓存和性能提升")
        print(f"📊 日志增强：更详细的操作记录和错误跟踪")
        
        print(f"\n🔗 相关资源:")
        print(f"📚 详细部署指南：部署指南.md")
        print(f"📖 同步功能指南：docs/sync_guide.md") 
        print(f"🔧 快速配置工具：快速部署.py")
        print(f"✅ 配置验证工具：config_validator.py")
        print(f"🔍 权限检查工具：check_permissions.py")
        print(f"🆔 ID获取助手：get_ids.py")
        
        await bot.close()
    
    try:
        await bot.start(config['token'])
    except Exception as e:
        print(f"❌ 连接失败: {e}")

if __name__ == "__main__":
    asyncio.run(get_server_info()) 