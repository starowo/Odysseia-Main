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
        # 确保从 bot 实例获取 config，而不是重新打开文件，以保证一致性
        self.config = getattr(bot, 'config', {})

    command_bot_manage = app_commands.Group(name="管理机器人", description="管理机器人")

    @commands.Cog.listener()
    async def on_ready(self):
        if self.logger:
            self.logger.info("管理命令已加载")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """应用命令的权限检查"""
        try:
            # 重新从文件加载配置以获取最新的管理员列表进行权限检查
            with open('config.json', 'r', encoding='utf-8') as f:
                runtime_config = json.load(f)
            is_admin = interaction.user.id in runtime_config.get('admins', [])
            if not is_admin:
                self.logger.warning(f"用户 {interaction.user} (ID: {interaction.user.id}) 尝试执行受限命令 '{interaction.command.qualified_name if interaction.command else '未知命令'}' 但不是管理员。")
                await interaction.response.send_message("❌ 你没有权限执行此命令。", ephemeral=True)
                return False
            return True
        except FileNotFoundError:
            self.logger.error("config.json 未找到，无法进行权限检查。")
            await interaction.response.send_message("❌ 配置文件错误，无法检查权限。", ephemeral=True)
            return False
        except Exception as e:
            self.logger.error(f"权限检查时发生错误: {e}")
            await interaction.response.send_message("❌ 权限检查时发生内部错误。", ephemeral=True)
            return False

    # ---- 全局Cog管理命令 ----
    @command_bot_manage.command(name="模块列表", description="列出所有可用模块及其状态")
    async def list_modules(self, interaction: discord.Interaction):
        """列出所有可用模块及其状态"""
        embed = discord.Embed(title="可用模块", color=discord.Color.blue())

        # 使用 self.bot.extensions 来检查当前加载的模块
        cog_manager_instance = self.bot.cog_manager
        for cog_key, module_path_value in cog_manager_instance.cog_module_paths.items():
            # 检查模块是否实际加载
            status = "✅ 已启用" if module_path_value in self.bot.extensions else "❌ 已禁用"
            # 从主配置中获取描述信息
            description = self.config.get('cogs', {}).get(cog_key, {}).get('description', '无描述')

            embed.add_field(
                name=f"{cog_key} - {status}",
                value=description,
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @command_bot_manage.command(name="启用模块", description="启用指定模块")
    async def enable_module(self, interaction: discord.Interaction, module_name: str):
        """启用指定模块"""
        cog_manager_instance = self.bot.cog_manager

        if module_name not in cog_manager_instance.cog_module_paths:
            await interaction.response.send_message(f"❌ 模块 `{module_name}` 未在机器人已知模块路径中定义。", ephemeral=True)
            return

        module_path_to_load = cog_manager_instance.cog_module_paths[module_name]

        if module_path_to_load in self.bot.extensions:
            await interaction.response.send_message(f"⚠️ 模块 `{module_name}` 已经处于启用状态。", ephemeral=True)
            return

        success, message = await cog_manager_instance.load_extension(module_path_to_load, module_name)

        if success:
            # 更新 config.json 中的状态
            try:
                with open('config.json', 'r', encoding='utf-8') as f:
                    current_config = json.load(f)
                if 'cogs' not in current_config:
                    current_config['cogs'] = {}
                if module_name not in current_config['cogs']:
                    current_config['cogs'][module_name] = {}
                current_config['cogs'][module_name]['enabled'] = True
                with open('config.json', 'w', encoding='utf-8') as f:
                    json.dump(current_config, f, indent=4, ensure_ascii=False)
                self.config = current_config  # 更新 cog 内部的 config 副本
            except Exception as e:
                self.logger.error(f"更新 config.json 失败 (启用模块 {module_name}): {e}")
                message += " (但配置文件更新失败)"

        await interaction.response.send_message(message, ephemeral=True)

    @command_bot_manage.command(name="禁用模块", description="禁用指定模块")
    async def disable_module(self, interaction: discord.Interaction, module_name: str):
        """禁用指定模块"""
        cog_manager_instance = self.bot.cog_manager

        if module_name not in cog_manager_instance.cog_module_paths:
            await interaction.response.send_message(f"❌ 模块 `{module_name}` 未在机器人已知模块路径中定义。", ephemeral=True)
            return

        module_path_to_unload = cog_manager_instance.cog_module_paths[module_name]

        if module_path_to_unload not in self.bot.extensions:
            await interaction.response.send_message(f"⚠️ 模块 `{module_name}` 已经处于禁用状态。", ephemeral=True)
            return

        success, message = await cog_manager_instance.unload_extension(module_path_to_unload, module_name)

        if success:
            try:
                with open('config.json', 'r', encoding='utf-8') as f:
                    current_config = json.load(f)
                if 'cogs' not in current_config:
                    current_config['cogs'] = {}
                if module_name not in current_config['cogs']:
                    current_config['cogs'][module_name] = {}
                current_config['cogs'][module_name]['enabled'] = False
                with open('config.json', 'w', encoding='utf-8') as f:
                    json.dump(current_config, f, indent=4, ensure_ascii=False)
                self.config = current_config  # 更新 cog 内部的 config 副本
            except Exception as e:
                self.logger.error(f"更新 config.json 失败 (禁用模块 {module_name}): {e}")
                message += " (但配置文件更新失败)"

        await interaction.response.send_message(message, ephemeral=True)

    @command_bot_manage.command(name="重载模块", description="重载指定模块")
    async def reload_module(self, interaction: discord.Interaction, module_name: str):
        """重载指定模块"""
        cog_manager_instance = self.bot.cog_manager

        if module_name not in cog_manager_instance.cog_module_paths:
            await interaction.response.send_message(f"❌ 模块 `{module_name}` 未在机器人已知模块路径中定义。", ephemeral=True)
            return

        module_path_to_reload = cog_manager_instance.cog_module_paths[module_name]

        # reload_extension 会在未加载时尝试加载它
        success, message = await cog_manager_instance.reload_extension(module_path_to_reload, module_name)

        if success:
            # 如果重载/加载成功，确保其在 config.json 中标记为 enabled
            try:
                with open('config.json', 'r', encoding='utf-8') as f:
                    current_config = json.load(f)
                if 'cogs' not in current_config:
                    current_config['cogs'] = {}
                if module_name not in current_config['cogs']:
                    current_config['cogs'][module_name] = {}

                # 只有当模块实际加载成功后才更新配置为 enabled
                if module_path_to_reload in self.bot.extensions:
                    if not current_config['cogs'][module_name].get('enabled', False):
                        current_config['cogs'][module_name]['enabled'] = True
                        with open('config.json', 'w', encoding='utf-8') as f:
                            json.dump(current_config, f, indent=4, ensure_ascii=False)
                        self.config = current_config  # 更新 cog 内部的 config 副本
                elif current_config['cogs'][module_name].get('enabled', False):
                    current_config['cogs'][module_name]['enabled'] = False
                    with open('config.json', 'w', encoding='utf-8') as f:
                        json.dump(current_config, f, indent=4, ensure_ascii=False)
                    self.config = current_config  # 更新 cog 内部的 config 副本
            except Exception as e:
                self.logger.error(f"更新 config.json 失败 (重载模块 {module_name}): {e}")
                message += " (但配置文件更新可能不一致)"

        await interaction.response.send_message(message, ephemeral=True)

    @command_bot_manage.command(name="ping", description="测试机器人响应时间")
    async def ping_slash(self, interaction: discord.Interaction):
        """测试机器人响应时间 (应用命令)"""
        await interaction.response.send_message(f'延迟: {round(self.bot.latency * 1000)}ms', ephemeral=True)


# ---- 这个函数是关键 ----
async def setup(bot: commands.Bot):
    """当扩展被加载时，discord.py 会调用这个函数。"""
    await bot.add_cog(BotManageCommands(bot))