#!/usr/bin/env python3
"""
配置文件验证器
验证多服务器配置的完整性和正确性
"""

import json
from pathlib import Path

def validate_config():
    """验证配置文件"""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
    except FileNotFoundError:
        print("❌ 配置文件 config.json 不存在")
        return False
    except json.JSONDecodeError as e:
        print(f"❌ 配置文件格式错误: {e}")
        return False
    
    print("🔍 验证配置文件...")
    
    # 验证基本配置
    required_fields = ['token', 'cogs', 'guild_configs']
    for field in required_fields:
        if field not in config:
            print(f"❌ 缺少必需字段: {field}")
            return False
    
    # 验证Token
    token = config.get('token', '')
    if not token or token == "在此填入你的Discord Token":
        print("❌ 未设置有效的Discord Token")
        return False
    
    print("✅ 基本配置验证通过")
    
    # 验证服务器配置
    guild_configs = config.get('guild_configs', {})
    if not guild_configs:
        print("⚠️ 未配置任何服务器")
        return True
    
    print(f"\n📋 已配置服务器数量: {len(guild_configs)}")
    
    for guild_id, guild_config in guild_configs.items():
        print(f"\n🏠 验证服务器: {guild_config.get('name', guild_id)}")
        
        # 验证管理员配置
        admins = guild_config.get('admins', [])
        if not admins:
            print(f"  ⚠️ 未配置管理员")
        else:
            print(f"  ✅ 管理员数量: {len(admins)}")
        
        # 验证角色配置
        role_configs = [
            ('quiz_role_id', '答题验证角色'),
            ('warned_role_id', '警告角色'),
            ('verified_role_id', '验证角色'),
            ('buffer_role_id', '缓冲角色')
        ]
        
        for role_key, role_name in role_configs:
            role_id = guild_config.get(role_key)
            if role_id and str(role_id) != "123456789":
                print(f"  ✅ {role_name}: {role_id}")
            else:
                print(f"  ⚠️ {role_name}: 未配置或使用示例ID")
        
        # 验证频道配置
        channel_id = guild_config.get('punish_announce_channel_id')
        if channel_id and str(channel_id) != "123456789":
            print(f"  ✅ 处罚公示频道: {channel_id}")
        else:
            print(f"  ⚠️ 处罚公示频道: 未配置或使用示例ID")
        
        # 验证日志配置
        logging_config = guild_config.get('logging', {})
        if logging_config.get('enabled', False):
            log_channel = logging_config.get('channel_id')
            if log_channel:
                print(f"  ✅ 日志频道: {log_channel}")
            else:
                print(f"  ❌ 日志已启用但未设置频道ID")
        else:
            print(f"  ⚠️ 日志功能未启用")
    
    # 验证Cog配置
    print(f"\n🔧 Cog配置验证:")
    cogs_config = config.get('cogs', {})
    available_cogs = [
        'thread_manage',
        'admin', 
        'anonymous_feedback',
        'verify',
        'misc',
        'event'
    ]
    
    for cog_name in available_cogs:
        cog_config = cogs_config.get(cog_name, {})
        enabled = cog_config.get('enabled', False)
        print(f"  {'✅' if enabled else '❌'} {cog_name}: {'启用' if enabled else '禁用'}")
    
    print(f"\n🎉 配置验证完成!")
    return True

def check_auxiliary_configs():
    """检查辅助配置文件"""
    print(f"\n📁 检查辅助配置文件:")
    
    # 验证验证模块配置
    verify_config_path = Path('config/verify/config.json')
    if verify_config_path.exists():
        print(f"  ✅ 验证模块配置: {verify_config_path}")
    else:
        print(f"  ❌ 验证模块配置: {verify_config_path} 不存在")
    
    verify_questions_path = Path('config/verify/questions.json')
    if verify_questions_path.exists():
        print(f"  ✅ 验证题目配置: {verify_questions_path}")
    else:
        print(f"  ❌ 验证题目配置: {verify_questions_path} 不存在")
    
    # 检查数据目录
    data_dir = Path('data')
    if data_dir.exists():
        print(f"  ✅ 数据目录: {data_dir}")
    else:
        print(f"  ⚠️ 数据目录: {data_dir} 不存在，将自动创建")
    
    logs_dir = Path('logs')
    if logs_dir.exists():
        print(f"  ✅ 日志目录: {logs_dir}")
    else:
        print(f"  ⚠️ 日志目录: {logs_dir} 不存在，将自动创建")

def main():
    """主函数"""
    print("🚀 Odysseia Bot 配置验证器")
    print("=" * 50)
    
    if validate_config():
        check_auxiliary_configs()
        print(f"\n✨ 验证完成，配置文件可用!")
    else:
        print(f"\n💥 验证失败，请修复配置文件后重试")

if __name__ == '__main__':
    main() 