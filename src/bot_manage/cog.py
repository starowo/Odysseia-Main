import discord
from discord.ext import commands
from discord import app_commands
import json
import asyncio
import aiohttp
import tempfile
import os
from functools import wraps
from src.utils.auth import is_bot_owner

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
            
    
    # ---- 全局Cog管理命令 ----
    @command_bot_manage.command(name="模块列表", description="列出所有可用模块及其状态")
    @is_bot_owner()
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
    @is_bot_owner()
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
    @is_bot_owner()
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
    @is_bot_owner()
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

    @command_bot_manage.command(name="热重载模块", description="热重载指定模块（更新最新代码，含同目录工具文件）")
    @is_bot_owner()
    async def hot_reload_module(self, interaction: discord.Interaction, module_name: str):
        """热重载指定模块（重新导入Python文件，加载最新代码，同时重载同目录辅助模块）"""
        cog_manager = self.bot.cog_manager
        
        if module_name not in self.config.get('cogs', {}):
            await interaction.response.send_message(f"❌ 模块 `{module_name}` 不存在于配置中", ephemeral=True)
            return
        
        if module_name not in cog_manager.cog_map:
            await interaction.response.send_message(f"❌ 模块 `{module_name}` 不在cog_map中", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        cog = cog_manager.cog_map[module_name]
        cog_name = cog_manager.cog_class_names[module_name]

        if cog_name not in self.bot.cogs:
            success, message = await cog_manager.load_cog(cog)
            await interaction.followup.send(f"⚠️ 模块 `{cog_name}` 未启用，已尝试加载: {message}", ephemeral=True)
            return
        
        success, message, reloaded_files = await cog_manager.reload_cog(cog)
        await self.bot.tree.sync()
        
        if reloaded_files:
            file_list = "\n".join(f"  • `{f}`" for f in reloaded_files)
            message += f"\n\n📦 已重载的文件 ({len(reloaded_files)}):\n{file_list}"
        
        await interaction.followup.send(message, ephemeral=True)

    @command_bot_manage.command(name="重载文件", description="重载单个Python文件模块（不重启Cog）")
    @is_bot_owner()
    async def reload_file(self, interaction: discord.Interaction, file_path: str):
        """
        重载单个Python文件模块。
        file_path 支持点分路径(如 src.thread_manage.auto_clear)或文件路径(如 src/thread_manage/auto_clear.py)
        """
        cog_manager = self.bot.cog_manager
        success, message = cog_manager.reload_module_file(file_path)
        await interaction.response.send_message(message, ephemeral=True)

    @command_bot_manage.command(name="ping", description="测试机器人响应时间")
    async def ping_slash(self, interaction: discord.Interaction):
        """测试机器人响应时间 (应用命令)"""
        await interaction.response.send_message(f'延迟: {round(self.bot.latency * 1000)}ms', ephemeral=True) 

    @command_bot_manage.command(name="同步命令", description="同步所有命令到Discord")
    @is_bot_owner()
    async def sync_commands(self, interaction: discord.Interaction):
        """同步所有命令到Discord"""
        synced = await self.bot.tree.sync()
        synced_guild = await self.bot.tree.sync(guild=interaction.guild)
        await interaction.response.send_message(f"✅ 已同步 {len(synced)} 个全局命令\n已同步 {len(synced_guild)} 个服务器命令", ephemeral=True)

    # ---- 配置管理命令 ----
    @command_bot_manage.command(name="获取配置", description="获取当前的配置文件")
    @is_bot_owner()
    async def get_config(self, interaction: discord.Interaction):
        """获取当前的配置文件"""
        try:
            # 重新加载最新配置
            with open('config.json', 'r', encoding='utf-8') as f:
                current_config = json.load(f)
            
            # 将配置转换为格式化的JSON字符串
            config_json = json.dumps(current_config, indent=4, ensure_ascii=False)
            
            # 创建临时文件
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as temp_file:
                temp_file.write(config_json)
                temp_file_path = temp_file.name
            
            try:
                # 发送配置文件作为附件
                with open(temp_file_path, 'rb') as f:
                    config_file = discord.File(f, filename='config.json')
                    embed = discord.Embed(
                        title="📁 当前配置文件",
                        description="这是机器人当前使用的配置文件",
                        color=discord.Color.green()
                    )
                    await interaction.response.send_message(embed=embed, file=config_file, ephemeral=True)
            finally:
                # 清理临时文件
                os.unlink(temp_file_path)
                
        except Exception as e:
            await interaction.response.send_message(f"❌ 获取配置文件失败: {e}", ephemeral=True)

    @command_bot_manage.command(name="替换配置", description="通过上传文件替换配置")
    @is_bot_owner()
    async def replace_config(self, interaction: discord.Interaction, 文件: discord.Attachment):
        """通过上传文件替换配置"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            # 检查文件类型
            if not 文件.filename.endswith('.json'):
                await interaction.followup.send("❌ 请上传JSON格式的配置文件", ephemeral=True)
                return
            
            # 检查文件大小（限制为1MB）
            if 文件.size > 1024 * 1024:
                await interaction.followup.send("❌ 配置文件过大，请确保小于1MB", ephemeral=True)
                return
            
            # 下载文件内容
            file_content = await 文件.read()
            config_text = file_content.decode('utf-8')
            
            # 验证JSON格式
            try:
                new_config = json.loads(config_text)
            except json.JSONDecodeError as e:
                await interaction.followup.send(f"❌ JSON格式错误: {e}", ephemeral=True)
                return
            
            # 备份当前配置
            try:
                with open('config.json', 'r', encoding='utf-8') as f:
                    backup_config = json.load(f)
                with open('config.json.backup', 'w', encoding='utf-8') as f:
                    json.dump(backup_config, f, indent=4, ensure_ascii=False)
            except Exception as e:
                await interaction.followup.send(f"⚠️ 无法创建配置备份: {e}", ephemeral=True)
            
            # 写入新配置
            with open('config.json', 'w', encoding='utf-8') as f:
                json.dump(new_config, f, indent=4, ensure_ascii=False)
            
            # 更新当前实例的配置
            self.config = new_config
            
            embed = discord.Embed(
                title="✅ 配置已替换",
                description=f"已成功将配置替换为 `{文件.filename}`\n备份文件已保存为 `config.json.backup`",
                color=discord.Color.green()
            )
            embed.add_field(name="⚠️ 重要提醒", value="请重启机器人或重载相关模块使新配置生效", inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            await interaction.followup.send(f"❌ 替换配置失败: {e}", ephemeral=True)

    def _merge_config(self, base_config: dict, override_config: dict) -> dict:
        """递归合并配置字典，override_config中的值会覆盖base_config中的对应值"""
        result = base_config.copy()
        
        for key, value in override_config.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                # 如果两边都是字典，递归合并
                result[key] = self._merge_config(result[key], value)
            else:
                # 否则直接覆盖
                result[key] = value
        
        return result

    @command_bot_manage.command(name="覆盖配置", description="通过JSON文本部分覆盖配置（只更新提供的键值对）")
    @is_bot_owner()
    async def override_config(self, interaction: discord.Interaction, json文本: str):
        """通过JSON文本部分覆盖配置（只更新提供的键值对，保留其他配置）"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            # 验证JSON格式
            try:
                override_data = json.loads(json文本)
            except json.JSONDecodeError as e:
                await interaction.followup.send(f"❌ JSON格式错误: {e}", ephemeral=True)
                return
            
            # 加载当前配置
            try:
                with open('config.json', 'r', encoding='utf-8') as f:
                    current_config = json.load(f)
            except Exception as e:
                await interaction.followup.send(f"❌ 无法读取当前配置: {e}", ephemeral=True)
                return
            
            # 备份当前配置
            try:
                with open('config.json.backup', 'w', encoding='utf-8') as f:
                    json.dump(current_config, f, indent=4, ensure_ascii=False)
            except Exception as e:
                await interaction.followup.send(f"⚠️ 无法创建配置备份: {e}", ephemeral=True)
            
            # 合并配置（只覆盖提供的键值对）
            merged_config = self._merge_config(current_config, override_data)
            
            # 写入合并后的配置
            with open('config.json', 'w', encoding='utf-8') as f:
                json.dump(merged_config, f, indent=4, ensure_ascii=False)
            
            # 更新当前实例的配置
            self.config = merged_config
            
            embed = discord.Embed(
                title="✅ 配置已部分覆盖",
                description="已成功更新指定的配置项，其他配置保持不变\n备份文件已保存为 `config.json.backup`",
                color=discord.Color.green()
            )
            embed.add_field(name="⚠️ 重要提醒", value="请重启机器人或重载相关模块使新配置生效", inline=False)
            
            # 显示更新的配置项
            if len(json文本) <= 500:
                embed.add_field(name="📝 更新的配置项", value=f"```json\n{json.dumps(override_data, indent=2, ensure_ascii=False)[:500]}```", inline=False)
            
            # 统计更新的键数量
            def count_keys(d):
                count = 0
                for key, value in d.items():
                    count += 1
                    if isinstance(value, dict):
                        count += count_keys(value)
                return count
            
            updated_keys = count_keys(override_data)
            embed.add_field(name="📊 更新统计", value=f"共更新了 {updated_keys} 个配置项", inline=True)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            await interaction.followup.send(f"❌ 覆盖配置失败: {e}", ephemeral=True)