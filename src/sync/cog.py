import asyncio
import json
import pathlib
import datetime
import uuid
from typing import Dict, List, Optional, Set
from discord.ext import commands
from discord import app_commands
import discord

from src.utils.confirm_view import confirm_view

class ServerSyncCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger
        self.name = "服务器同步"
        # 初始化配置缓存
        self._config_cache = {}
        self._config_cache_mtime = None

    @commands.Cog.listener()
    async def on_ready(self):
        if self.logger:
            self.logger.info("服务器同步模块已加载")

    @property
    def config(self):
        """读取同步配置文件并缓存，只有在文件修改后重新加载"""
        try:
            path = pathlib.Path('config/server_sync/config.json')
            mtime = path.stat().st_mtime
            if self._config_cache_mtime != mtime:
                with open(path, 'r', encoding='utf-8') as f:
                    self._config_cache = json.load(f)
                self._config_cache_mtime = mtime
            return self._config_cache
        except Exception as e:
            if self.logger:
                self.logger.error(f"加载同步配置文件失败: {e}")
            return {}

    def _save_config(self):
        """保存配置文件"""
        try:
            path = pathlib.Path('config/server_sync/config.json')
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self._config_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            if self.logger:
                self.logger.error(f"保存同步配置文件失败: {e}")

    def is_admin():
        async def predicate(ctx):
            try:
                guild = ctx.guild
                if not guild:
                    return False
                    
                # 使用统一的配置系统
                cog = ctx.cog
                config = getattr(cog, 'config', {})
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

    # ====== 同步指令 ======
    sync = app_commands.Group(name="同步", description="服务器同步相关指令")
    sync_manage = app_commands.Group(name="同步管理", description="同步管理相关指令")

    @sync.command(name="身份组同步", description="同步可同步的身份组到配置中的全部子服务器")
    @is_admin()
    async def sync_roles(self, interaction: discord.Interaction):
        """同步身份组到所有配置的服务器"""
        if not self.config.get("enabled", False):
            await interaction.response.send_message("❌ 同步功能未启用", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        
        guild_id = str(interaction.guild.id)
        user_id = interaction.user.id
        
        # 检查当前服务器是否在同步列表中
        if guild_id not in self.config.get("servers", {}):
            await interaction.followup.send("❌ 当前服务器未在同步列表中", ephemeral=True)
            return

        # 获取用户在当前服务器的身份组
        user_roles = [role for role in interaction.user.roles if role != interaction.guild.default_role]
        
        # 获取可同步的身份组
        role_mapping = self.config.get("role_mapping", {})
        syncable_roles = []
        
        for role in user_roles:
            role_name = role.name
            if role_name in role_mapping:
                syncable_roles.append((role_name, role))

        if not syncable_roles:
            await interaction.followup.send("❌ 您没有可同步的身份组", ephemeral=True)
            return

        # 同步到其他服务器
        sync_results = []
        servers_config = self.config.get("servers", {})
        
        for target_guild_id, server_config in servers_config.items():
            if target_guild_id == guild_id:  # 跳过当前服务器
                continue
                
            target_guild = self.bot.get_guild(int(target_guild_id))
            if not target_guild:
                sync_results.append(f"❌ 无法访问服务器 {target_guild_id}")
                continue
                
            target_member = target_guild.get_member(user_id)
            if not target_member:
                sync_results.append(f"❌ 您不在服务器 {target_guild.name} 中")
                continue

            # 同步身份组
            synced_count = 0
            role_configs = server_config.get("roles", {})
            
            for role_name, source_role in syncable_roles:
                if role_name in role_configs:
                    target_role_id = role_configs[role_name]
                    target_role = target_guild.get_role(target_role_id)
                    
                    if target_role:
                        try:
                            if target_role not in target_member.roles:
                                await target_member.add_roles(target_role, reason=f"身份组同步 from {interaction.guild.name}")
                                synced_count += 1
                        except discord.Forbidden:
                            sync_results.append(f"❌ 无权限在 {target_guild.name} 中添加身份组 {role_name}")
                        except Exception as e:
                            sync_results.append(f"❌ 在 {target_guild.name} 中同步身份组 {role_name} 失败: {e}")
                    else:
                        sync_results.append(f"❌ 在 {target_guild.name} 中未找到身份组 {role_name}")

            if synced_count > 0:
                sync_results.append(f"✅ 在 {target_guild.name} 中成功同步 {synced_count} 个身份组")

        # 发送结果
        result_text = "\n".join(sync_results) if sync_results else "✅ 同步完成"
        await interaction.followup.send(f"身份组同步结果:\n{result_text}", ephemeral=True)

    # ====== 同步管理指令 ======
    @sync_manage.command(name="添加服务器", description="将当前服务器添加到同步列表")
    @is_admin()
    async def add_server(self, interaction: discord.Interaction):
        """添加当前服务器到同步列表"""
        guild_id = str(interaction.guild.id)
        
        config = self.config
        if "servers" not in config:
            config["servers"] = {}
            
        if guild_id in config["servers"]:
            await interaction.response.send_message("❌ 当前服务器已在同步列表中", ephemeral=True)
            return
            
        config["servers"][guild_id] = {
            "name": interaction.guild.name,
            "roles": {},
            "punishment_sync": False,
            "punishment_announce_channel": None,
            "punishment_confirm_channel": None
        }
        
        self._config_cache = config
        self._save_config()
        
        await interaction.response.send_message("✅ 已将当前服务器添加到同步列表", ephemeral=True)

    @sync_manage.command(name="删除服务器", description="从同步列表中删除当前服务器")
    @is_admin()
    async def remove_server(self, interaction: discord.Interaction):
        """从同步列表删除当前服务器"""
        guild_id = str(interaction.guild.id)
        
        config = self.config
        if guild_id not in config.get("servers", {}):
            await interaction.response.send_message("❌ 当前服务器不在同步列表中", ephemeral=True)
            return

        # 确认删除
        confirmed = await confirm_view(
            interaction,
            title="确认删除服务器",
            description="确定要从同步列表中删除当前服务器吗？这将移除所有身份组映射配置。",
            colour=discord.Colour.red(),
            timeout=60,
        )

        if not confirmed:
            return

        del config["servers"][guild_id]
        
        # 同时删除相关的身份组映射
        role_mapping = config.get("role_mapping", {})
        for role_name in list(role_mapping.keys()):
            if guild_id in role_mapping[role_name]:
                del role_mapping[role_name][guild_id]
                # 如果这个身份组没有其他服务器映射了，删除整个映射
                if not role_mapping[role_name]:
                    del role_mapping[role_name]
        
        self._config_cache = config
        self._save_config()
        
        await interaction.edit_original_response(content="✅ 已从同步列表中删除当前服务器")

    @sync_manage.command(name="身份组", description="将身份组添加到同步列表")
    @is_admin()
    @app_commands.describe(名字="身份组名字", role="身份组")
    async def add_role_mapping(self, interaction: discord.Interaction, 名字: str, role: discord.Role):
        """添加身份组映射"""
        guild_id = str(interaction.guild.id)
        
        config = self.config
        if guild_id not in config.get("servers", {}):
            await interaction.response.send_message("❌ 当前服务器未在同步列表中，请先添加服务器", ephemeral=True)
            return

        if "role_mapping" not in config:
            config["role_mapping"] = {}
            
        if 名字 not in config["role_mapping"]:
            config["role_mapping"][名字] = {}
            
        # 添加到服务器配置
        config["servers"][guild_id]["roles"][名字] = role.id
        
        # 添加到全局映射
        config["role_mapping"][名字][guild_id] = role.id
        
        self._config_cache = config
        self._save_config()
        
        await interaction.response.send_message(f"✅ 已将身份组 {role.mention} 添加到同步列表，名称: {名字}", ephemeral=True)

    @sync_manage.command(name="处罚同步", description="开启或关闭此服务器的处罚同步")
    @is_admin()
    @app_commands.describe(状态="开启或关闭")
    @app_commands.choices(状态=[
        app_commands.Choice(name="开", value="on"),
        app_commands.Choice(name="关", value="off"),
    ])
    async def toggle_punishment_sync(self, interaction: discord.Interaction, 状态: str):
        """开启或关闭处罚同步"""
        guild_id = str(interaction.guild.id)
        
        config = self.config
        if guild_id not in config.get("servers", {}):
            await interaction.response.send_message("❌ 当前服务器未在同步列表中，请先添加服务器", ephemeral=True)
            return

        enabled = 状态 == "on"
        config["servers"][guild_id]["punishment_sync"] = enabled
        
        if enabled and "punishment_sync" not in config:
            config["punishment_sync"] = {"enabled": True, "servers": {}}
            
        if enabled:
            config["punishment_sync"]["servers"][guild_id] = True
        elif guild_id in config.get("punishment_sync", {}).get("servers", {}):
            del config["punishment_sync"]["servers"][guild_id]
        
        self._config_cache = config
        self._save_config()
        
        status_text = "开启" if enabled else "关闭"
        await interaction.response.send_message(f"✅ 已{status_text}此服务器的处罚同步", ephemeral=True)

    @sync_manage.command(name="处罚公示频道", description="设置此服务器的处罚公示频道")
    @is_admin()
    @app_commands.describe(频道="处罚公示频道")
    async def set_punishment_announce_channel(self, interaction: discord.Interaction, 频道: discord.TextChannel):
        """设置处罚公示频道"""
        guild_id = str(interaction.guild.id)
        
        config = self.config
        if guild_id not in config.get("servers", {}):
            await interaction.response.send_message("❌ 当前服务器未在同步列表中，请先添加服务器", ephemeral=True)
            return

        config["servers"][guild_id]["punishment_announce_channel"] = 频道.id
        
        self._config_cache = config
        self._save_config()
        
        await interaction.response.send_message(f"✅ 已设置处罚公示频道为 {频道.mention}", ephemeral=True)

    @sync_manage.command(name="处罚确认频道", description="设置此服务器的处罚同步确认频道")
    @is_admin()
    @app_commands.describe(频道="处罚确认频道")
    async def set_punishment_confirm_channel(self, interaction: discord.Interaction, 频道: discord.TextChannel):
        """设置处罚确认频道"""
        guild_id = str(interaction.guild.id)
        
        config = self.config
        if guild_id not in config.get("servers", {}):
            await interaction.response.send_message("❌ 当前服务器未在同步列表中，请先添加服务器", ephemeral=True)
            return

        config["servers"][guild_id]["punishment_confirm_channel"] = 频道.id
        
        self._config_cache = config
        self._save_config()
        
        await interaction.response.send_message(f"✅ 已设置处罚确认频道为 {频道.mention}", ephemeral=True)

    # ====== 提供给其他模块的身份组操作函数 ======
    async def sync_add_role(self, guild: discord.Guild, member: discord.Member, role: discord.Role, reason: str = None):
        """同步添加身份组到所有配置的服务器"""
        if not self.config.get("enabled", False):
            # 同步未启用，使用普通方式
            await member.add_roles(role, reason=reason)
            return

        guild_id = str(guild.id)
        if guild_id not in self.config.get("servers", {}):
            # 当前服务器未配置同步，使用普通方式
            await member.add_roles(role, reason=reason)
            return

        # 先在当前服务器添加身份组
        await member.add_roles(role, reason=reason)

        # 检查是否有映射的身份组
        role_name = role.name
        role_mapping = self.config.get("role_mapping", {})
        
        if role_name not in role_mapping:
            return  # 没有映射配置

        # 同步到其他服务器
        servers_config = self.config.get("servers", {})
        
        for target_guild_id, server_config in servers_config.items():
            if target_guild_id == guild_id:  # 跳过当前服务器
                continue
                
            target_guild = self.bot.get_guild(int(target_guild_id))
            if not target_guild:
                continue
                
            target_member = target_guild.get_member(member.id)
            if not target_member:
                continue

            # 获取目标身份组
            role_configs = server_config.get("roles", {})
            if role_name in role_configs:
                target_role_id = role_configs[role_name]
                target_role = target_guild.get_role(target_role_id)
                
                if target_role:
                    try:
                        if target_role not in target_member.roles:
                            await target_member.add_roles(target_role, reason=f"身份组同步: {reason}")
                    except Exception as e:
                        if self.logger:
                            self.logger.error(f"同步添加身份组失败 {target_guild.name}: {e}")

    async def sync_remove_role(self, guild: discord.Guild, member: discord.Member, role: discord.Role, reason: str = None):
        """同步移除身份组到所有配置的服务器"""
        if not self.config.get("enabled", False):
            # 同步未启用，使用普通方式
            await member.remove_roles(role, reason=reason)
            return

        guild_id = str(guild.id)
        if guild_id not in self.config.get("servers", {}):
            # 当前服务器未配置同步，使用普通方式
            await member.remove_roles(role, reason=reason)
            return

        # 先在当前服务器移除身份组
        await member.remove_roles(role, reason=reason)

        # 检查是否有映射的身份组
        role_name = role.name
        role_mapping = self.config.get("role_mapping", {})
        
        if role_name not in role_mapping:
            return  # 没有映射配置

        # 同步到其他服务器
        servers_config = self.config.get("servers", {})
        
        for target_guild_id, server_config in servers_config.items():
            if target_guild_id == guild_id:  # 跳过当前服务器
                continue
                
            target_guild = self.bot.get_guild(int(target_guild_id))
            if not target_guild:
                continue
                
            target_member = target_guild.get_member(member.id)
            if not target_member:
                continue

            # 获取目标身份组
            role_configs = server_config.get("roles", {})
            if role_name in role_configs:
                target_role_id = role_configs[role_name]
                target_role = target_guild.get_role(target_role_id)
                
                if target_role:
                    try:
                        if target_role in target_member.roles:
                            await target_member.remove_roles(target_role, reason=f"身份组同步: {reason}")
                    except Exception as e:
                        if self.logger:
                            self.logger.error(f"同步移除身份组失败 {target_guild.name}: {e}")

    # ====== 提供给其他模块的处罚操作函数 ======
    async def sync_punishment(self, guild: discord.Guild, punishment_type: str, member: discord.Member, 
                            moderator: discord.Member, reason: str = None, duration: int = None, 
                            warn_days: int = 0, punishment_id: str = None, img: discord.Attachment = None):
        """同步处罚到其他服务器"""
        if not self.config.get("punishment_sync", {}).get("enabled", False):
            return  # 处罚同步未启用

        guild_id = str(guild.id)
        if not self.config.get("servers", {}).get(guild_id, {}).get("punishment_sync", False):
            return  # 当前服务器未启用处罚同步

        # 创建处罚记录
        punishment_record = {
            "id": punishment_id or uuid.uuid4().hex[:8],
            "type": punishment_type,
            "source_guild": guild.id,
            "source_guild_name": guild.name,
            "user_id": member.id,
            "user_name": f"{member.display_name}#{member.discriminator}",
            "moderator_id": moderator.id,
            "moderator_name": f"{moderator.display_name}#{moderator.discriminator}",
            "reason": reason,
            "duration": duration,
            "warn_days": warn_days,
            "img_url": img.url if img else None,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

        # 广播到其他服务器的确认频道
        servers_config = self.config.get("servers", {})
        
        for target_guild_id, server_config in servers_config.items():
            if target_guild_id == guild_id:  # 跳过当前服务器
                continue
                
            if not server_config.get("punishment_sync", False):
                continue  # 目标服务器未启用处罚同步
                
            target_guild = self.bot.get_guild(int(target_guild_id))
            if not target_guild:
                continue

            confirm_channel_id = server_config.get("punishment_confirm_channel")
            if not confirm_channel_id:
                continue
                
            confirm_channel = target_guild.get_channel(confirm_channel_id)
            if not confirm_channel:
                continue

            # 创建确认embed
            embed = discord.Embed(
                title="🚨 处罚同步确认",
                color=discord.Color.orange(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            
            embed.add_field(name="来源服务器", value=guild.name, inline=True)
            embed.add_field(name="处罚类型", value=punishment_type, inline=True)
            embed.add_field(name="用户", value=f"{member.mention} ({member.display_name})", inline=True)
            embed.add_field(name="管理员", value=f"{moderator.mention} ({moderator.display_name})", inline=True)
            embed.add_field(name="原因", value=reason or "未提供", inline=False)
            
            if duration:
                if punishment_type == "mute":
                    duration_text = f"{duration // 60}分钟" if duration < 3600 else f"{duration // 3600}小时"
                    embed.add_field(name="禁言时长", value=duration_text, inline=True)
            
            if warn_days > 0:
                embed.add_field(name="警告天数", value=f"{warn_days}天", inline=True)
            
            # 添加图片
            if img:
                embed.set_image(url=img.url)
                
            embed.set_footer(text=f"处罚ID: {punishment_record['id']}")

            # 创建确认按钮
            view = PunishmentConfirmView(punishment_record, target_guild_id)
            
            try:
                await confirm_channel.send(embed=embed, view=view)
            except Exception as e:
                if self.logger:
                    self.logger.error(f"发送处罚确认消息失败 {target_guild.name}: {e}")

    async def sync_revoke_punishment(self, guild: discord.Guild, punishment_id: str, moderator: discord.Member, reason: str = None):
        """同步撤销处罚"""
        if not self.config.get("punishment_sync", {}).get("enabled", False):
            return  # 处罚同步未启用

        guild_id = str(guild.id)
        if not self.config.get("servers", {}).get(guild_id, {}).get("punishment_sync", False):
            return  # 当前服务器未启用处罚同步

        # 直接同步撤销到其他服务器（不需确认）
        servers_config = self.config.get("servers", {})
        
        for target_guild_id, server_config in servers_config.items():
            if target_guild_id == guild_id:  # 跳过当前服务器
                continue
                
            if not server_config.get("punishment_sync", False):
                continue  # 目标服务器未启用处罚同步
                
            target_guild = self.bot.get_guild(int(target_guild_id))
            if not target_guild:
                continue

            # 尝试撤销处罚
            await self._revoke_punishment_in_guild(target_guild, punishment_id, moderator, reason)

    async def _revoke_punishment_in_guild(self, guild: discord.Guild, punishment_id: str, moderator: discord.Member, reason: str = None):
        """在指定服务器撤销处罚"""
        # 查找处罚记录
        punish_dir = pathlib.Path("data") / "punish" / str(guild.id)
        if not punish_dir.exists():
            return

        record_file = punish_dir / f"{punishment_id}.json"
        if not record_file.exists():
            return

        try:
            with open(record_file, "r", encoding="utf-8") as f:
                record = json.load(f)

            user_id = int(record["user_id"])
            user_obj = guild.get_member(user_id)
            
            if not user_obj:
                try:
                    user_obj = await guild.fetch_member(user_id)
                except:
                    user_obj = None

            if record["type"] == "mute" and user_obj:
                try:
                    await user_obj.timeout(None, reason=f"同步撤销处罚: {reason}")
                    # 移除警告身份组
                    if record.get("warn_days", 0) > 0:

                        # 从多服务器配置获取warned_role_id
                        guild_configs = getattr(self.bot, 'config', {}).get('guild_configs', {})
                        guild_config = guild_configs.get(str(guild.id), {})
                        warned_role_id = guild_config.get("warned_role_id")

                        if warned_role_id:
                            warned_role = guild.get_role(int(warned_role_id))
                            if warned_role and warned_role in user_obj.roles:
                                await user_obj.remove_roles(warned_role, reason=f"同步撤销处罚")
                except discord.Forbidden:
                    pass
            elif record["type"] == "ban":
                try:
                    await guild.unban(discord.Object(id=user_id), reason=f"同步撤销处罚: {reason}")
                except discord.Forbidden:
                    pass

            # 删除记录文件
            record_file.unlink(missing_ok=True)

            # 发布撤销公告
            guild_config = self.config.get("servers", {}).get(str(guild.id), {})
            announce_channel_id = guild_config.get("punishment_announce_channel")
            if announce_channel_id:
                announce_channel = guild.get_channel(announce_channel_id)
                if announce_channel:
                    embed = discord.Embed(title="🔓 撤销处罚", color=discord.Color.green())
                    embed.add_field(name="处罚ID", value=punishment_id)
                    embed.add_field(name="用户", value=f"<@{user_id}>")
                    embed.add_field(name="操作者", value=moderator.mention)
                    embed.add_field(name="原因", value=reason or "同步撤销", inline=False)
                    await announce_channel.send(embed=embed)

        except Exception as e:
            if self.logger:
                self.logger.error(f"撤销处罚失败 {guild.name}: {e}")


class PunishmentConfirmView(discord.ui.View):
    """处罚确认视图"""
    
    def __init__(self, punishment_record: dict, target_guild_id: str):
        super().__init__(timeout=86400)  # 24小时超时
        self.punishment_record = punishment_record
        self.target_guild_id = target_guild_id
        
    @discord.ui.button(label="确认执行", style=discord.ButtonStyle.danger, emoji="✅")
    async def confirm_punishment(self, interaction: discord.Interaction, button: discord.ui.Button):
        """确认执行处罚"""
        # 检查权限（需要管理员权限）
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 只有管理员可以确认处罚", ephemeral=True)
            return

        await interaction.response.defer()

        guild = interaction.guild
        punishment_type = self.punishment_record["type"]
        user_id = self.punishment_record["user_id"]
        reason = self.punishment_record["reason"]
        duration = self.punishment_record.get("duration")
        warn_days = self.punishment_record.get("warn_days", 0)
        punishment_id = self.punishment_record["id"]

        try:
            # 获取用户
            user_obj = guild.get_member(user_id)
            if not user_obj:
                try:
                    user_obj = await guild.fetch_member(user_id)
                except:
                    await interaction.followup.send("❌ 无法找到用户", ephemeral=True)
                    return

            # 执行处罚
            if punishment_type == "mute":
                if duration and duration > 0:
                    await user_obj.timeout(datetime.timedelta(seconds=duration), reason=f"同步处罚: {reason}")
                
                # 添加警告身份组
                if warn_days > 0:
                    sync_cog = interaction.client.get_cog("ServerSyncCommands")
                    if sync_cog:
                        guild_configs = getattr(sync_cog.bot, 'config', {}).get('guild_configs', {})
                        guild_config = guild_configs.get(str(guild.id), {})
                        warned_role_id = guild_config.get("warned_role_id")
                        
                        if warned_role_id:
                            warned_role = guild.get_role(int(warned_role_id))
                            if warned_role:
                                await user_obj.add_roles(warned_role, reason=f"同步处罚警告 {warn_days} 天")

            elif punishment_type == "ban":
                await guild.ban(user_obj, reason=f"同步处罚: {reason}", delete_message_days=0)

            # 保存处罚记录
            punish_dir = pathlib.Path("data") / "punish" / str(guild.id)
            punish_dir.mkdir(parents=True, exist_ok=True)
            
            record_file = punish_dir / f"{punishment_id}.json"
            with open(record_file, "w", encoding="utf-8") as f:
                json.dump(self.punishment_record, f, ensure_ascii=False, indent=2)

            # 发布公告
            sync_cog = interaction.client.get_cog("ServerSyncCommands")
            if sync_cog:
                guild_config = sync_cog.config.get("servers", {}).get(str(guild.id), {})
                announce_channel_id = guild_config.get("punishment_announce_channel")
                if announce_channel_id:
                    announce_channel = guild.get_channel(announce_channel_id)
                    if announce_channel:
                        embed = discord.Embed(
                            title="🚨 同步处罚执行",
                            color=discord.Color.red(),
                            timestamp=datetime.datetime.now(datetime.timezone.utc)
                        )
                        embed.add_field(name="来源服务器", value=self.punishment_record["source_guild_name"], inline=True)
                        embed.add_field(name="处罚类型", value=punishment_type, inline=True)
                        embed.add_field(name="用户", value=user_obj.mention, inline=True)
                        embed.add_field(name="原管理员", value=self.punishment_record["moderator_name"], inline=True)
                        embed.add_field(name="确认管理员", value=interaction.user.mention, inline=True)
                        embed.add_field(name="原因", value=reason or "未提供", inline=False)
                        
                        # 添加图片
                        img_url = self.punishment_record.get("img_url")
                        if img_url:
                            embed.set_image(url=img_url)
                            
                        embed.set_footer(text=f"处罚ID: {punishment_id}")
                        await announce_channel.send(embed=embed)

            # 更新确认消息
            embed = discord.Embed(title="✅ 处罚已确认执行", color=discord.Color.green())
            embed.add_field(name="确认者", value=interaction.user.mention)
            embed.add_field(name="执行时间", value=discord.utils.format_dt(datetime.datetime.now(), "F"))
            
            # 禁用按钮
            for item in self.children:
                item.disabled = True
                
            await interaction.edit_original_response(embed=embed, view=self)
            
        except Exception as e:
            await interaction.followup.send(f"❌ 执行处罚失败: {e}", ephemeral=True)

    @discord.ui.button(label="拒绝执行", style=discord.ButtonStyle.secondary, emoji="❌")
    async def reject_punishment(self, interaction: discord.Interaction, button: discord.ui.Button):
        """拒绝执行处罚"""
        # 检查权限
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 只有管理员可以拒绝处罚", ephemeral=True)
            return

        embed = discord.Embed(title="❌ 处罚已拒绝", color=discord.Color.red())
        embed.add_field(name="拒绝者", value=interaction.user.mention)
        embed.add_field(name="拒绝时间", value=discord.utils.format_dt(datetime.datetime.now(), "F"))
        
        # 禁用按钮
        for item in self.children:
            item.disabled = True
            
        await interaction.response.edit_message(embed=embed, view=self)


async def setup(bot):
    await bot.add_cog(ServerSyncCommands(bot)) 

