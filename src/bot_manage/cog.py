#src\bot_manage\cog.py
import discord
from discord.ext import commands
from discord import app_commands
import json
from functools import wraps
import traceback  

class BotManageCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger
        self.name = "管理机器人" 
        self.config = self.bot.config 
        self.command_bot_manage = app_commands.Group(name="bot管理", description="管理机器人")
    
    @commands.Cog.listener()
    async def on_ready(self):
        if self.logger:
            self.logger.info("机器人管理命令已加载")
            
    def is_bot_manager():
        async def predicate(interaction: discord.Interaction): # predicate 接收 interaction
            # 从 bot.config 获取管理员列表
            # interaction.client 是 bot 实例
            config = interaction.client.config
            admin_ids = config.get('admins', [])
            
            print(f"DEBUG (Bot Manager Check): 用户 ID (int): {interaction.user.id}, 类型: {type(interaction.user.id)}")
            print(f"DEBUG (Bot Manager Check): config.json 中的管理员 ID 列表 (list of str): {admin_ids}, 列表中元素类型: {type(admin_ids[0]) if admin_ids else 'N/A'}")
            
            if str(interaction.user.id) in admin_ids: # 确保将用户ID转换为字符串进行比较
                print(f"DEBUG (Bot Manager Check): 用户 {interaction.user.id} IS a bot manager. 检查通过。")
                return True
            else:
                print(f"DEBUG (Bot Manager Check): 用户 {interaction.user.id} IS NOT a bot manager. 检查失败。")
                return False
        return app_commands.check(predicate) # 使用 app_commands.check
    
    # ---- 全局Cog管理命令 ----
    @app_commands.command(name="模块列表", description="列出所有可用模块及其状态")
    @is_bot_manager()
    async def list_modules(self, interaction: discord.Interaction):
        """列出所有可用模块及其状态"""
        embed = discord.Embed(title="可用模块", color=discord.Color.blue())
        
        # 遍历 CogManager 中定义的模块路径
        cog_manager_instance = self.bot.cog_manager
        if not cog_manager_instance:
            await interaction.response.send_message("❌ 机器人管理器未初始化。", ephemeral=True)
            return

        for cog_name, module_path in cog_manager_instance.cog_module_paths.items():
            # 检查模块是否已加载
            # bot.extensions 存储的是扩展的模块路径字符串
            status = "✅ 已启用" if module_path in self.bot.extensions else "❌ 已禁用"
            
            # 从主配置中获取模块描述
            description = self.config.get('cogs', {}).get(cog_name, {}).get('description', '无描述')
            
            embed.add_field(
                name=f"{cog_name} - {status}",
                value=description,
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="启用模块", description="启用指定模块")
    @is_bot_manager()
    async def enable_module(self, interaction: discord.Interaction, module_name: str):
        """启用指定模块"""
        cog_manager_instance = self.bot.cog_manager
        if not cog_manager_instance:
            await interaction.response.send_message("❌ 机器人管理器未初始化。", ephemeral=True)
            return

        # 检查模块是否存在于 CogManager 的路径映射中
        if module_name not in cog_manager_instance.cog_module_paths:
            await interaction.response.send_message(f"❌ 模块 `{module_name}` 不存在于机器人模块列表中。", ephemeral=True)
            return
        
        # 如果模块已加载，则返回
        if cog_manager_instance.cog_module_paths[module_name] in self.bot.extensions:
            await interaction.response.send_message(f"⚠️ 模块 `{module_name}` 已经处于启用状态。", ephemeral=True)
            return
        
        # 加载模块
        success, message = await cog_manager_instance.load_cog_by_name(module_name)
        
        # 如果成功，更新配置
        if success:
            # 确保 'cogs' 键存在
            if 'cogs' not in self.config:
                self.config['cogs'] = {}
            # 确保模块的配置项存在
            if module_name not in self.config['cogs']:
                self.config['cogs'][module_name] = {}
            self.config['cogs'][module_name]['enabled'] = True
            try:
                with open('config.json', 'w', encoding='utf-8') as f:
                    json.dump(self.config, f, indent=4, ensure_ascii=False)
            except Exception as e:
                self.logger.error(f"保存配置失败: {e}")
                message += "\n⚠️ 配置保存失败，下次启动可能需要手动启用。"
        
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="禁用模块", description="禁用指定模块")
    @is_bot_manager()
    async def disable_module(self, interaction: discord.Interaction, module_name: str):
        """禁用指定模块"""
        cog_manager_instance = self.bot.cog_manager
        if not cog_manager_instance:
            await interaction.response.send_message("❌ 机器人管理器未初始化。", ephemeral=True)
            return
        
        # 检查模块是否存在于 CogManager 的路径映射中
        if module_name not in cog_manager_instance.cog_module_paths:
            await interaction.response.send_message(f"❌ 模块 `{module_name}` 不存在于机器人模块列表中。", ephemeral=True)
            return
        
        # 如果模块未加载，则返回
        if cog_manager_instance.cog_module_paths[module_name] not in self.bot.extensions:
            await interaction.response.send_message(f"⚠️ 模块 `{module_name}` 已经处于禁用状态。", ephemeral=True)
            return
        
        # 卸载模块
        success, message = await cog_manager_instance.unload_cog_by_name(module_name)
        
        # 如果成功，更新配置
        if success:
            if 'cogs' not in self.config:
                self.config['cogs'] = {}
            if module_name not in self.config['cogs']:
                self.config['cogs'][module_name] = {}
            self.config['cogs'][module_name]['enabled'] = False
            try:
                with open('config.json', 'w', encoding='utf-8') as f:
                    json.dump(self.config, f, indent=4, ensure_ascii=False)
            except Exception as e:
                self.logger.error(f"保存配置失败: {e}")
                message += "\n⚠️ 配置保存失败，下次启动可能需要手动禁用。"
        
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="重载模块", description="重载指定模块")
    @is_bot_manager()
    async def reload_module(self, interaction: discord.Interaction, module_name: str):
        """重载指定模块"""
        cog_manager_instance = self.bot.cog_manager
        if not cog_manager_instance:
            await interaction.response.send_message("❌ 机器人管理器未初始化。", ephemeral=True)
            return
        
        # 检查模块是否存在于 CogManager 的路径映射中
        if module_name not in cog_manager_instance.cog_module_paths:
            await interaction.response.send_message(f"❌ 模块 `{module_name}` 不存在于机器人模块列表中。", ephemeral=True)
            return
        
        # 重载模块
        success, message = await cog_manager_instance.reload_cog_by_name(module_name)
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="ping", description="测试机器人响应时间")
    async def ping_slash(self, interaction: discord.Interaction):
        """测试机器人响应时间 (应用命令)"""
        await interaction.response.send_message(f'延迟: {round(self.bot.latency * 1000)}ms', ephemeral=True)

# 每个 Cog 模块都需要一个 setup 函数，供 discord.py 加载扩展时调用
async def setup(bot: commands.Bot):
    await bot.add_cog(BotManageCommands(bot))

