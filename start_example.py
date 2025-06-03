#!/usr/bin/env python3
"""
测试启动脚本 - 验证所有模块是否能正常加载
这个脚本不会实际连接到Discord，只是验证代码结构是否正确
"""

import json
import os
import sys
from pathlib import Path
import time

def create_test_config():
    """创建测试配置文件"""
    config = {
        "token": "测试TOKEN",
        "logging": {
            "enabled": False,
            "guild_id": 123456789,
            "channel_id": 123456789,
            "level": "INFO"
        },
        "cogs": {
            "thread_manage": {
                "enabled": True,
                "description": "子区自助管理功能"
            },
            "admin": {
                "enabled": True,
                "description": "管理员功能"
            },
            "anonymous_feedback": {
                "enabled": True,
                "description": "匿名反馈系统"
            },
            "verify": {
                "enabled": False,
                "description": "验证功能"
            },
            "misc": {
                "enabled": True,
                "description": "杂项功能"
            },
            "event": {
                "enabled": False,
                "description": "事件功能"
            }
        },
        "admins": [123456789],
        "prefix": "!",
        "status": "watching",
        "status_text": "匿名反馈系统测试",
        "quiz_role_id": 123456789,
        "punish_announce_channel_id": 123456789,
        "warned_role_id": 123456789
    }
    
    # 使用专门的测试配置文件名，避免覆盖用户配置
    with open('config_test.json', 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=4)
    
    print("✅ 测试配置文件已创建 (config_test.json)")

def test_imports():
    """测试所有模块导入"""
    try:
        print("🔍 测试模块导入...")
        
        # 测试主模块
        import src.thread_manage.cog as thread_manage
        print("  ✅ thread_manage 模块导入成功")
        
        import src.bot_manage.cog as bot_manage
        print("  ✅ bot_manage 模块导入成功")
        
        import src.admin.cog as admin
        print("  ✅ admin 模块导入成功")
        
        import src.anonymous_feedback.cog as anonymous_feedback
        print("  ✅ anonymous_feedback 模块导入成功")
        
        # 测试数据库类
        db = anonymous_feedback.AnonymousFeedbackDatabase("data/test.db")
        print("  ✅ 数据库类初始化成功")
        
        # 测试cookie生成
        cookie = db.get_user_cookie(123456789, 987654321)
        print(f"  ✅ Cookie生成测试: {cookie}")
        
        print("🎉 所有模块导入测试通过！")
        return True
        
    except Exception as e:
        print(f"❌ 模块导入失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_database():
    """测试数据库功能"""
    try:
        print("🔍 测试数据库功能...")
        
        import src.anonymous_feedback.cog as af
        
        # 创建测试数据库
        db = af.AnonymousFeedbackDatabase("data/test.db")
        
        # 测试用户注册
        cookie = db.register_user(123456789, 987654321)
        print(f"  ✅ 用户注册测试: {cookie}")
        
        # 测试反馈添加
        db_id, guild_feedback_id = db.add_feedback(
            cookie, 987654321, 
            "https://discord.com/channels/123/456/789",
            "text", "测试反馈内容"
        )
        print(f"  ✅ 反馈记录测试: 数据库ID={db_id}, 服务器内ID={guild_feedback_id}")
        
        # 测试反馈查询
        feedback_data = db.get_feedback_by_id(db_id)
        print(f"  ✅ 反馈查询测试: {feedback_data['content']}")
        
        # 测试按服务器内ID查询
        feedback_data2 = db.get_feedback_by_guild_id(987654321, guild_feedback_id)
        print(f"  ✅ 服务器内ID查询测试: {feedback_data2['content']}")
        
        # 关闭数据库连接
        del db
        
        # 稍等一下让Windows释放文件句柄
        time.sleep(0.1)
        
        # 清理测试数据库
        test_db_path = Path("data/test.db")
        if test_db_path.exists():
            try:
                test_db_path.unlink()
                print("  ✅ 测试数据库已清理")
            except:
                print("  ⚠️ 测试数据库文件清理跳过（文件可能被占用）")
        
        print("🎉 数据库功能测试通过！")
        return True
        
    except Exception as e:
        print(f"❌ 数据库测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """主测试函数"""
    print("🚀 Odysseia 匿名反馈系统集成测试")
    print("=" * 50)
    
    # 创建必要目录
    Path("data").mkdir(exist_ok=True)
    Path("logs").mkdir(exist_ok=True)
    
    # 创建测试配置
    create_test_config()
    
    # 测试模块导入
    if not test_imports():
        print("❌ 模块导入测试失败，请检查代码")
        return False
    
    print()
    
    # 测试数据库功能
    if not test_database():
        print("❌ 数据库测试失败，请检查代码")
        return False
    
    print()
    print("🎉 所有测试通过！匿名反馈系统已成功集成到Odysseia机器人")
    print("📝 要启动机器人，请：")
    print("   1. 确认 config.json 中的配置正确")
    print("   2. 运行 python main.py")
    
    # 清理测试配置（只删除测试文件，不删除用户配置）
    test_config_path = Path("config_test.json")
    if test_config_path.exists():
        test_config_path.unlink()
        print("✅ 测试配置文件已清理")
    
    return True

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⚠️ 测试被用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 测试过程中发生未预期错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1) 