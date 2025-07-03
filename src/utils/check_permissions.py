#!/usr/bin/env python3
"""
检查机器人权限脚本
确保机器人在普通频道和子区中都有足够权限
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

async def check_permissions():
    """检查机器人权限"""
    print("🔍 开始检查机器人权限...")
    
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
        
        # 获取服务器
        guild = None
        if 'logging' in config and 'guild_id' in config['logging']:
            guild = bot.get_guild(config['logging']['guild_id'])
        
        if not guild:
            print("❌ 无法找到配置的服务器")
            await bot.close()
            return
        
        print(f"📍 检查服务器: {guild.name}")
        
        # 获取机器人成员对象
        bot_member = guild.get_member(bot.user.id)
        if not bot_member:
            print("❌ 机器人不在服务器中")
            await bot.close()
            return
        
        # 检查管理员权限
        if bot_member.guild_permissions.administrator:
            print("✅ 机器人拥有管理员权限")
            print("🎉 权限检查通过！可以在任何频道和子区中使用")
        else:
            print("⚠️ 机器人没有管理员权限，检查具体权限...")
            
            permissions = bot_member.guild_permissions
            required_perms = [
                ('发送消息', permissions.send_messages),
                ('在子区发送消息', permissions.send_messages_in_threads),
                ('创建公开子区', permissions.create_public_threads),
                ('创建私密子区', permissions.create_private_threads),
                ('管理子区', permissions.manage_threads),
                ('嵌入链接', permissions.embed_links),
                ('附加文件', permissions.attach_files),
                ('查看消息历史', permissions.read_message_history),
                ('添加反应', permissions.add_reactions),
                ('管理消息', permissions.manage_messages),
                ('查看频道', permissions.view_channel),
            ]
            
            missing_perms = []
            for perm_name, has_perm in required_perms:
                if has_perm:
                    print(f"✅ {perm_name}")
                else:
                    print(f"❌ {perm_name}")
                    missing_perms.append(perm_name)
            
            if missing_perms:
                print(f"\n⚠️ 缺少权限: {', '.join(missing_perms)}")
                print("💡 建议重新邀请机器人并授予管理员权限")
            else:
                print("\n✅ 所有必要权限都已具备")
        
        # 测试频道访问
        print("\n📋 测试频道访问...")
        text_channels = guild.text_channels[:3]  # 测试前3个频道
        
        for channel in text_channels:
            try:
                # 检查频道权限
                channel_perms = channel.permissions_for(bot_member)
                if channel_perms.send_messages:
                    print(f"✅ 可以在 #{channel.name} 发送消息")
                    
                    # 检查子区权限
                    if channel.threads:
                        thread = channel.threads[0]
                        thread_perms = thread.permissions_for(bot_member)
                        if thread_perms.send_messages:
                            print(f"  ✅ 可以在子区 {thread.name} 发送消息")
                        else:
                            print(f"  ❌ 无法在子区 {thread.name} 发送消息")
                else:
                    print(f"❌ 无法在 #{channel.name} 发送消息")
                    
            except Exception as e:
                print(f"⚠️ 检查频道 #{channel.name} 时出错: {e}")
        
        print("\n🔍 权限检查完成")
        await bot.close()
    
    try:
        await bot.start(config['token'])
    except discord.LoginFailure:
        print("❌ Token无效，请检查config.json中的token")
    except Exception as e:
        print(f"❌ 连接失败: {e}")

def main():
    """主函数"""
    print("🚀 机器人权限检查工具")
    print("=" * 40)
    
    try:
        asyncio.run(check_permissions())
    except KeyboardInterrupt:
        print("\n⏹️ 检查已取消")
    
    print("\n💡 如果权限检查通过，你就可以在任何频道和子区使用匿名反馈功能了！")
    print("📋 匿名反馈系统需要论坛频道支持，请确保服务器中有论坛频道")
    print("🔧 使用 get_new_server_info.py 获取完整的服务器配置建议")
    print("✅ 使用 config_validator.py 验证配置文件的正确性")

if __name__ == "__main__":
    main() 