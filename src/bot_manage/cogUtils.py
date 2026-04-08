import logging
import importlib
import pkgutil
import sys
from pathlib import Path
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
import src.post_filter.cog as post_filter

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
            "post_filter": "src.post_filter.cog"
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
            "post_filter": "PostFilterCog"
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
            "post_filter": post_filter.PostFilterCog(bot)
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
    
    def _get_package_modules(self, module_name: str) -> list[str]:
        """获取指定模块包下所有已加载的子模块路径（不含 cog 本身），按依赖深度排序"""
        cog_module_path = self.cog_module_paths.get(module_name)
        if not cog_module_path:
            return []
        
        package_prefix = cog_module_path.rsplit('.', 1)[0]  # e.g. "src.thread_manage"
        sibling_modules = []
        for mod_key in list(sys.modules.keys()):
            if mod_key == cog_module_path:
                continue
            if mod_key == package_prefix or mod_key.startswith(package_prefix + '.'):
                sibling_modules.append(mod_key)
        
        # 子模块越深越先 reload（叶子节点优先），保证被依赖方先更新
        sibling_modules.sort(key=lambda m: m.count('.'), reverse=True)
        return sibling_modules

    async def reload_cog(self, cog):
        """重载指定的Cog（支持热更新），同时重载同包下的辅助模块"""
        cog_name = getattr(cog, 'name', type(cog).__name__)
        
        module_name = None
        for name, instance in self.cog_map.items():
            if instance is cog:
                module_name = name
                break
        
        if module_name is None:
            self.logger.error(f"无法找到模块 {cog_name} 对应的模块名")
            return False, f"❌ 无法找到模块 `{cog_name}` 对应的模块名", []
        
        try:
            success, _ = await self.unload_cog(cog)
            if not success:
                return False, f"❌ 卸载模块 `{cog_name}` 失败", []
            
            reloaded_files = []
            
            # 先重载同包下的辅助模块（工具代码）
            sibling_modules = self._get_package_modules(module_name)
            for mod_path in sibling_modules:
                if mod_path in sys.modules:
                    try:
                        self.logger.info(f"正在重新导入辅助模块: {mod_path}")
                        importlib.reload(sys.modules[mod_path])
                        reloaded_files.append(mod_path)
                    except Exception as e:
                        self.logger.warning(f"重载辅助模块 {mod_path} 失败: {e}")
            
            # 再重载 cog 模块本身
            module_path = self.cog_module_paths.get(module_name)
            if module_path and module_path in sys.modules:
                self.logger.info(f"正在重新导入模块: {module_path}")
                importlib.reload(sys.modules[module_path])
                reloaded_files.append(module_path)
            
            if module_name in self.cog_module_paths:
                module_path = self.cog_module_paths[module_name]
                class_name = self.cog_class_names[module_name]
                
                module = importlib.import_module(module_path)
                cog_class = getattr(module, class_name)
                new_cog = cog_class(self.bot)
                
                self.cog_map[module_name] = new_cog
                
                success, message = await self.load_cog(new_cog)
                if success:
                    self.logger.info(f"已热重载模块: {cog_name}")
                    return True, f"🔄 模块 `{cog_name}` 热重载成功（已加载最新代码）", reloaded_files
                else:
                    return False, f"❌ 重载后加载模块 `{cog_name}` 失败: {message}", reloaded_files
            else:
                return False, f"❌ 未找到模块 `{module_name}` 的路径配置", reloaded_files
                
        except Exception as e:
            self.logger.error(f"热重载模块 {cog_name} 失败: {e}")
            return False, f"❌ 热重载模块 `{cog_name}` 失败: {e}", []
    
    async def reload_cog_by_name(self, module_name: str):
        """通过模块名重载Cog（便捷方法）"""
        if module_name not in self.cog_map:
            return False, f"❌ 模块 `{module_name}` 不存在", []
        
        cog = self.cog_map[module_name]
        return await self.reload_cog(cog)

    def reload_module_file(self, file_path: str) -> tuple[bool, str]:
        """
        重载单个 Python 文件模块。
        file_path 支持两种格式:
          - 点分模块路径: "src.thread_manage.auto_clear"
          - 文件系统路径: "src/thread_manage/auto_clear.py"
        """
        # 统一转换为点分模块路径
        module_path = file_path.replace('\\', '/').replace('/', '.')
        if module_path.endswith('.py'):
            module_path = module_path[:-3]
        
        if module_path not in sys.modules:
            try:
                importlib.import_module(module_path)
                self.logger.info(f"首次导入模块: {module_path}")
                return True, f"✅ 模块 `{module_path}` 首次导入成功"
            except Exception as e:
                self.logger.error(f"导入模块 {module_path} 失败: {e}")
                return False, f"❌ 导入模块 `{module_path}` 失败: {e}"
        
        try:
            importlib.reload(sys.modules[module_path])
            self.logger.info(f"已重载文件模块: {module_path}")
            return True, f"✅ 文件 `{module_path}` 重载成功"
        except Exception as e:
            self.logger.error(f"重载文件模块 {module_path} 失败: {e}")
            return False, f"❌ 文件 `{module_path}` 重载失败: {e}"