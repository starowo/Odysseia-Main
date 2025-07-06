import logging
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

logger = logging.getLogger('bot')

# 模块管理
class CogManager:
    """Cog管理器，处理Cog的加载、卸载和重载"""
    def __init__(self, bot: commands.Bot, config: dict):
        self.bot: commands.Bot = bot
        self.config: dict = config
        self.loaded_cogs: set = set()
        self.cog_map: dict = {
            "thread_manage": thread_manage.ThreadSelfManage(bot),
            "bot_manage": bot_manage.BotManageCommands(bot),
            "admin": admin.AdminCommands(bot),
            "verify": verify.VerifyCommands(bot),
            "misc": misc.MiscCommands(bot),
            "event": event.EventCommands(bot),
            "anonymous_feedback": anonymous_feedback.AnonymousFeedbackCog(bot),
            "sync": sync.ServerSyncCommands(bot),
            "license": license_auto.LicenseCog(bot)
        }
    
    async def load_all_enabled(self):
        """加载所有配置中启用的Cog"""
        for cog_name, cog_config in self.config.get('cogs', {}).items():
            if cog_config.get('enabled', False):
                if cog_name in self.cog_map:
                    await self.load_cog(self.cog_map[cog_name])
                else:
                    logger.warning(f"模块 {cog_name} 在配置中启用但不在cog_map中")
    
    async def load_cog(self, cog):
        """加载指定的Cog"""
        try:
            await self.bot.add_cog(cog)
            self.loaded_cogs.add(cog)
            await cog.on_ready()
            cog_name = getattr(cog, 'name', type(cog).__name__)
            logger.info(f"已加载模块: {cog_name}")
        except Exception as e:
            cog_name = getattr(cog, 'name', type(cog).__name__)
            logger.error(f"加载模块 {cog_name} 失败: {e}")
    
    async def unload_cog(self, cog):
        """卸载指定的Cog"""
        try:
            await self.bot.remove_cog(cog.qualified_name)
            self.loaded_cogs.discard(cog)
            cog_name = getattr(cog, 'name', type(cog).__name__)
            logger.info(f"已卸载模块: {cog_name}")
        except Exception as e:
            cog_name = getattr(cog, 'name', type(cog).__name__)
            logger.error(f"卸载模块 {cog_name} 失败: {e}")
    
    async def reload_cog(self, cog):
        """重载指定的Cog"""
        try:
            cog_name = getattr(cog, 'name', type(cog).__name__)
            await self.unload_cog(cog)
            await self.load_cog(cog)
            logger.info(f"已重载模块: {cog_name}")
        except Exception as e:
            cog_name = getattr(cog, 'name', type(cog).__name__)
            logger.error(f"重载模块 {cog_name} 失败: {e}")