"""
热重载功能测试
测试新的扩展系统是否正常工作
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from discord.ext import commands
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class TestHotReload:
    """热重载功能测试类"""
    
    @pytest.fixture(autouse=True)
    def setup(self, mock_bot, mock_config):
        """设置测试环境"""
        self.mock_bot = mock_bot
        self.mock_config = mock_config
        
        # 模拟bot的配置和cog_manager
        self.mock_bot.config = mock_config
        
    @pytest.mark.asyncio
    async def test_cog_manager_initialization(self, temp_config_file):
        """测试CogManager初始化"""
        with patch('main.CONFIG', self.mock_config):
            from main import CogManager
            
            cog_manager = CogManager(self.mock_bot, self.mock_config)
            
            # 验证模块路径映射
            expected_paths = {
                "thread_manage": "src.thread_manage.cog",
                "bot_manage": "src.bot_manage.cog",
                "admin": "src.admin.cog",
                "verify": "src.verify.cog"
            }
            
            assert cog_manager.cog_module_paths == expected_paths
            assert cog_manager.bot == self.mock_bot
            assert cog_manager.config == self.mock_config
    
    @pytest.mark.asyncio
    async def test_load_extension_success(self, temp_config_file):
        """测试成功加载扩展"""
        with patch('main.CONFIG', self.mock_config):
            from main import CogManager
            
            cog_manager = CogManager(self.mock_bot, self.mock_config)
            
            # 测试加载扩展
            success, message = await cog_manager.load_extension(
                "src.bot_manage.cog", "bot_manage"
            )
            
            assert success is True
            assert "已加载" in message
            self.mock_bot.load_extension.assert_called_once_with("src.bot_manage.cog")
    
    @pytest.mark.asyncio
    async def test_load_extension_already_loaded(self, temp_config_file):
        """测试加载已经加载的扩展"""
        with patch('main.CONFIG', self.mock_config):
            from main import CogManager
            
            cog_manager = CogManager(self.mock_bot, self.mock_config)
            
            # 模拟ExtensionAlreadyLoaded异常
            self.mock_bot.load_extension.side_effect = commands.ExtensionAlreadyLoaded("test")
            
            success, message = await cog_manager.load_extension(
                "src.bot_manage.cog", "bot_manage"
            )
            
            assert success is True
            assert "已经处于启用状态" in message
    
    @pytest.mark.asyncio
    async def test_load_extension_not_found(self, temp_config_file):
        """测试加载不存在的扩展"""
        with patch('main.CONFIG', self.mock_config):
            from main import CogManager
            
            cog_manager = CogManager(self.mock_bot, self.mock_config)
            
            # 模拟ExtensionNotFound异常
            self.mock_bot.load_extension.side_effect = commands.ExtensionNotFound("test")
            
            success, message = await cog_manager.load_extension(
                "nonexistent.module", "test"
            )
            
            assert success is False
            assert "模块路径未找到" in message
    
    @pytest.mark.asyncio
    async def test_unload_extension_success(self, temp_config_file):
        """测试成功卸载扩展"""
        with patch('main.CONFIG', self.mock_config):
            from main import CogManager
            
            cog_manager = CogManager(self.mock_bot, self.mock_config)
            
            success, message = await cog_manager.unload_extension(
                "src.bot_manage.cog", "bot_manage"
            )
            
            assert success is True
            assert "已卸载" in message
            self.mock_bot.unload_extension.assert_called_once_with("src.bot_manage.cog")
    
    @pytest.mark.asyncio
    async def test_unload_extension_not_loaded(self, temp_config_file):
        """测试卸载未加载的扩展"""
        with patch('main.CONFIG', self.mock_config):
            from main import CogManager
            
            cog_manager = CogManager(self.mock_bot, self.mock_config)
            
            # 模拟ExtensionNotLoaded异常
            self.mock_bot.unload_extension.side_effect = commands.ExtensionNotLoaded("test")
            
            success, message = await cog_manager.unload_extension(
                "src.bot_manage.cog", "bot_manage"
            )
            
            assert success is True
            assert "已经处于禁用状态" in message
    
    @pytest.mark.asyncio
    async def test_reload_extension_success(self, temp_config_file):
        """测试成功重载扩展"""
        with patch('main.CONFIG', self.mock_config):
            from main import CogManager
            
            cog_manager = CogManager(self.mock_bot, self.mock_config)
            
            success, message = await cog_manager.reload_extension(
                "src.bot_manage.cog", "bot_manage"
            )
            
            assert success is True
            assert "已重载" in message
            self.mock_bot.reload_extension.assert_called_once_with("src.bot_manage.cog")
    
    @pytest.mark.asyncio
    async def test_reload_extension_not_loaded(self, temp_config_file):
        """测试重载未加载的扩展（应该尝试加载）"""
        with patch('main.CONFIG', self.mock_config):
            from main import CogManager
            
            cog_manager = CogManager(self.mock_bot, self.mock_config)
            
            # 模拟ExtensionNotLoaded异常，然后成功加载
            self.mock_bot.reload_extension.side_effect = commands.ExtensionNotLoaded("test")
            
            # 重置load_extension的side_effect
            async def mock_load_extension(name):
                self.mock_bot.extensions[name] = MagicMock()
            
            self.mock_bot.load_extension.side_effect = mock_load_extension
            
            success, message = await cog_manager.reload_extension(
                "src.bot_manage.cog", "bot_manage"
            )
            
            assert success is True
            assert "已加载" in message
    
    @pytest.mark.asyncio
    async def test_load_all_enabled(self, temp_config_file):
        """测试加载所有启用的模块"""
        with patch('main.CONFIG', self.mock_config):
            from main import CogManager
            
            cog_manager = CogManager(self.mock_bot, self.mock_config)
            
            # 模拟load_extension方法
            cog_manager.load_extension = AsyncMock(return_value=(True, "成功"))
            
            await cog_manager.load_all_enabled()
            
            # 验证调用了正确的模块
            expected_calls = [
                ("src.thread_manage.cog", "thread_manage"),
                ("src.bot_manage.cog", "bot_manage"),
                ("src.admin.cog", "admin")
                # verify模块在配置中是disabled的，不应该被加载
            ]
            
            assert cog_manager.load_extension.call_count == 3
            for call_args in cog_manager.load_extension.call_args_list:
                assert call_args[0] in expected_calls
