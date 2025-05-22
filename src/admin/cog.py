import discord
from discord.ext import commands
from discord import app_commands
import logging
import json
from functools import wraps

class AdminCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger
        self.name = "管理命令"
        self.config = None
        # 从main.py加载配置
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                self.config = json.load(f)
        except Exception as e:
            if self.logger:
                self.logger.error(f"加载配置文件失败: {e}")

    admin = app_commands.Group(name="管理", description="管理机器人")
    
    @commands.Cog.listener()
    async def on_ready(self):
        if self.logger:
            self.logger.info("管理命令已加载")
            
    
    def is_admin():
        async def predicate(ctx):
            # 在运行时重新加载配置以获取最新的管理员列表
            try:
                with open('config.json', 'r', encoding='utf-8') as f:
                    config = json.load(f)
                return ctx.author.id in config.get('admins', [])
            except Exception:
                return False
        return commands.check(predicate)
    
    # ---- 全局Cog管理命令 ----
    @admin.command(name="模块列表", description="列出所有可用模块及其状态")
    @is_admin()
    async def list_modules(self, interaction: discord.Interaction):
        """列出所有可用模块及其状态"""
        embed = discord.Embed(title="可用模块", color=discord.Color.blue())
        
        for cog_name, cog_config in self.config.get('cogs', {}).items():
            status = "✅ 已启用" if cog_name in self.bot.cogs else "❌ 已禁用"
            description = cog_config.get('description', '无描述')
            
            embed.add_field(
                name=f"{cog_name} - {status}",
                value=description,
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @admin.command(name="启用模块", description="启用指定模块")
    @is_admin()
    async def enable_module(self, interaction: discord.Interaction, module_name: str):
        """启用指定模块"""
        from main import cog_manager
        
        # 检查模块是否存在于配置中
        if module_name not in self.config.get('cogs', {}):
            await interaction.response.send_message(f"❌ 模块 `{module_name}` 不存在于配置中", ephemeral=True)
            return
        
        # 如果模块已加载，则返回
        if module_name in self.bot.cogs:
            await interaction.response.send_message(f"⚠️ 模块 `{module_name}` 已经处于启用状态", ephemeral=True)
            return
        
        # 加载模块
        if module_name in cog_manager.cog_map:
            cog = cog_manager.cog_map[module_name]
            success, message = await cog_manager.load_cog(cog)
        else:
            await interaction.response.send_message(f"❌ 模块 `{module_name}` 不在cog_map中", ephemeral=True)
            return
        
        # 如果成功，更新配置
        if success:
            self.config['cogs'][module_name]['enabled'] = True
            with open('config.json', 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        
        await interaction.response.send_message(message, ephemeral=True)

    @admin.command(name="禁用模块", description="禁用指定模块")
    @is_admin()
    async def disable_module(self, interaction: discord.Interaction, module_name: str):
        """禁用指定模块"""
        from main import cog_manager
        
        # 检查模块是否存在于配置中
        if module_name not in self.config.get('cogs', {}):
            await interaction.response.send_message(f"❌ 模块 `{module_name}` 不存在于配置中", ephemeral=True)
            return
        
        # 如果模块未加载，则返回
        if module_name not in self.bot.cogs:
            await interaction.response.send_message(f"⚠️ 模块 `{module_name}` 已经处于禁用状态", ephemeral=True)
            return
        
        # 卸载模块
        if module_name in cog_manager.cog_map:
            cog = cog_manager.cog_map[module_name]
            success, message = await cog_manager.unload_cog(cog)
        else:
            await interaction.response.send_message(f"❌ 模块 `{module_name}` 不在cog_map中", ephemeral=True)
            return
        
        # 如果成功，更新配置
        if success:
            self.config['cogs'][module_name]['enabled'] = False
            with open('config.json', 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        
        await interaction.response.send_message(message, ephemeral=True)

    @admin.command(name="重载模块", description="重载指定模块")
    @is_admin()
    async def reload_module(self, interaction: discord.Interaction, module_name: str):
        """重载指定模块"""
        from main import cog_manager
        
        # 检查模块是否存在于配置中
        if module_name not in self.config.get('cogs', {}):
            await interaction.response.send_message(f"❌ 模块 `{module_name}` 不存在于配置中", ephemeral=True)
            return
        
        # 检查模块是否在cog_map中
        if module_name not in cog_manager.cog_map:
            await interaction.response.send_message(f"❌ 模块 `{module_name}` 不在cog_map中", ephemeral=True)
            return
        
        cog = cog_manager.cog_map[module_name]
        
        # 如果模块未加载，则先加载
        if module_name not in self.bot.cogs:
            await interaction.response.send_message(f"⚠️ 模块 `{module_name}` 未启用，正在尝试加载...", ephemeral=True)
            success, message = await cog_manager.load_cog(cog)
            await interaction.response.send_message(message, ephemeral=True)
            return
        
        # 重载模块
        success, message = await cog_manager.reload_cog(cog)
        await interaction.response.send_message(message, ephemeral=True)

    @admin.command(name="ping", description="测试机器人响应时间")
    async def ping_slash(self, interaction: discord.Interaction):
        """测试机器人响应时间 (应用命令)"""
        await interaction.response.send_message(f'延迟: {round(self.bot.latency * 1000)}ms', ephemeral=True) 