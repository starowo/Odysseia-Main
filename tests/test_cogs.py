"""
Cog功能测试
测试所有Cog的setup函数和基本功能
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
import sys
from pathlib import Path
import json

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class TestCogSetupFunctions:
    """测试所有Cog的setup函数"""
    
    @pytest.fixture(autouse=True)
    def setup(self, mock_bot, mock_config):
        """设置测试环境"""
        self.mock_bot = mock_bot
        self.mock_config = mock_config
        self.mock_bot.config = mock_config
    
    @pytest.mark.asyncio
    async def test_bot_manage_setup(self, temp_config_file):
        """测试bot_manage模块的setup函数"""
        with patch('builtins.open', mock_open(read_data=json.dumps(self.mock_config))):
            # 导入并测试setup函数
            from src.bot_manage.cog import setup, BotManageCommands
            
            await setup(self.mock_bot)
            
            # 验证add_cog被调用
            self.mock_bot.add_cog.assert_called_once()
            
            # 验证传入的是BotManageCommands实例
            call_args = self.mock_bot.add_cog.call_args[0][0]
            assert isinstance(call_args, BotManageCommands)
    
    @pytest.mark.asyncio
    async def test_thread_manage_setup(self):
        """测试thread_manage模块的setup函数"""
        from src.thread_manage.cog import setup, ThreadSelfManage
        
        await setup(self.mock_bot)
        
        # 验证add_cog被调用
        self.mock_bot.add_cog.assert_called_once()
        
        # 验证传入的是ThreadSelfManage实例
        call_args = self.mock_bot.add_cog.call_args[0][0]
        assert isinstance(call_args, ThreadSelfManage)
    
    @pytest.mark.asyncio
    async def test_admin_setup(self):
        """测试admin模块的setup函数"""
        from src.admin.cog import setup, AdminCommands
        
        await setup(self.mock_bot)
        
        # 验证add_cog被调用
        self.mock_bot.add_cog.assert_called_once()
        
        # 验证传入的是AdminCommands实例
        call_args = self.mock_bot.add_cog.call_args[0][0]
        assert isinstance(call_args, AdminCommands)
    
    @pytest.mark.asyncio
    async def test_verify_setup(self):
        """测试verify模块的setup函数"""
        from src.verify.cog import setup, VerifyCommands
        
        await setup(self.mock_bot)
        
        # 验证add_cog被调用
        self.mock_bot.add_cog.assert_called_once()
        
        # 验证传入的是VerifyCommands实例
        call_args = self.mock_bot.add_cog.call_args[0][0]
        assert isinstance(call_args, VerifyCommands)


class TestCogInitialization:
    """测试Cog初始化"""
    
    @pytest.fixture(autouse=True)
    def setup(self, mock_bot, mock_config):
        """设置测试环境"""
        self.mock_bot = mock_bot
        self.mock_config = mock_config
        self.mock_bot.config = mock_config
        self.mock_bot.logger = MagicMock()
    
    def test_bot_manage_initialization(self, temp_config_file):
        """测试BotManageCommands初始化"""
        with patch('builtins.open', mock_open(read_data=json.dumps(self.mock_config))):
            from src.bot_manage.cog import BotManageCommands
            
            cog = BotManageCommands(self.mock_bot)
            
            assert cog.bot == self.mock_bot
            assert cog.logger == self.mock_bot.logger
            assert cog.name == "管理命令"
            assert cog.config == self.mock_config
    
    def test_thread_manage_initialization(self):
        """测试ThreadSelfManage初始化"""
        from src.thread_manage.cog import ThreadSelfManage
        
        cog = ThreadSelfManage(self.mock_bot)
        
        assert cog.bot == self.mock_bot
        assert cog.logger == self.mock_bot.logger
        assert cog.name == "自助管理"
    
    def test_admin_initialization(self):
        """测试AdminCommands初始化"""
        from src.admin.cog import AdminCommands
        
        cog = AdminCommands(self.mock_bot)
        
        assert cog.bot == self.mock_bot
        assert cog.logger == self.mock_bot.logger
        assert cog.name == "管理命令"
    
    def test_verify_initialization(self):
        """测试VerifyCommands初始化"""
        from src.verify.cog import VerifyCommands
        
        cog = VerifyCommands(self.mock_bot)
        
        assert cog.bot == self.mock_bot
        assert cog.logger == self.mock_bot.logger
        assert cog.name == "答题验证"


class TestInteractionCheck:
    """测试权限检查系统"""
    
    @pytest.fixture(autouse=True)
    def setup(self, mock_bot, mock_config, mock_interaction):
        """设置测试环境"""
        self.mock_bot = mock_bot
        self.mock_config = mock_config
        self.mock_interaction = mock_interaction
        self.mock_bot.config = mock_config
        self.mock_bot.logger = MagicMock()
    
    @pytest.mark.asyncio
    async def test_bot_manage_interaction_check_admin(self, temp_config_file):
        """测试bot_manage的interaction_check - 管理员用户"""
        # 设置用户为管理员
        self.mock_interaction.user.id = 123456789  # 在mock_config的admins列表中
        
        with patch('builtins.open', mock_open(read_data=json.dumps(self.mock_config))):
            from src.bot_manage.cog import BotManageCommands
            
            cog = BotManageCommands(self.mock_bot)
            
            result = await cog.interaction_check(self.mock_interaction)
            
            assert result is True
            # 验证没有发送错误消息
            self.mock_interaction.response.send_message.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_bot_manage_interaction_check_non_admin(self, temp_config_file):
        """测试bot_manage的interaction_check - 非管理员用户"""
        # 设置用户为非管理员
        self.mock_interaction.user.id = 999999999  # 不在mock_config的admins列表中
        
        with patch('builtins.open', mock_open(read_data=json.dumps(self.mock_config))):
            from src.bot_manage.cog import BotManageCommands
            
            cog = BotManageCommands(self.mock_bot)
            
            result = await cog.interaction_check(self.mock_interaction)
            
            assert result is False
            # 验证发送了错误消息
            self.mock_interaction.response.send_message.assert_called_once()
            call_args = self.mock_interaction.response.send_message.call_args
            assert "没有权限" in call_args[0][0]
            assert call_args[1]['ephemeral'] is True
    
    @pytest.mark.asyncio
    async def test_bot_manage_interaction_check_config_error(self, temp_config_file):
        """测试bot_manage的interaction_check - 配置文件错误"""
        # 模拟文件不存在
        with patch('builtins.open', side_effect=FileNotFoundError()):
            from src.bot_manage.cog import BotManageCommands
            
            cog = BotManageCommands(self.mock_bot)
            
            result = await cog.interaction_check(self.mock_interaction)
            
            assert result is False
            # 验证发送了错误消息
            self.mock_interaction.response.send_message.assert_called_once()
            call_args = self.mock_interaction.response.send_message.call_args
            assert "配置文件错误" in call_args[0][0]
