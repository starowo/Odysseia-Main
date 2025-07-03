#!/usr/bin/env python3
"""
配置文件验证器 v3.0
验证单服务器配置的完整性和正确性
支持新的三次警告制和简化架构
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
        print("💡 请运行 'python 快速部署.py' 或复制 config.example.json 为 config.json")
        return False
    except json.JSONDecodeError as e:
        print(f"❌ 配置文件格式错误: {e}")
        return False
    
    print("🔍 验证配置文件...")
    
    # 验证基本配置
    required_fields = ['token', 'cogs', 'admins']
    for field in required_fields:
        if field not in config:
            print(f"❌ 缺少必需字段: {field}")
            return False
    
    # 验证Token
    token = config.get('token', '')
    if not token or token == "你的机器人Token_从Discord开发者门户获取":
        print("❌ 未设置有效的Discord Token")
        print("💡 请在Discord开发者门户获取Token并填入配置文件")
        return False
    
    print("✅ 基本配置验证通过")
    
    # 验证管理员配置（v3.0新特性：支持身份组ID）
    admins = config.get('admins', [])
    if not admins:
        print("❌ 未配置管理员")
        print("💡 请在admins字段中添加管理员身份组ID")
        return False
    else:
        print(f"✅ 管理员数量: {len(admins)}")
        print("   📝 注意：现在使用身份组ID进行权限检查")
    
    # 验证身份组配置
    print(f"\n🎭 身份组配置验证:")
    role_configs = [
        ('verified_role_id', '验证身份组'),
        ('buffer_role_id', '缓冲身份组'),
        ('quiz_role_id', '答题身份组'),
        ('warned_role_id', '警告身份组')
    ]
    
    for role_key, role_name in role_configs:
        role_id = config.get(role_key)
        if role_id and str(role_id) != "123456789012345678" and role_id != 0:
            print(f"  ✅ {role_name}: {role_id}")
        else:
            print(f"  ⚠️ {role_name}: 未配置或使用示例ID")
    
    # 验证频道配置
    print(f"\n📺 频道配置验证:")
    channel_id = config.get('punish_announce_channel_id')
    if channel_id and str(channel_id) != "123456789012345683" and channel_id != 0:
        print(f"  ✅ 处罚公示频道: {channel_id}")
    else:
        print(f"  ⚠️ 处罚公示频道: 未配置或使用示例ID")
    
    # 验证日志配置
    print(f"\n📋 日志配置验证:")
    logging_config = config.get('logging', {})
    if logging_config.get('enabled', False):
        log_channel = logging_config.get('channel_id')
        guild_id = logging_config.get('guild_id')
        if log_channel and guild_id:
            print(f"  ✅ 日志频道: {log_channel}")
            print(f"  ✅ 服务器ID: {guild_id}")
            print(f"  ✅ 日志级别: {logging_config.get('level', 'INFO')}")
        else:
            print(f"  ❌ 日志已启用但缺少必要配置")
    else:
        print(f"  ⚠️ 日志功能未启用")
    
    # 验证赛事管理配置（v3.0必填项）
    print(f"\n🏆 赛事管理配置验证:")
    event_managers = config.get('event_managers', [])
    highest_role = config.get('highest_role_available')
    
    if 'event_managers' not in config:
        print(f"  ❌ 缺少必填字段: event_managers")
        return False
    elif event_managers:
        print(f"  ✅ 赛事管理员数量: {len(event_managers)}")
    else:
        print(f"  ⚠️ 赛事管理员列表为空")
    
    if 'highest_role_available' not in config:
        print(f"  ❌ 缺少必填字段: highest_role_available")
        return False
    elif highest_role == 0:
        print(f"  ✅ 最高可管理身份组: 无限制 (0)")
    elif highest_role:
        print(f"  ✅ 最高可管理身份组: {highest_role}")
    else:
        print(f"  ⚠️ 最高可管理身份组未设置")
    
    # 验证Cog配置
    print(f"\n🔧 功能模块配置验证:")
    cogs_config = config.get('cogs', {})
    available_cogs = [
        ('thread_manage', '子区自助管理'),
        ('admin', '管理员功能'), 
        ('anonymous_feedback', '匿名反馈系统'),
        ('verify', '验证系统'),
        ('misc', '杂项功能'),
        ('event', '赛事管理'),
        ('bot_manage', '机器人管理'),
        ('sync', '服务器同步')
    ]
    
    for cog_name, cog_desc in available_cogs:
        cog_config = cogs_config.get(cog_name, {})
        enabled = cog_config.get('enabled', False)
        status = '✅ 启用' if enabled else '❌ 禁用'
        print(f"  {status} {cog_name}: {cog_desc}")
    
    # 检查匿名反馈系统配置
    anonymous_enabled = cogs_config.get('anonymous_feedback', {}).get('enabled', False)
    if anonymous_enabled:
        print(f"\n📫 匿名反馈系统详情:")
        print(f"  ✅ 自动化系统，无需额外配置")
        print(f"  ✅ 支持三次警告制")
        print(f"  ✅ 支持帖主管理功能")
        print(f"  ✅ 支持多媒体反馈（文字/图片/文件）")
    
    print(f"\n🎉 配置验证完成!")
    return True

def check_auxiliary_configs():
    """检查辅助配置文件"""
    print(f"\n📁 检查辅助配置文件:")
    
    # 验证验证模块配置
    verify_config_path = Path('config/verify/config.json')
    if verify_config_path.exists():
        print(f"  ✅ 验证模块配置: {verify_config_path}")
        try:
            with open(verify_config_path, 'r', encoding='utf-8') as f:
                verify_config = json.load(f)
                if 'questions' in verify_config:
                    print(f"    📝 验证题目数量: {len(verify_config['questions'])}")
                if 'messages' in verify_config:
                    print(f"    💬 消息模板配置: ✅")
        except Exception as e:
            print(f"    ⚠️ 验证配置读取失败: {e}")
    else:
        print(f"  ❌ 验证模块配置: {verify_config_path} 不存在")
        print(f"    💡 验证系统需要此配置文件才能正常工作")
    
    # 检查数据目录
    data_dir = Path('data')
    if data_dir.exists():
        print(f"  ✅ 数据目录: {data_dir}")
        # 检查匿名反馈数据库
        anon_db = data_dir / 'anonymous_feedback.db'
        if anon_db.exists():
            print(f"    📊 匿名反馈数据库: 已存在")
        else:
            print(f"    📊 匿名反馈数据库: 将自动创建")
    else:
        print(f"  ⚠️ 数据目录: {data_dir} 不存在，将自动创建")
    
    logs_dir = Path('logs')
    if logs_dir.exists():
        print(f"  ✅ 日志目录: {logs_dir}")
    else:
        print(f"  ⚠️ 日志目录: {logs_dir} 不存在，将自动创建")
    
    # 检查多服务器配置是否存在（应该已删除）
    old_config_dir = Path('config/event')
    if old_config_dir.exists():
        print(f"  ⚠️ 发现旧版多服务器配置目录: {old_config_dir}")
        print(f"    💡 v3.0已简化为单服务器架构，可以删除此目录")

def show_migration_tips():
    """显示升级提示"""
    print(f"\n🔄 v3.0 升级说明:")
    print(f"  📋 主要变更:")
    print(f"    • admins字段现在支持身份组ID")
    print(f"    • event_managers和highest_role_available为必填项")
    print(f"    • 删除多服务器架构，简化配置")
    print(f"    • 匿名反馈系统新增三次警告制")
    print(f"    • 新增帖主管理功能")
    print(f"    • 修复批量删除消息和一键删帖功能")
    
    print(f"\n  🛠️ 配置建议:")
    print(f"    • 使用身份组ID而非用户ID配置管理员")
    print(f"    • highest_role_available设为0表示无身份组限制")
    print(f"    • 启用anonymous_feedback体验新的三次警告制")
    print(f"    • 在论坛频道测试匿名反馈功能")

def main():
    """主函数"""
    print("🚀 Odysseia Bot 配置验证器 v3.0")
    print("支持新的单服务器架构和三次警告制")
    print("=" * 60)
    
    if validate_config():
        check_auxiliary_configs()
        show_migration_tips()
        print(f"\n✨ 验证完成，配置文件可用!")
        print(f"🎯 运行 'python main.py' 启动机器人")
    else:
        print(f"\n💥 验证失败，请修复配置文件后重试")
        print(f"💡 可运行 'python 快速部署.py' 重新配置")

if __name__ == '__main__':
    main() 