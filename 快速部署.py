#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Odysseia Discord Bot 快速部署脚本
适用于零代码基础的用户快速配置机器人
"""

import json
import os
import sys
from pathlib import Path

def print_banner():
    """显示欢迎横幅"""
    print("=" * 60)
    print("🚀 Odysseia Discord Bot 快速部署工具")
    print("=" * 60)
    print("本工具将帮助您快速配置机器人")
    print("请按照提示逐步输入相关信息")
    print("-" * 60)

def print_step(step_num, title):
    """显示步骤标题"""
    print(f"\n📋 步骤 {step_num}: {title}")
    print("-" * 40)

def get_input(prompt, required=True, input_type=str, default=None):
    """获取用户输入并验证"""
    while True:
        if default:
            user_input = input(f"{prompt} (默认: {default}): ").strip()
            if not user_input:
                user_input = str(default)
        else:
            user_input = input(f"{prompt}: ").strip()
        
        if not user_input and required:
            print("❌ 此项为必填项，请重新输入")
            continue
        
        if not user_input and not required:
            return None
            
        # 类型转换
        try:
            if input_type == int:
                return int(user_input)
            elif input_type == bool:
                return user_input.lower() in ['y', 'yes', 'true', '1', '是']
            else:
                return user_input
        except ValueError:
            print(f"❌ 输入格式错误，请输入有效的{input_type.__name__}")
            continue

def get_multiple_ids(prompt):
    """获取多个ID输入"""
    print(f"{prompt}")
    print("💡 提示：多个ID请用逗号分隔，例如：123456789,987654321")
    user_input = input("请输入: ").strip()
    
    if not user_input:
        return []
    
    try:
        ids = [int(id_str.strip()) for id_str in user_input.split(',') if id_str.strip()]
        return ids
    except ValueError:
        print("❌ ID格式错误，请输入有效的数字ID")
        return get_multiple_ids(prompt)

def validate_token(token):
    """验证Discord Token格式"""
    if not token:
        return False
    # 基础格式检查
    parts = token.split('.')
    return len(parts) == 3 and len(parts[0]) > 0

def create_basic_config():
    """创建基础配置"""
    config = {
        "token": "",
        "prefix": "!",
        "status": "watching",
        "status_text": "多服务器管理中",
        "admins": [],
        "cogs": {
            "thread_manage": {"enabled": True},
            "admin": {"enabled": True},
            "anonymous_feedback": {"enabled": True},
            "verify": {"enabled": True},
            "misc": {"enabled": True},
            "event": {"enabled": True},
            "bot_manage": {"enabled": True}
        },
        "guild_configs": {}
    }
    return config

def setup_bot_basic_info(config):
    """设置机器人基础信息"""
    print_step(1, "机器人基础配置")
    
    # 获取Token
    print("🔑 首先需要您的机器人Token")
    print("💡 获取方法：Discord开发者门户 → 应用 → Bot → Token → Copy")
    print("⚠️  请确保Token的安全，不要泄露给他人")
    
    while True:
        token = get_input("请输入机器人Token")
        if validate_token(token):
            config["token"] = token
            break
        else:
            print("❌ Token格式似乎不正确，请检查后重新输入")
    
    # 设置命令前缀
    prefix = get_input("设置命令前缀", default="!")
    config["prefix"] = prefix
    
    # 设置机器人状态
    print("\n🎮 机器人状态设置")
    status_options = {
        "1": ("playing", "正在玩"),
        "2": ("watching", "正在观看"),
        "3": ("listening", "正在听")
    }
    
    print("选择机器人状态类型：")
    for key, (_, desc) in status_options.items():
        print(f"  {key}. {desc}")
    
    status_choice = get_input("请选择状态类型 (1-3)", default="2")
    if status_choice in status_options:
        config["status"] = status_options[status_choice][0]
    
    status_text = get_input("设置状态文字", default="多服务器管理中")
    config["status_text"] = status_text
    
    print("✅ 机器人基础信息配置完成")

def setup_global_admins(config):
    """设置全局管理员"""
    print_step(2, "全局管理员配置")
    
    print("👑 全局管理员拥有所有服务器的最高权限")
    print("💡 获取用户ID：右键用户头像 → 复制用户ID（需开启开发者模式）")
    
    admin_ids = get_multiple_ids("请输入全局管理员的用户ID")
    config["admins"] = admin_ids
    
    if admin_ids:
        print(f"✅ 已设置 {len(admin_ids)} 个全局管理员")
    else:
        print("⚠️  未设置全局管理员，后续可在配置文件中手动添加")

def setup_guild_config(config):
    """设置服务器配置"""
    print_step(3, "服务器配置")
    
    print("🏰 现在配置机器人将要管理的服务器")
    print("💡 可以配置多个服务器，每个服务器独立管理")
    
    while True:
        # 获取服务器基础信息
        print("\n" + "="*30)
        guild_id = get_input("请输入服务器ID", input_type=int)
        guild_name = get_input("请输入服务器名称（便于识别）")
        
        guild_config = {
            "name": guild_name,
            "admins": [],
            "verified_role_id": 0,
            "buffer_role_id": 0,
            "quiz_role_id": 0,
            "warned_role_id": 0,
            "punish_announce_channel_id": 0,
            "logging": {
                "enabled": True,
                "channel_id": 0
            }
        }
        
        # 服务器管理员
        print(f"\n👮 {guild_name} 的管理员配置")
        print("💡 这里需要输入管理员身份组ID，拥有这些身份组的用户将获得管理权限")
        server_admins = get_multiple_ids("请输入该服务器的管理员身份组ID")
        guild_config["admins"] = server_admins
        
        # 身份组配置
        print(f"\n🎭 {guild_name} 的身份组配置")
        print("💡 这些身份组需要在Discord服务器中先创建好")
        
        role_configs = [
            ("verified_role_id", "已验证用户身份组ID"),
            ("buffer_role_id", "验证缓冲身份组ID"),
            ("quiz_role_id", "答题验证身份组ID"),
            ("warned_role_id", "警告状态身份组ID")
        ]
        
        for role_key, role_desc in role_configs:
            role_id = get_input(f"请输入{role_desc}", input_type=int, required=False)
            if role_id:
                guild_config[role_key] = role_id
        
        # 频道配置
        print(f"\n📺 {guild_name} 的频道配置")
        print("💡 这些频道需要在Discord服务器中先创建好")
        
        punish_channel = get_input("请输入处罚公示频道ID", input_type=int, required=False)
        if punish_channel:
            guild_config["punish_announce_channel_id"] = punish_channel
        
        log_channel = get_input("请输入机器人日志频道ID", input_type=int, required=False)
        if log_channel:
            guild_config["logging"]["channel_id"] = log_channel
        
        # 赛事管理配置（可选）
        print(f"\n🏆 {guild_name} 的赛事管理配置")
        enable_event = get_input("是否配置赛事管理？(y/n)", input_type=bool, default=False)
        if enable_event:
            event_managers = get_multiple_ids("请输入赛事管理员用户ID（可选）")
            highest_role = get_input("请输入最高可管理身份组ID（可选）", input_type=int, required=False)
            
            if event_managers:
                guild_config["event_managers"] = event_managers
            if highest_role:
                guild_config["highest_role_available"] = highest_role
        
        # 保存服务器配置
        config["guild_configs"][str(guild_id)] = guild_config
        print(f"✅ 服务器 {guild_name} 配置完成")
        
        # 询问是否继续添加服务器
        add_more = get_input("是否需要配置更多服务器？(y/n)", input_type=bool, default=False)
        if not add_more:
            break

def setup_module_config(config):
    """设置功能模块配置"""
    print_step(4, "功能模块配置")
    
    print("🧩 选择要启用的功能模块")
    print("💡 您可以根据需要启用或禁用特定功能")
    
    modules = {
        "thread_manage": "子区管理功能",
        "admin": "管理员命令",
        "anonymous_feedback": "匿名反馈系统（论坛专用，自动化无需配置）",
        "verify": "验证系统",
        "misc": "杂项命令",
        "event": "赛事管理",
        "bot_manage": "机器人管理命令"
    }
    
    for module_key, module_desc in modules.items():
        enabled = get_input(f"是否启用 {module_desc}？(y/n)", input_type=bool, default=True)
        config["cogs"][module_key]["enabled"] = enabled
    
    print("\n💡 功能说明：")
    print("📢 匿名反馈系统：论坛频道专用，用户可在帖子内发送匿名消息，无需额外配置")
    print("🔧 子区管理：支持帖主和管理员管理论坛帖子")
    print("🛡️  验证系统：新用户入群验证功能")
    print("🎮 赛事管理：身份组发放和赛事相关功能")
    print("⚙️  机器人管理：运行时动态管理模块开关")
    
    print("✅ 功能模块配置完成")

def save_config(config):
    """保存配置文件"""
    print_step(5, "保存配置")
    
    config_path = Path("config.json")
    
    # 备份已存在的配置文件
    if config_path.exists():
        backup_path = Path("config.backup.json")
        print(f"📦 发现已存在的配置文件，备份到 {backup_path}")
        config_path.rename(backup_path)
    
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        print(f"✅ 配置文件已保存到 {config_path}")
        return True
    except Exception as e:
        print(f"❌ 保存配置文件失败: {e}")
        return False

def show_next_steps():
    """显示后续步骤"""
    print("\n" + "=" * 60)
    print("🎉 配置完成！后续步骤：")
    print("=" * 60)
    
    steps = [
        "1. 检查 config.json 文件确保配置正确",
        "2. 在Discord服务器中创建必要的频道和身份组",
        "3. 运行命令启动机器人：python main.py",
        "4. 在Discord中测试机器人功能",
        "5. 如遇问题请查看部署指南或日志文件"
    ]
    
    for step in steps:
        print(f"📋 {step}")
    
    print("\n💡 特别提醒：")
    print("�� 匿名反馈系统只在论坛频道的帖子内可用，完全自动化")
    print("🔸 权限配置：全局管理员使用用户ID，服务器管理员使用身份组ID")
    print("🔸 验证系统完全自动化，只需在Discord服务器中创建相关身份组")
    print("🔸 赛事管理为可选功能，可用于身份组发放和权限控制")
    print("🔸 机器人管理命令可以在运行时动态开关功能模块")
    
    print("\n🔗 相关资源：")
    print("📚 详细部署指南：部署指南.md")
    print("🐛 问题反馈：GitHub Issues")
    print("💬 技术支持：加入官方交流群")
    
    print("\n🚀 祝您使用愉快！")

def check_requirements():
    """检查环境要求"""
    print("🔍 检查环境要求...")
    
    # 检查Python版本
    if sys.version_info < (3, 10):
        print("❌ Python版本过低，需要Python 3.10或更高版本")
        print(f"   当前版本：{sys.version}")
        return False
    
    # 检查依赖包
    try:
        import discord
        print("✅ discord.py 已安装")
    except ImportError:
        print("❌ discord.py 未安装，请运行：pip install -r requirements.txt")
        return False
    
    # 检查requirements.txt文件
    req_path = Path("requirements.txt")
    if not req_path.exists():
        print("⚠️  未找到 requirements.txt 文件")
    
    print("✅ 环境检查完成")
    return True

def main():
    """主函数"""
    print_banner()
    
    # 环境检查
    if not check_requirements():
        print("\n❌ 环境检查失败，请解决上述问题后重新运行")
        input("按回车键退出...")
        return
    
    try:
        # 创建配置
        config = create_basic_config()
        
        # 配置流程
        setup_bot_basic_info(config)
        setup_global_admins(config)
        setup_guild_config(config)
        setup_module_config(config)
        
        # 保存配置
        if save_config(config):
            show_next_steps()
        else:
            print("\n❌ 配置保存失败，请检查权限或磁盘空间")
    
    except KeyboardInterrupt:
        print("\n\n⚠️  用户取消配置")
    except Exception as e:
        print(f"\n❌ 配置过程中发生错误: {e}")
        print("请检查输入信息或联系技术支持")
    
    input("\n按回车键退出...")

if __name__ == "__main__":
    main() 