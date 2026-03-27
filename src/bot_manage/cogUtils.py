import logging
import importlib
import sys
from discord.ext import commands

import src.thread_manage.cog as thread_manage
import src.bot_manage.cog as bot_manage
import src.admin.cog as admin
import src.verify.cog as verify
import src.misc.cog as misc
import src.event.cog as event
import src.anonymous_feedback.cog as anonymous_feedback
import src.sync.cog as sync
import src.license.cog as license_auto
import src.banner.cog as banner
import src.thread_pin_watch.cog as thread_pin_watch

# 模块管理
class CogManager:
    """Cog管理器，处理Cog的加载、卸载和重载，支持热更新"""
    def __init__(self, bot: commands.Bot, config: dict):
        self.bot: commands.Bot = bot
        bot.cog_manager = self
        self.logger = bot.logger
        self.config: dict = config
        self.loaded_cogs: set = set()
        
        # 模块路径映射，用于热重载
        self.cog_module_paths: dict = {
            "thread_manage": "src.thread_manage.cog",
            "bot_manage": "src.bot_manage.cog", 
            "admin": "src.admin.cog",
            "verify": "src.verify.cog",
            "misc": "src.misc.cog",
            "event": "src.event.cog",
            "anonymous_feedback": "src.anonymous_feedback.cog",
            "sync": "src.sync.cog",
            "license": "src.license.cog",
            "banner": "src.banner.cog",
            "thread_pin_watch": "src.thread_pin_watch.cog"
        }
        
        # Cog类名映射
        self.cog_class_names: dict = {
            "thread_manage": "ThreadSelfManage",
            "bot_manage": "BotManageCommands",
            "admin": "AdminCommands", 
            "verify": "VerifyCommands",
            "misc": "MiscCommands",
            "event": "EventCommands",
            "anonymous_feedback": "AnonymousFeedbackCog",
            "sync": "ServerSyncCommands",
            "license": "LicenseCog",
            "banner": "BannerCommands",
            "thread_pin_watch": "ThreadPinWatchCommands"
        }
        
        # 初始化Cog实例映射
        self.cog_map: dict = {
            "thread_manage": thread_manage.ThreadSelfManage(bot),
            "bot_manage": bot_manage.BotManageCommands(bot),
            "admin": admin.AdminCommands(bot),
            "verify": verify.VerifyCommands(bot),
            "misc": misc.MiscCommands(bot),
            "event": event.EventCommands(bot),
            "anonymous_feedback": anonymous_feedback.AnonymousFeedbackCog(bot),
            "sync": sync.ServerSyncCommands(bot),
            "license": license_auto.LicenseCog(bot),
            "banner": banner.BannerCommands(bot),
            "thread_pin_watch": thread_pin_watch.ThreadPinWatchCommands(bot)
        }
    
    async def load_all_enabled(self):
        """加载所有配置中启用的Cog"""
        for cog_name, cog_config in self.config.get('cogs', {}).items():
            if cog_config.get('enabled', False):
                if cog_name in self.cog_map:
                    success, message = await self.load_cog(self.cog_map[cog_name])
                    if not success:
                        self.logger.error(f"启用模块 {cog_name} 失败: {message}")
                else:
                    self.logger.warning(f"模块 {cog_name} 在配置中启用但不在cog_map中")
    
    async def load_cog(self, cog):
        """加载指定的Cog"""
        try:
            await self.bot.add_cog(cog)
            self.loaded_cogs.add(cog)
            if hasattr(cog, 'on_ready'):
                await cog.on_ready()
            cog_name = getattr(cog, 'name', type(cog).__name__)
            self.logger.info(f"已加载模块: {cog_name}")
            return True, f"✅ 模块 `{cog_name}` 加载成功"
        except Exception as e:
            cog_name = getattr(cog, 'name', type(cog).__name__)
            self.logger.error(f"加载模块 {cog_name} 失败: {e}")
            return False, f"❌ 模块 `{cog_name}` 加载失败: {e}"
    
    async def unload_cog(self, cog):
        """卸载指定的Cog"""
        try:
            if hasattr(cog, 'on_disable'):
                await cog.on_disable()
            await self.bot.remove_cog(cog.qualified_name)
            
            self.loaded_cogs.discard(cog)
            cog_name = getattr(cog, 'name', type(cog).__name__)
            self.logger.info(f"已卸载模块: {cog_name}")
            return True, f"✅ 模块 `{cog_name}` 卸载成功"
        except Exception as e:
            cog_name = getattr(cog, 'name', type(cog).__name__)
            self.logger.error(f"卸载模块 {cog_name} 失败: {e}")
            return False, f"❌ 模块 `{cog_name}` 卸载失败: {e}"
    
    async def reload_cog(self, cog):
        """重载指定的Cog（支持热更新）"""
        cog_name = getattr(cog, 'name', type(cog).__name__)
        
        # 查找对应的模块名
        module_name = None
        for name, instance in self.cog_map.items():
            if instance is cog:
                module_name = name
                break
        
        if module_name is None:
            self.logger.error(f"无法找到模块 {cog_name} 对应的模块名")
            return False, f"❌ 无法找到模块 `{cog_name}` 对应的模块名"
        
        try:
            # 先卸载现有的Cog
            success, _ = await self.unload_cog(cog)
            if not success:
                return False, f"❌ 卸载模块 `{cog_name}` 失败"
            
            # 重新导入模块（热更新的关键步骤）
            module_path = self.cog_module_paths.get(module_name)
            if module_path and module_path in sys.modules:
                self.logger.info(f"正在重新导入模块: {module_path}")
                importlib.reload(sys.modules[module_path])
            
            # 重新创建Cog实例
            if module_name in self.cog_module_paths:
                module_path = self.cog_module_paths[module_name]
                class_name = self.cog_class_names[module_name]
                
                # 动态导入重新加载后的模块
                module = importlib.import_module(module_path)
                cog_class = getattr(module, class_name)
                new_cog = cog_class(self.bot)
                
                # 更新cog_map中的实例
                self.cog_map[module_name] = new_cog
                
                # 加载新的Cog实例
                success, message = await self.load_cog(new_cog)
                if success:
                    self.logger.info(f"已热重载模块: {cog_name}")
                    return True, f"🔄 模块 `{cog_name}` 热重载成功（已加载最新代码）"
                else:
                    return False, f"❌ 重载后加载模块 `{cog_name}` 失败: {message}"
            else:
                return False, f"❌ 未找到模块 `{module_name}` 的路径配置"
                
        except Exception as e:
            self.logger.error(f"热重载模块 {cog_name} 失败: {e}")
            return False, f"❌ 热重载模块 `{cog_name}` 失败: {e}"
    
    async def reload_cog_by_name(self, module_name: str):
        """通过模块名重载Cog（便捷方法）"""
        if module_name not in self.cog_map:
            return False, f"❌ 模块 `{module_name}` 不存在"
        
        cog = self.cog_map[module_name]
        return await self.reload_cog(cog)