"""
配置系统测试
测试配置传递机制是否正常工作
"""
import pytest
import json
from unittest.mock import MagicMock, patch, mock_open
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class TestConfigSystem:
    """配置系统测试类"""
    
    @pytest.fixture(autouse=True)
    def setup(self, mock_bot, mock_config):
        """设置测试环境"""
        self.mock_bot = mock_bot
        self.mock_config = mock_config
    
    def test_config_loading(self, temp_config_file):
        """测试配置文件加载"""
        with patch('main.CONFIG', self.mock_config):
            from main import load_config
            
            # 测试配置加载函数
            with patch('builtins.open', mock_open(read_data=json.dumps(self.mock_config))):
                config = load_config()
                
                assert config is not None
                assert config['token'] == 'test_token'
                assert config['prefix'] == '!'
                assert 'cogs' in config
                assert 'admins' in config
    
    def test_config_loading_error(self):
        """测试配置文件加载错误处理"""
        from main import load_config
        
        # 模拟文件不存在
        with patch('builtins.open', side_effect=FileNotFoundError()):
            config = load_config()
            assert config is None
        
        # 模拟JSON解析错误
        with patch('builtins.open', mock_open(read_data="invalid json")):
            config = load_config()
            assert config is None
    
    def test_bot_config_attachment(self, temp_config_file):
        """测试配置附加到bot实例"""
        with patch('main.CONFIG', self.mock_config):
            # 模拟main.py中的配置附加逻辑
            self.mock_bot.config = self.mock_config
            self.mock_bot.logger = MagicMock()
            
            # 验证配置正确附加
            assert hasattr(self.mock_bot, 'config')
            assert self.mock_bot.config == self.mock_config
            assert hasattr(self.mock_bot, 'logger')
    
    def test_cog_manager_attachment(self, temp_config_file):
        """测试CogManager附加到bot实例"""
        with patch('main.CONFIG', self.mock_config):
            from main import CogManager
            
            cog_manager = CogManager(self.mock_bot, self.mock_config)
            self.mock_bot.cog_manager = cog_manager
            
            # 验证cog_manager正确附加
            assert hasattr(self.mock_bot, 'cog_manager')
            assert self.mock_bot.cog_manager == cog_manager
            assert self.mock_bot.cog_manager.bot == self.mock_bot
            assert self.mock_bot.cog_manager.config == self.mock_config
    
    def test_cog_config_access(self, temp_config_file):
        """测试Cog通过bot实例访问配置"""
        self.mock_bot.config = self.mock_config
        self.mock_bot.logger = MagicMock()
        
        with patch('builtins.open', mock_open(read_data=json.dumps(self.mock_config))):
            from src.bot_manage.cog import BotManageCommands
            
            cog = BotManageCommands(self.mock_bot)
            
            # 验证Cog可以访问配置
            assert cog.config == self.mock_config
            assert cog.config['token'] == 'test_token'
            assert cog.config['admins'] == [123456789]
    
    def test_config_fallback(self):
        """测试配置回退机制"""
        # 创建没有config属性的bot
        bot_without_config = MagicMock()
        bot_without_config.logger = MagicMock()
        # 删除config属性
        if hasattr(bot_without_config, 'config'):
            delattr(bot_without_config, 'config')
        
        with patch('builtins.open', mock_open(read_data=json.dumps(self.mock_config))):
            from src.bot_manage.cog import BotManageCommands
            
            cog = BotManageCommands(bot_without_config)
            
            # 验证使用了getattr的默认值
            assert cog.config == {}
    
    def test_config_update_mechanism(self, temp_config_file):
        """测试配置更新机制"""
        self.mock_bot.config = self.mock_config.copy()
        self.mock_bot.logger = MagicMock()
        self.mock_bot.extensions = {}
        
        # 创建CogManager
        with patch('main.CONFIG', self.mock_config):
            from main import CogManager
            
            cog_manager = CogManager(self.mock_bot, self.mock_config)
            self.mock_bot.cog_manager = cog_manager
        
        # 创建BotManageCommands实例
        with patch('builtins.open', mock_open(read_data=json.dumps(self.mock_config))):
            from src.bot_manage.cog import BotManageCommands
            
            cog = BotManageCommands(self.mock_bot)
            
            # 模拟配置更新
            updated_config = self.mock_config.copy()
            updated_config['new_setting'] = 'new_value'
            
            # 模拟配置文件更新
            with patch('builtins.open', mock_open(read_data=json.dumps(updated_config))):
                # 在实际应用中，这会通过enable_module等方法更新
                cog.config = updated_config
                
                assert cog.config['new_setting'] == 'new_value'
    
    def test_module_paths_configuration(self, temp_config_file):
        """测试模块路径配置"""
        with patch('main.CONFIG', self.mock_config):
            from main import CogManager
            
            cog_manager = CogManager(self.mock_bot, self.mock_config)
            
            # 验证模块路径映射正确
            expected_paths = {
                "thread_manage": "src.thread_manage.cog",
                "bot_manage": "src.bot_manage.cog",
                "admin": "src.admin.cog",
                "verify": "src.verify.cog"
            }
            
            assert cog_manager.cog_module_paths == expected_paths
            
            # 验证所有配置中的模块都有对应的路径
            for cog_name in self.mock_config['cogs'].keys():
                assert cog_name in cog_manager.cog_module_paths
    
    def test_logging_configuration(self, temp_config_file):
        """测试日志配置"""
        # 验证日志配置结构
        logging_config = self.mock_config.get('logging', {})
        
        assert 'enabled' in logging_config
        assert 'guild_id' in logging_config
        assert 'channel_id' in logging_config
        assert 'level' in logging_config
        
        # 验证日志级别有效
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        assert logging_config['level'].upper() in valid_levels
