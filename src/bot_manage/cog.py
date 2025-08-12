import discord
from discord.ext import commands
from discord import app_commands
import json
from functools import wraps

class BotManageCommands(commands.Cog):
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

    command_bot_manage = app_commands.Group(name="管理机器人", description="管理机器人")
    
    @commands.Cog.listener()
    async def on_ready(self):
        if self.logger:
            self.logger.info("管理命令已加载")
            
    
    def is_bot_manager():
        async def predicate(ctx):
            try:
                guild = ctx.guild
                if not guild:
                    return False
                    
                # 在运行时重新加载配置以获取最新的管理员列表
                with open('config.json', 'r', encoding='utf-8') as f:
                    config = json.load(f)
                
                admin_roles = config.get('admins', [])
                
                # 检查用户是否拥有任何管理员身份组
                for admin_role_id in admin_roles:
                    role = guild.get_role(int(admin_role_id))
                    if role and role in ctx.author.roles:
                        return True
                return False
            except Exception:
                return False
        return commands.check(predicate)
    
    # ---- 全局Cog管理命令 ----
    @command_bot_manage.command(name="模块列表", description="列出所有可用模块及其状态")
    @is_bot_manager()
    async def list_modules(self, interaction: discord.Interaction):
        """列出所有可用模块及其状态"""
        embed = discord.Embed(title="可用模块", color=discord.Color.blue())
        
        cog_manager = self.bot.cog_manager
        for cog_name, cog_config in self.config.get('cogs', {}).items():
            # 检查模块是否在cog_manager中存在
            if cog_name in cog_manager.cog_map:
                cog_instance = cog_manager.cog_map[cog_name]
                # 检查该cog类是否已加载到bot中（通过类名检查）
                cog_class_name = cog_instance.__class__.__name__
                status = "✅ 已启用" if cog_class_name in self.bot.cogs else "❌ 已禁用"
            else:
                status = "❌ 未知模块"
            
            description = cog_config.get('description', '无描述')
            
            embed.add_field(
                name=f"{cog_name} - {status}",
                value=description,
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @command_bot_manage.command(name="启用模块", description="启用指定模块")
    @is_bot_manager()
    async def enable_module(self, interaction: discord.Interaction, module_name: str):
        """启用指定模块"""
        cog_manager = self.bot.cog_manager
        
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

    @command_bot_manage.command(name="禁用模块", description="禁用指定模块")
    @is_bot_manager()
    async def disable_module(self, interaction: discord.Interaction, module_name: str):
        """禁用指定模块"""
        cog_manager = self.bot.cog_manager
        
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

    @command_bot_manage.command(name="重载模块", description="重载指定模块（不更新代码）")
    @is_bot_manager()
    async def reload_module(self, interaction: discord.Interaction, module_name: str):
        """重载指定模块（简单重载，不更新代码）"""
        cog_manager = self.bot.cog_manager
        
        # 检查模块是否存在于配置中
        if module_name not in self.config.get('cogs', {}):
            await interaction.response.send_message(f"❌ 模块 `{module_name}` 不存在于配置中", ephemeral=True)
            return
        
        # 检查模块是否在cog_map中
        if module_name not in cog_manager.cog_map:
            await interaction.response.send_message(f"❌ 模块 `{module_name}` 不在cog_map中", ephemeral=True)
            return
        
        cog = cog_manager.cog_map[module_name]
        
        cog_name = cog_manager.cog_class_names[module_name]
        # 如果模块未加载，则先加载
        if cog_name not in self.bot.cogs:
            await interaction.response.send_message(f"⚠️ 模块 `{module_name}` 未启用，正在尝试加载...", ephemeral=True)
            success, message = await cog_manager.load_cog(cog)
            await interaction.followup.send(message, ephemeral=True)
            return
        
        # 简单重载模块（卸载后重新加载同一实例）
        try:
            await cog_manager.unload_cog(cog)
            success, message = await cog_manager.load_cog(cog)
            await interaction.response.send_message(f"🔄 {message}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ 重载模块失败: {e}", ephemeral=True)

    @command_bot_manage.command(name="热重载模块", description="热重载指定模块（更新最新代码）")
    @is_bot_manager()
    async def hot_reload_module(self, interaction: discord.Interaction, module_name: str):
        """热重载指定模块（重新导入Python文件，加载最新代码）"""
        cog_manager = self.bot.cog_manager
        
        # 检查模块是否存在于配置中
        if module_name not in self.config.get('cogs', {}):
            await interaction.response.send_message(f"❌ 模块 `{module_name}` 不存在于配置中", ephemeral=True)
            return
        
        # 检查模块是否在cog_map中
        if module_name not in cog_manager.cog_map:
            await interaction.response.send_message(f"❌ 模块 `{module_name}` 不在cog_map中", ephemeral=True)
            return
        
        # 先发送正在处理的消息
        await interaction.response.defer(ephemeral=True)
        
        cog = cog_manager.cog_map[module_name]
        cog_name = cog_manager.cog_class_names[module_name] # 获取模块名

        # 如果模块未加载，则先加载
        if cog_name not in self.bot.cogs:
            success, message = await cog_manager.load_cog(cog)
            await interaction.followup.send(f"⚠️ 模块 `{cog_name}` 未启用，已尝试加载: {message}", ephemeral=True)
            return
        
        # 热重载模块
        success, message = await cog_manager.reload_cog(cog)
        await self.bot.tree.sync()
        await interaction.followup.send(message, ephemeral=True)

    @command_bot_manage.command(name="ping", description="测试机器人响应时间")
    async def ping_slash(self, interaction: discord.Interaction):
        """测试机器人响应时间 (应用命令)"""
        await interaction.response.send_message(f'延迟: {round(self.bot.latency * 1000)}ms', ephemeral=True) 