#!/usr/bin/env python3
"""
测试匿名反馈系统在子区中的功能
运行前请确保机器人已配置并运行
"""

import asyncio
import sys
from pathlib import Path

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
        self.text_channels = [MockTextChannel()]
    
    def get_channel(self, channel_id):
        # 模拟普通频道ID为999，子区ID为123
        if channel_id == 999:
            return MockTextChannel()
        return None  # 子区不能通过get_channel获取

class MockTextChannel:
    """模拟文字频道对象"""
    def __init__(self):
        self.id = 999
        self.threads = [MockThread()]
    
    async def archived_threads(self, limit=None):
        """模拟已归档的子区"""
        yield MockThread(archived=True)

class MockThread:
    """模拟子区对象"""
    def __init__(self, archived=False):
        self.id = 123
        self.archived = archived

async def test_channel_detection():
    """测试频道检测功能"""
    print("🧪 测试子区检测功能")
    
    # 创建模拟的cog
    mock_bot = MockBot()
    cog = AnonymousFeedbackCog(mock_bot)
    
    # 测试普通频道
    print("\n📍 测试普通频道（ID: 999）")
    channel = await cog._get_target_channel(12345, 999)
    if channel:
        print(f"✅ 成功获取普通频道，ID: {channel.id}")
    else:
        print("❌ 无法获取普通频道")
    
    # 测试子区
    print("\n📍 测试子区（ID: 123）")
    thread = await cog._get_target_channel(12345, 123)
    if thread:
        print(f"✅ 成功获取子区，ID: {thread.id}")
    else:
        print("❌ 无法获取子区")
    
    # 测试不存在的频道
    print("\n📍 测试不存在的频道（ID: 888）")
    none_channel = await cog._get_target_channel(12345, 888)
    if none_channel:
        print(f"⚠️ 意外获取到频道: {none_channel.id}")
    else:
        print("✅ 正确返回None")
    
    print("\n🎉 测试完成！")

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

async def main():
    """主测试函数"""
    print("🚀 开始测试匿名反馈系统（子区支持）")
    print("=" * 50)
    
    # 测试URL解析
    test_url_parsing()
    
    # 测试频道检测
    await test_channel_detection()
    
    print("\n" + "=" * 50)
    print("📝 测试说明：")
    print("- 普通频道测试应该成功")
    print("- 子区测试应该成功（通过threads属性）")
    print("- 不存在的频道应该返回None")
    print("\n💡 如果所有测试都通过，说明子区支持功能正常")
    print("   现在可以重新邀请机器人（包含子区权限）并测试实际功能")

if __name__ == "__main__":
    asyncio.run(main()) 