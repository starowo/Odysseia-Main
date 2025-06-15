#!/usr/bin/env python3
"""
测试匿名反馈系统的增强功能
运行前请确保机器人已配置并运行
"""

import asyncio
import sys
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

# 添加src目录到路径
sys.path.append(str(Path(__file__).parent / "src"))

from src.anonymous_feedback.cog import AnonymousFeedbackCog

class MockBot:
    """模拟bot对象"""
    def __init__(self):
        self.logger = None
    
    def get_guild(self, guild_id):
        print(f"模拟获取服务器 ID: {guild_id}")
        return MockGuild()

class MockGuild:
    """模拟服务器对象"""
    def __init__(self):
        self.id = 12345
        self.text_channels = [MockTextChannel()]
    
    def get_channel(self, channel_id):
        if channel_id == 999:
            return MockTextChannel()
        return None
    
    def get_thread(self, thread_id):
        if thread_id == 123:
            return MockThread()
        return None

class MockTextChannel:
    """模拟文字频道对象"""
    def __init__(self):
        self.id = 999
        self.threads = [MockThread()]
    
    async def archived_threads(self, limit=None):
        """模拟已归档的子区"""
        yield MockThread(archived=True)

class MockThread:
    """模拟论坛帖子对象"""
    def __init__(self, archived=False):
        self.id = 123
        self.archived = archived
        self.owner_id = 987654321  # 模拟帖主ID

async def test_enhanced_feedback_system():
    """测试增强的匿名反馈系统"""
    print("🧪 测试增强匿名反馈系统")
    
    mock_bot = MockBot()
    cog = AnonymousFeedbackCog(mock_bot)
    
    # 测试数据库初始化
    print("\n📊 测试数据库结构")
    db_path = cog.db_path
    
    if db_path.exists():
        with sqlite3.connect(db_path) as conn:
            # 检查所有必要的表
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            table_names = [table[0] for table in tables]
            
            required_tables = [
                'users', 'feedback', 'guild_sequences', 'downvote_records',
                'warning_records', 'author_warnings', 'trace_records',
                'author_global_bans', 'author_anonymous_disabled'
            ]
            
            for table in required_tables:
                if table in table_names:
                    print(f"✅ 表 {table} 存在")
                else:
                    print(f"❌ 表 {table} 缺失")
    else:
        print("⚠️ 数据库文件不存在，将在首次使用时创建")

def test_url_parsing():
    """测试URL解析功能"""
    print("\n🧪 测试Discord链接解析")
    
    mock_bot = MockBot()
    cog = AnonymousFeedbackCog(mock_bot)
    
    # 测试有效链接
    test_urls = [
        "https://discord.com/channels/123456789/987654321/555666777",
        "https://discord.com/channels/111/222/333",
    ]
    
    for url in test_urls:
        result = cog._parse_discord_url(url)
        if result:
            guild_id, channel_id, message_id = result
            print(f"✅ 解析成功: 服务器{guild_id}, 频道{channel_id}, 消息{message_id}")
        else:
            print(f"❌ 解析失败: {url}")
    
    # 测试无效链接
    invalid_urls = [
        "https://example.com",
        "not_a_url",
        "https://discord.com/channels/abc/def/ghi"
    ]
    
    for url in invalid_urls:
        result = cog._parse_discord_url(url)
        if result:
            print(f"⚠️ 无效链接意外解析成功: {url}")
        else:
            print(f"✅ 正确拒绝无效链接: {url}")

def test_user_permissions():
    """测试用户权限检查"""
    print("\n🧪 测试用户权限系统")
    
    mock_bot = MockBot()
    cog = AnonymousFeedbackCog(mock_bot)
    
    # 模拟用户cookie
    test_cookie = cog._get_user_cookie(123456, 12345)
    print(f"✅ 生成用户cookie: {test_cookie[:8]}...")
    
    # 测试权限检查
    is_allowed, error_msg = cog._check_user_permissions(test_cookie, 123, 12345)
    if is_allowed:
        print("✅ 用户权限检查通过")
    else:
        print(f"⚠️ 用户权限检查失败: {error_msg}")

def test_file_validation():
    """测试文件验证功能"""
    print("\n🧪 测试文件验证系统")
    
    mock_bot = MockBot()
    cog = AnonymousFeedbackCog(mock_bot)
    
    # 模拟文件对象
    class MockAttachment:
        def __init__(self, filename, size, content_type="image/png"):
            self.filename = filename
            self.size = size
            self.content_type = content_type
    
    # 测试图片文件
    test_files = [
        ("test.png", 1024*1024, "image/png", "image"),  # 1MB PNG
        ("test.jpg", 30*1024*1024, "image/jpeg", "image"),  # 30MB JPG (超大)
        ("test.pdf", 5*1024*1024, "application/pdf", "file"),  # 5MB PDF
        ("test.exe", 1024, "application/exe", "file"),  # 不支持的格式
    ]
    
    for filename, size, content_type, expected_type in test_files:
        mock_file = MockAttachment(filename, size, content_type)
        is_valid, error_msg = cog._validate_file(mock_file, expected_type)
        
        if is_valid:
            print(f"✅ 文件验证通过: {filename}")
        else:
            print(f"❌ 文件验证失败: {filename} - {error_msg}")

async def test_thread_detection():
    """测试论坛帖子检测"""
    print("\n🧪 测试论坛帖子检测")
    
    mock_bot = MockBot()
    cog = AnonymousFeedbackCog(mock_bot)
    
    # 测试获取线程
    thread = await cog._get_thread_by_id(12345, 123)
    if thread:
        print(f"✅ 成功获取论坛帖子，ID: {thread.id}, 帖主: {thread.owner_id}")
    else:
        print("❌ 无法获取论坛帖子")

def test_command_structure():
    """测试命令结构"""
    print("\n🧪 测试命令结构")
    
    mock_bot = MockBot()
    cog = AnonymousFeedbackCog(mock_bot)
    
    # 检查命令组
    print(f"✅ 用户命令组: {cog.feedback.name}")
    print(f"✅ 帖主命令组: {cog.author_feedback.name}")
    print(f"✅ 管理员命令组: {cog.admin_feedback.name}")
    
    # 检查是否有统一的发送命令
    commands = [cmd for cmd in cog.feedback.commands]
    command_names = [cmd.name for cmd in commands]
    
    expected_commands = ["发送", "查询溯源记录", "删除反馈"]
    for cmd_name in expected_commands:
        if cmd_name in command_names:
            print(f"✅ 用户命令存在: {cmd_name}")
        else:
            print(f"❌ 用户命令缺失: {cmd_name}")

async def main():
    """主测试函数"""
    print("🚀 开始测试增强匿名反馈系统")
    print("=" * 60)
    
    # 测试URL解析
    test_url_parsing()
    
    # 测试数据库和系统
    await test_enhanced_feedback_system()
    
    # 测试用户权限
    test_user_permissions()
    
    # 测试文件验证
    test_file_validation()
    
    # 测试线程检测
    await test_thread_detection()
    
    # 测试命令结构
    test_command_structure()
    
    print("\n" + "=" * 60)
    print("📝 测试总结：")
    print("✨ 新功能特性：")
    print("  - 统一发送命令（支持多图片+多文件）")
    print("  - 图片直接显示技术")
    print("  - 按帖主独立警告系统")
    print("  - 帖主全局管理功能")
    print("  - 用户溯源记录查询")
    print("  - 用户自主删除反馈")
    print("  - 6个踩自动删除机制")
    print("\n🔧 技术改进：")
    print("  - aiohttp异步文件下载")
    print("  - Discord时间戳格式")
    print("  - 9个数据库表结构")
    print("  - 统一命令命名规范")
    print("  - 增强错误处理机制")
    print("\n💡 如果所有测试都通过，说明增强匿名反馈系统功能正常")

if __name__ == "__main__":
    asyncio.run(main()) 