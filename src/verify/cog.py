import asyncio
import json
import random
import pathlib
import datetime
import uuid
from typing import List, Dict, Optional

import discord
from discord.ext import commands
from discord import app_commands

from src.utils import dm
from src.utils.auth import is_admin
from src.utils.confirm_view import confirm_view
from src.utils.config_helper import get_config_value, get_config_for_guild


class VerifyCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger
        self.name = "答题验证"
        self.questions = []
        # 初始化配置缓存
        self._config_cache = {}
        self._config_cache_mtime = None
        # 自动升级功能状态
        self.auto_upgrade_enabled = True
        # 活跃的答题会话
        self.active_quiz_sessions = {}
        self.active_quiz_sessions_by_user = {}

    @property
    def config(self):
        """读取配置文件并缓存，只有在文件修改后重新加载"""
        try:
            path = pathlib.Path('config.json')
            mtime = path.stat().st_mtime
            if self._config_cache_mtime != mtime:
                with open(path, 'r', encoding='utf-8') as f:
                    self._config_cache = json.load(f)
                self._config_cache_mtime = mtime
            return self._config_cache
        except Exception as e:
            if self.logger:
                self.logger.error(f"加载配置文件失败: {e}")
            return {}
    
    def get_guild_config(self, key: str, guild_id: Optional[int] = None, default=None):
        """获取服务器特定配置值"""
        return get_config_value(key, guild_id, default)

    def _load_questions(self):
        """加载题目库"""
        try:
            questions_path = pathlib.Path("config/verify/questions.json")
            if questions_path.exists():
                with open(questions_path, 'r', encoding='utf-8') as f:
                    self.questions = json.load(f)
                if self.logger:
                    self.logger.info(f"已加载 {len(self.questions)} 道题目")
            else:
                if self.logger:
                    self.logger.error("题目文件不存在")
        except Exception as e:
            if self.logger:
                self.logger.error(f"加载题目失败: {e}")

    def _save_user_attempt(self, guild_id: int, user_id: int, success: bool):
        """保存用户答题记录"""
        data_dir = pathlib.Path("data") / "verify" / str(guild_id)
        data_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = data_dir / f"{user_id}.json"
        
        # 读取现有记录
        if file_path.exists():
            with open(file_path, 'r', encoding='utf-8') as f:
                user_data = json.load(f)
        else:
            user_data = {
                "attempts": [],
                "last_success": None,
                "timeout_until": None,  # 保持向后兼容
                "quiz_cooldown_until": None  # 新增：答题冷却时间
            }
        
        # 添加新记录
        attempt_record = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "success": success
        }
        user_data["attempts"].append(attempt_record)
        
        if success:
            user_data["last_success"] = attempt_record["timestamp"]
        
        # 保存记录
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)
        
        return user_data

    def _get_user_data(self, guild_id: int, user_id: int) -> Dict:
        """获取用户数据"""
        data_dir = pathlib.Path("data") / "verify" / str(guild_id)
        file_path = data_dir / f"{user_id}.json"
        
        if file_path.exists():
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        return {
            "attempts": [],
            "last_success": None,
            "timeout_until": None,  # 保持向后兼容
            "quiz_cooldown_until": None  # 新增：答题冷却时间
        }

    def _set_user_timeout(self, guild_id: int, user_id: int, minutes: int):
        """设置用户禁言时间（保持向后兼容）"""
        data_dir = pathlib.Path("data") / "verify" / str(guild_id)
        data_dir.mkdir(parents=True, exist_ok=True)
        file_path = data_dir / f"{user_id}.json"
        
        user_data = self._get_user_data(guild_id, user_id)
        timeout_until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
        user_data["timeout_until"] = timeout_until.isoformat()
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)

    def _set_user_quiz_cooldown(self, guild_id: int, user_id: int, minutes: int):
        """设置用户答题冷却时间"""
        data_dir = pathlib.Path("data") / "verify" / str(guild_id)
        data_dir.mkdir(parents=True, exist_ok=True)
        file_path = data_dir / f"{user_id}.json"
        
        user_data = self._get_user_data(guild_id, user_id)
        cooldown_until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
        user_data["quiz_cooldown_until"] = cooldown_until.isoformat()
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)

    def _is_user_in_timeout(self, guild_id: int, user_id: int) -> bool:
        """检查用户是否在禁言期间（保持向后兼容）"""
        user_data = self._get_user_data(guild_id, user_id)
        timeout_until = user_data.get("timeout_until")
        
        if timeout_until:
            timeout_time = datetime.datetime.fromisoformat(timeout_until)
            return datetime.datetime.now(datetime.timezone.utc) < timeout_time
        
        return False

    def _is_user_in_quiz_cooldown(self, guild_id: int, user_id: int) -> bool:
        """检查用户是否在答题冷却期间"""
        user_data = self._get_user_data(guild_id, user_id)
        cooldown_until = user_data.get("quiz_cooldown_until")
        
        if cooldown_until:
            cooldown_time = datetime.datetime.fromisoformat(cooldown_until)
            return datetime.datetime.now(datetime.timezone.utc) < cooldown_time
        
        return False

    def _get_quiz_cooldown_remaining(self, guild_id: int, user_id: int) -> Optional[int]:
        """获取答题冷却剩余时间（分钟）"""
        user_data = self._get_user_data(guild_id, user_id)
        cooldown_until = user_data.get("quiz_cooldown_until")
        
        if cooldown_until:
            cooldown_time = datetime.datetime.fromisoformat(cooldown_until)
            now = datetime.datetime.now(datetime.timezone.utc)
            if now < cooldown_time:
                remaining_seconds = (cooldown_time - now).total_seconds()
                return int(remaining_seconds / 60) + 1  # 向上取整
        
        return None

    def _get_recent_failed_attempts(self, guild_id: int, user_id: int) -> int:
        """获取最近失败次数"""
        user_data = self._get_user_data(guild_id, user_id)
        now = datetime.datetime.now(datetime.timezone.utc)
        reset_hours = self.config.get("attempt_reset_hours", 24)
        cutoff_time = now - datetime.timedelta(hours=reset_hours)
        
        recent_failures = 0
        for attempt in reversed(user_data.get("attempts", [])):
            attempt_time = datetime.datetime.fromisoformat(attempt["timestamp"])
            if attempt_time < cutoff_time:
                break
            if not attempt["success"]:
                recent_failures += 1
            else:
                break  # 遇到成功记录就停止计数
        
        return recent_failures

    def _create_quiz_session(self, guild_id: int, user_id: int, questions: List[Dict], language: str) -> str:
        """创建答题会话"""
        session_id = str(uuid.uuid4())
        session_data = {
            "session_id": session_id,
            "guild_id": guild_id,
            "user_id": user_id,
            "questions": questions,
            "language": language,
            "current_question": 0,
            "answers": [None] * len(questions),
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        self.active_quiz_sessions[session_id] = session_data
        self.active_quiz_sessions_by_user[user_id] = session_data
        return session_id

    def _get_quiz_session(self, session_id: str) -> Optional[Dict]:
        """获取答题会话"""
        if session_id in self.active_quiz_sessions_by_user:
            return self.active_quiz_sessions_by_user[session_id]
        return self.active_quiz_sessions.get(session_id)
    
    def _get_quiz_id_by_user(self, user_id: int) -> Optional[str]:
        """通过用户ID获取答题会话ID"""
        session = self.active_quiz_sessions_by_user.get(user_id)
        if session:
            return session["session_id"]
        return None

    def _update_quiz_session(self, session_id: str, **kwargs):
        """更新答题会话"""
        if session_id in self.active_quiz_sessions:
            self.active_quiz_sessions[session_id].update(kwargs)

    def _clear_quiz_session(self, session_id: str):
        """清除答题会话"""
        if session_id in self.active_quiz_sessions:
            del self.active_quiz_sessions[session_id]

    def _clear_user_quiz_sessions(self, guild_id: int, user_id: int):
        """清除用户的所有答题会话"""
        to_remove = []
        for session_id, session in self.active_quiz_sessions.items():
            if session["guild_id"] == guild_id and session["user_id"] == user_id:
                to_remove.append(session_id)
        if user_id in self.active_quiz_sessions_by_user:
            del self.active_quiz_sessions_by_user[user_id]
        
        for session_id in to_remove:
            del self.active_quiz_sessions[session_id]

    async def _auto_upgrade_task(self):
        """自动升级任务 - 将缓冲区用户升级到已验证用户"""
        while True:
            try:
                # 每小时检查一次
                await asyncio.sleep(60 * 60)
                
                if not self.auto_upgrade_enabled:
                    continue
                    
                # 检查所有服务器
                for guild in self.bot.guilds:
                    await self._process_auto_upgrade(guild)
                    
            except Exception as e:
                if self.logger:
                    self.logger.error(f"自动升级任务错误: {e}")
                continue

    async def _process_auto_upgrade(self, guild: discord.Guild):
        """处理单个服务器的自动升级"""
        try:
            # 使用服务器特定配置
            buffer_role_id = self.get_guild_config("buffer_role_id", guild.id)
            verified_role_id = self.get_guild_config("verified_role_id", guild.id)
            upper_buffer_role_id = self.get_guild_config("upper_buffer_role_id", guild.id)
            
            if not buffer_role_id or not verified_role_id:
                return
                
            if buffer_role_id == "请填入缓冲区身份组ID" or verified_role_id == "请填入已验证身份组ID":
                return
                
            buffer_role = guild.get_role(int(buffer_role_id))
            verified_role = guild.get_role(int(verified_role_id))
            upper_buffer_role = guild.get_role(int(upper_buffer_role_id))
            
            if not buffer_role or not verified_role:
                return
                
            # 获取拥有缓冲区身份组的成员
            eligible_members = []
            current_time = datetime.datetime.now(datetime.timezone.utc)


            upgrade_threshold = datetime.timedelta(days=3)
            for member in upper_buffer_role.members:

                # 检查用户的最后成功答题时间
                user_data = self._get_user_data(guild.id, member.id)
                last_success = user_data.get("last_success")
                
                if last_success:
                    try:
                        success_time = datetime.datetime.fromisoformat(last_success)
                        if current_time - success_time >= upgrade_threshold:
                            eligible_members.append((member, success_time))
                    except Exception as e:
                        if self.logger:
                            self.logger.warning(f"解析用户 {member.id} 成功时间失败: {e}")
                        continue
                else:
                    # 在此bot上线前就通过答题的成员，直接升级
                    eligible_members.append((member, current_time))
            upgrade_threshold = datetime.timedelta(days=5)  # 5天后自动升级
            
            for member in buffer_role.members:
                if upper_buffer_role in member.roles:
                    continue # 刚才已经检查过，跳过
                    
                # 检查用户的最后成功答题时间
                user_data = self._get_user_data(guild.id, member.id)
                last_success = user_data.get("last_success")
                
                if last_success:
                    try:
                        success_time = datetime.datetime.fromisoformat(last_success)
                        if current_time - success_time >= upgrade_threshold:
                            eligible_members.append((member, success_time))
                    except Exception as e:
                        if self.logger:
                            self.logger.warning(f"解析用户 {member.id} 成功时间失败: {e}")
                        continue
                else:
                    # 在此bot上线前就通过答题的成员，直接升级
                    eligible_members.append((member, current_time))
            # 升级符合条件的成员
            for member, success_time in eligible_members:
                try:
                    # 检查是否启用同步模块
                    sync_cog = self.bot.get_cog("ServerSyncCommands")
                    if sync_cog:
                        if verified_role is not None and verified_role not in member.roles:
                            await sync_cog.sync_add_role(guild, member, verified_role, "自动升级：缓冲区期满")
                        if buffer_role is not None and buffer_role in member.roles:
                            await sync_cog.sync_remove_role(guild, member, buffer_role, "自动升级：缓冲区期满")
                        if upper_buffer_role is not None and upper_buffer_role in member.roles:
                            await sync_cog.sync_remove_role(guild, member, upper_buffer_role, "自动升级：缓冲区期满")
                    else:
                        if verified_role is not None and verified_role not in member.roles:
                            await member.add_roles(verified_role, reason="自动升级：缓冲区期满")
                        if buffer_role is not None and buffer_role in member.roles:
                            await member.remove_roles(buffer_role, reason="自动升级：缓冲区期满")
                        if upper_buffer_role is not None and upper_buffer_role in member.roles:
                            await member.remove_roles(upper_buffer_role, reason="自动升级：缓冲区期满")
                    
                    if self.logger:
                        self.logger.info(f"自动升级成功: {member} (ID: {member.id}) 在服务器 {guild.name}")
                    
                    # 发送私聊通知
                    # 会导致bot被标记为垃圾邮件发送者
                    '''
                    try:
                        embed = discord.Embed(
                            title="🎉 自动升级通知", 
                            description="恭喜！您已自动从缓冲区升级为正式成员，现在可以正常发言了！",
                            color=discord.Color.green()
                        )
                        embed.add_field(name="升级时间", value=current_time.strftime("%Y-%m-%d %H:%M:%S UTC"))
                        embed.add_field(name="服务器", value=guild.name)
                        await member.send(embed=embed)
                    except discord.Forbidden:
                        pass  # 无法发送私聊，跳过
                    '''
                        
                except discord.Forbidden:
                    if self.logger:
                        self.logger.warning(f"无权限升级用户: {member} (ID: {member.id})")
                    continue
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"升级用户失败: {member} (ID: {member.id}), 错误: {e}")
                    continue
                    
        except Exception as e:
            if self.logger:
                self.logger.error(f"处理服务器 {guild.name} 自动升级失败: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        self._load_questions()
        if self.logger:
            self.logger.info("答题验证模块已加载")
        # 注册持久化按钮视图
        self.bot.add_view(VerifyButtonView(self, "zh_cn"))
        self.bot.add_view(VerifyButtonView(self, "en_us"))
        # 注册答题视图（在重启后重新加载会话）
        # 注意：重启后会话会丢失，这是预期的行为
        # 启动自动升级任务
        self.auto_upgrade_task = asyncio.create_task(self._auto_upgrade_task())
        if self.logger:
            self.logger.info("自动升级任务已启动")

    async def on_disable(self):
        self.active_quiz_sessions.clear()
        self.auto_upgrade_task.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # 检查是否在指定频道
        if not message.guild:
            return
            
        channel_id = self.config.get("channel_id")
        if not channel_id or message.channel.id != channel_id:
            return
            
        # 如果不是管理员则删除消息
        try:
            if not message.author.guild_permissions.administrator:
                await message.delete()
        except Exception:
            pass

    verify = app_commands.Group(name="验证", description="答题验证相关命令")

    @verify.command(name="创建答题按钮", description="在指定频道创建答题引导消息和按钮")
    @is_admin()
    @app_commands.describe(channel="要创建按钮的频道")
    @app_commands.rename(channel="频道")
    async def create_verify_button(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not self.questions:
            await interaction.response.send_message("❌ 题目未正确加载", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # 创建中文embed
        zh_embed = discord.Embed(
            title="🎯 答题验证",
            description="\n".join([
                "阅读上述规则后，点击下方按钮即可开始答题。",
                "若答题时间过久导致按钮失效，可重新点击按钮获取新的题目。"
            ]),
            color=discord.Color.blue()
        )

        # 创建英文embed
        en_embed = discord.Embed(
            title="🎯 Quiz Verification",
            description="\n".join([
                "After reading the rules, please click the button below to start the quiz.",
                "If the button fails due to long quiz time, you can click the button again to get new questions."
            ]),
            color=discord.Color.green()
        )

        # 创建按钮视图
        view_zh = VerifyButtonView(self, "zh_cn")
        view_en = VerifyButtonView(self, "en_us")

        # 发送消息
        await channel.send(embed=zh_embed, view=view_zh)
        await channel.send(embed=en_embed, view=view_en)

        await interaction.followup.send(f"✅ 已在 {channel.mention} 创建答题按钮", ephemeral=True)
        if self.logger:
            self.logger.info(f"用户 {interaction.user} 在 {channel.mention} 创建答题按钮")

    @verify.command(name="自动升级状态", description="查看自动升级功能状态")
    @is_admin()
    async def auto_upgrade_status(self, interaction: discord.Interaction):
        """查看自动升级功能状态"""
        status = "启用" if self.auto_upgrade_enabled else "暂停"
        status_color = discord.Color.green() if self.auto_upgrade_enabled else discord.Color.red()
        
        embed = discord.Embed(
            title="🔄 自动升级功能状态",
            description=f"当前状态：**{status}**",
            color=status_color
        )
        
        if self.auto_upgrade_enabled:
            embed.add_field(
                name="📋 功能说明",
                value="自动升级功能已启用，系统会每小时检查缓冲区用户，将答题成功3天后的用户自动升级为正式成员。",
                inline=False
            )
        else:
            embed.add_field(
                name="⚠️ 功能暂停",
                value="自动升级功能已暂停，用户不会被自动升级。可使用 `/验证 恢复自动升级` 命令重新启用。",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @verify.command(name="暂停自动升级", description="暂停自动升级功能")
    @is_admin()
    async def pause_auto_upgrade(self, interaction: discord.Interaction):
        """暂停自动升级功能"""
        if not self.auto_upgrade_enabled:
            await interaction.response.send_message("❌ 自动升级功能已经是暂停状态", ephemeral=True)
            return
            
        self.auto_upgrade_enabled = False
        embed = discord.Embed(
            title="⏸️ 自动升级功能已暂停",
            description="自动升级功能已被暂停，用户将不会被自动升级。",
            color=discord.Color.orange()
        )
        embed.add_field(
            name="💡 提示",
            value="如需重新启用，请使用 `/验证 恢复自动升级` 命令",
            inline=False
        )
        
        if self.logger:
            self.logger.info(f"自动升级功能已被 {interaction.user} 暂停")
            
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @verify.command(name="恢复自动升级", description="恢复自动升级功能")
    @is_admin()
    async def resume_auto_upgrade(self, interaction: discord.Interaction):
        """恢复自动升级功能"""
        if self.auto_upgrade_enabled:
            await interaction.response.send_message("❌ 自动升级功能已经在运行中", ephemeral=True)
            return
            
        self.auto_upgrade_enabled = True
        embed = discord.Embed(
            title="▶️ 自动升级功能已恢复",
            description="自动升级功能已重新启用，系统将继续自动升级符合条件的用户。",
            color=discord.Color.green()
        )
        embed.add_field(
            name="📋 功能说明",
            value="系统会每小时检查缓冲区用户，将答题成功3天后的用户自动升级为正式成员。",
            inline=False
        )
        
        if self.logger:
            self.logger.info(f"自动升级功能已被 {interaction.user} 恢复")
            
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @verify.command(name="查询成员状态", description="查询指定成员的答题状态和缓冲区剩余时间")
    @app_commands.describe(member="要查询的成员")
    @app_commands.rename(member="成员")
    async def query_member_status(self, interaction: discord.Interaction, member: discord.Member):
        """查询指定成员的答题状态和缓冲区剩余时间"""
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("❌ 只能在服务器中使用此命令", ephemeral=True)
            return
        
        # 获取用户数据
        user_data = self._get_user_data(guild.id, member.id)
        last_success = user_data.get("last_success")
        
        # 获取身份组配置
        buffer_role_id = self.get_guild_config("buffer_role_id", guild.id)
        verified_role_id = self.get_guild_config("verified_role_id", guild.id)
        upper_buffer_role_id = self.get_guild_config("upper_buffer_role_id", guild.id)
        
        buffer_role = guild.get_role(int(buffer_role_id)) if buffer_role_id and buffer_role_id != "请填入缓冲区身份组ID" else None
        verified_role = guild.get_role(int(verified_role_id)) if verified_role_id and verified_role_id != "请填入已验证身份组ID" else None
        upper_buffer_role = guild.get_role(int(upper_buffer_role_id)) if upper_buffer_role_id else None
        
        # 创建embed
        embed = discord.Embed(
            title=f"📋 成员状态查询",
            description=f"查询成员：{member.mention}",
            color=discord.Color.blue()
        )
        
        # 显示当前身份组状态
        role_status = []
        if verified_role and verified_role in member.roles:
            role_status.append(f"✅ {verified_role.name}")
        if upper_buffer_role and upper_buffer_role in member.roles:
            role_status.append(f"🟡 {upper_buffer_role.name}（高级缓冲区）")
        if buffer_role and buffer_role in member.roles:
            role_status.append(f"🟠 {buffer_role.name}（缓冲区）")
        
        if role_status:
            embed.add_field(name="🎭 当前身份组", value="\n".join(role_status), inline=False)
        else:
            embed.add_field(name="🎭 当前身份组", value="无相关身份组", inline=False)
        
        # 显示答题成功时间和缓冲区剩余时间
        if last_success:
            try:
                success_time = datetime.datetime.fromisoformat(last_success)
                current_time = datetime.datetime.now(datetime.timezone.utc)
                time_since_success = current_time - success_time
                
                # 格式化最后成功时间（使用Discord时间戳格式）
                unix_timestamp = int(success_time.timestamp())
                success_time_str = f"<t:{unix_timestamp}:F> (<t:{unix_timestamp}:R>)"
                embed.add_field(name="⏰ 最后答题成功时间", value=success_time_str, inline=False)
                
                # 计算缓冲区剩余时间
                buffer_threshold = datetime.timedelta(days=5)
                upper_buffer_threshold = datetime.timedelta(days=3)
                
                # 普通缓冲区剩余时间（5天）
                if time_since_success < buffer_threshold:
                    buffer_remaining = buffer_threshold - time_since_success
                    buffer_remaining_days = buffer_remaining.days
                    buffer_remaining_hours = buffer_remaining.seconds // 3600
                    buffer_remaining_minutes = (buffer_remaining.seconds % 3600) // 60
                    
                    if buffer_remaining_days > 0:
                        buffer_str = f"{buffer_remaining_days}天 {buffer_remaining_hours}小时 {buffer_remaining_minutes}分钟"
                    elif buffer_remaining_hours > 0:
                        buffer_str = f"{buffer_remaining_hours}小时 {buffer_remaining_minutes}分钟"
                    else:
                        buffer_str = f"{buffer_remaining_minutes}分钟"
                    
                    embed.add_field(name="🟠 缓冲区剩余时间（5天）", value=buffer_str, inline=True)
                else:
                    embed.add_field(name="🟠 缓冲区剩余时间（5天）", value="✅ 已达到升级条件", inline=True)
                
                # 高级缓冲区剩余时间（3天）
                if time_since_success < upper_buffer_threshold:
                    upper_buffer_remaining = upper_buffer_threshold - time_since_success
                    upper_remaining_days = upper_buffer_remaining.days
                    upper_remaining_hours = upper_buffer_remaining.seconds // 3600
                    upper_remaining_minutes = (upper_buffer_remaining.seconds % 3600) // 60
                    
                    if upper_remaining_days > 0:
                        upper_str = f"{upper_remaining_days}天 {upper_remaining_hours}小时 {upper_remaining_minutes}分钟"
                    elif upper_remaining_hours > 0:
                        upper_str = f"{upper_remaining_hours}小时 {upper_remaining_minutes}分钟"
                    else:
                        upper_str = f"{upper_remaining_minutes}分钟"
                    
                    embed.add_field(name="🟡 高级缓冲区剩余时间（3天）", value=upper_str, inline=True)
                else:
                    embed.add_field(name="🟡 高级缓冲区剩余时间（3天）", value="✅ 已达到升级条件", inline=True)
                
                # 显示已经过的时间
                elapsed_days = time_since_success.days
                elapsed_hours = time_since_success.seconds // 3600
                elapsed_minutes = (time_since_success.seconds % 3600) // 60
                
                if elapsed_days > 0:
                    elapsed_str = f"{elapsed_days}天 {elapsed_hours}小时 {elapsed_minutes}分钟"
                elif elapsed_hours > 0:
                    elapsed_str = f"{elapsed_hours}小时 {elapsed_minutes}分钟"
                else:
                    elapsed_str = f"{elapsed_minutes}分钟"
                
                embed.add_field(name="📅 距离答题成功已过", value=elapsed_str, inline=False)
                
            except Exception as e:
                embed.add_field(name="⚠️ 错误", value=f"解析时间失败: {e}", inline=False)
        else:
            embed.add_field(name="⏰ 答题记录", value="该成员暂无答题成功记录", inline=False)
        
        # 显示答题尝试统计
        attempts = user_data.get("attempts", [])
        if attempts:
            total_attempts = len(attempts)
            success_count = sum(1 for a in attempts if a.get("success", False))
            fail_count = total_attempts - success_count
            embed.add_field(
                name="📊 答题统计",
                value=f"总尝试次数：{total_attempts}\n成功：{success_count} | 失败：{fail_count}",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @verify.command(name="手动升级检查", description="立即执行一次自动升级检查")
    @is_admin()
    async def manual_upgrade_check(self, interaction: discord.Interaction):
        """手动执行自动升级检查"""
        await interaction.response.defer(ephemeral=True)
        
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ 只能在服务器中使用此命令", ephemeral=True)
            return
            
        if not self.auto_upgrade_enabled:
            await interaction.followup.send("⚠️ 自动升级功能已暂停，但仍执行此次检查", ephemeral=True)
        
        try:
            # 统计升级前的信息
            buffer_role_id = self.config.get("buffer_role_id")
            if buffer_role_id and buffer_role_id != "请填入缓冲区身份组ID":
                buffer_role = guild.get_role(int(buffer_role_id))
                initial_count = len(buffer_role.members) if buffer_role else 0
            else:
                initial_count = 0
            
            # 执行升级检查
            await self._process_auto_upgrade(guild)
            
            # 统计升级后的信息
            final_count = len(buffer_role.members) if buffer_role else 0
            upgraded_count = initial_count - final_count
            
            embed = discord.Embed(
                title="✅ 手动升级检查完成",
                color=discord.Color.green()
            )
            embed.add_field(name="升级用户数", value=str(upgraded_count), inline=True)
            embed.add_field(name="当前缓冲区用户数", value=str(final_count), inline=True)
            embed.add_field(name="检查时间", value=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), inline=False)
            
            if upgraded_count > 0:
                embed.add_field(
                    name="📋 说明",
                    value=f"成功升级了 {upgraded_count} 名用户从缓冲区到正式成员。",
                    inline=False
                )
            else:
                embed.add_field(
                    name="📋 说明",
                    value="没有找到符合升级条件的用户（答题成功3天以上）。",
                    inline=False
                )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            error_msg = f"执行升级检查时出错: {str(e)}"
            if self.logger:
                self.logger.error(f"手动升级检查失败: {e}")
            await interaction.followup.send(f"❌ {error_msg}", ephemeral=True)

    async def _process_answers(self, interaction: discord.Interaction, answers: List[str], language: str):
        """处理答题逻辑"""
        guild = interaction.guild
        user = interaction.user

        if not guild or not user:
            await interaction.response.send_message("❌ 系统错误，请稍后再试", ephemeral=True)
            return

        # 检查是否在禁言期
        if self._is_user_in_timeout(guild.id, user.id):
            timeout_msg = "您因多次答题错误被临时禁言，请稍后再试" if language == "zh_cn" else "You are temporarily timed out due to multiple wrong answers. Please try again later."
            await interaction.response.send_message(f"❌ {timeout_msg}", ephemeral=True)
            return

        # 检查是否已有身份组（使用服务器特定配置）
        buffer_role_id = self.get_guild_config("buffer_role_id", guild.id)
        verified_role_id = self.get_guild_config("verified_role_id", guild.id)
        upper_buffer_role_id = self.get_guild_config("upper_buffer_role_id", guild.id)
        
        if buffer_role_id and buffer_role_id != "请填入缓冲区身份组ID":
            buffer_role = guild.get_role(int(buffer_role_id))
            if buffer_role and buffer_role in user.roles:
                already_msg = "您已拥有相关身份组，无需重复验证" if language == "zh_cn" else "You already have the required role, no need to verify again."
                await interaction.response.send_message(f"❌ {already_msg}", ephemeral=True)
                return

        if verified_role_id and verified_role_id != "请填入已验证身份组ID":
            verified_role = guild.get_role(int(verified_role_id))
            if verified_role and verified_role in user.roles:
                already_msg = "您已拥有相关身份组，无需重复验证" if language == "zh_cn" else "You already have the required role, no need to verify again."
                await interaction.response.send_message(f"❌ {already_msg}", ephemeral=True)
                return

        # 获取用户的题目
        user_questions = await self._get_user_questions(guild.id, user.id)
        if not user_questions:
            no_questions_msg = "请先点击答题按钮获取题目" if language == "zh_cn" else "Please click the quiz button first to get questions."
            await interaction.response.send_message(f"❌ {no_questions_msg}", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # 检查答案
        correct_count = 0
        for i, (question, user_answer) in enumerate(zip(user_questions, answers)):
            if user_answer.strip().lower() == question["ans"].lower():
                correct_count += 1

        # 判定结果
        is_success = correct_count == 5
        
        # 检查是否曾经通过答题
        data = self._get_user_data(guild.id, user.id)
        if data and data.get("last_success") is not None:
            has_passed = True
        else:
            has_passed = False

        # 保存记录
        self._save_user_attempt(guild.id, user.id, is_success)

        if is_success:
            # 答题成功
            success_msg = f"🎉 恭喜！您已成功通过验证（{correct_count}/5）" if language == "zh_cn" else f"🎉 Congratulations! You have passed the verification ({correct_count}/5)"
            
            # 添加身份组（使用服务器特定配置）
            try:
                buffer_mode = self.get_guild_config("buffer_mode", guild.id, True)
                if buffer_mode and buffer_role_id and buffer_role_id != "请填入缓冲区身份组ID":
                    if has_passed and upper_buffer_role_id and False:
                        upper_buffer_role = guild.get_role(int(upper_buffer_role_id))
                        if upper_buffer_role:
                            # 检查是否启用同步模块
                            sync_cog = self.bot.get_cog("ServerSyncCommands")
                            if sync_cog:
                                await sync_cog.sync_add_role(guild, user, upper_buffer_role, "答题验证通过")
                            else:
                                await user.add_roles(upper_buffer_role, reason="答题验证通过")
                            success_msg += "\n✅ 已添加缓冲区身份组\n服务器当前处于缓冲准入模式，您可浏览资源区，但无法在答疑频道外发言\n您将在缓冲区等待3天，之后会自动转移到可正常发言的身份组。" if language == "zh_cn" else "\n✅ Buffer role added\nThe server is currently in buffer access mode, you can browse the resource area, but you can only speak in the slow-speed restricted answer channel.\nThe server will transfer buffer status users to the normal speaking identity group at the appropriate time.\nIf you want to leave the buffer zone early, and get the support channel speaking permission, you can go to https://discord.com/channels/1134557553011998840/1400260572070547666 to take the advanced quiz"
                    else:
                        role = guild.get_role(int(buffer_role_id))
                        if role:
                            # 检查是否启用同步模块
                            sync_cog = self.bot.get_cog("ServerSyncCommands")
                            if sync_cog:
                                await sync_cog.sync_add_role(guild, user, role, "答题验证通过")
                            else:
                                await user.add_roles(role, reason="答题验证通过")
                            success_msg += "\n✅ 已添加缓冲区身份组\n服务器当前处于缓冲准入模式，您可浏览资源区，但无法在服务器内发言\n您将在缓冲区等待5天，之后会自动转移到可正常发言的身份组。\n如果想要提前离开缓冲区，并获取答疑区发言权限，可以前往https://discord.com/channels/1134557553011998840/1400260572070547666 进行进阶答题" if language == "zh_cn" else "\n✅ Buffer role added\nThe server is currently in buffer access mode, you can browse the resource area, but you can only speak in the slow-speed restricted answer channel.\nThe server will transfer buffer status users to the normal speaking identity group at the appropriate time.\nIf you want to leave the buffer zone early, and get the support channel speaking permission, you can go to https://discord.com/channels/1134557553011998840/1400260572070547666 to take the advanced quiz"
                else:
                    role = guild.get_role(int(verified_role_id))
                    if role:
                        # 检查是否启用同步模块
                        sync_cog = self.bot.get_cog("ServerSyncCommands")
                        if sync_cog:
                            await sync_cog.sync_add_role(guild, user, role, "答题验证通过")
                        else:
                            await user.add_roles(role, reason="答题验证通过")
                        success_msg += "\n✅ 已添加已验证身份组" if language == "zh_cn" else "\n✅ Verified role added"
            except discord.Forbidden:
                error_msg = "\n⚠️ 无法添加身份组，请联系管理员" if language == "zh_cn" else "\n⚠️ Cannot add role, please contact administrators"
                success_msg += error_msg

            # 清除用户题目
            await self._clear_user_questions(guild.id, user.id)
            await interaction.followup.send(success_msg, ephemeral=True)
        else:
            # 答题失败
            failed_attempts = self._get_recent_failed_attempts(guild.id, user.id)
            max_attempts = self.config.get("max_attempts_per_period", 3)
            
            fail_msg = f"❌ 答题失败（{correct_count}/5）" if language == "zh_cn" else f"❌ Quiz failed ({correct_count}/5)"
            
            if failed_attempts >= max_attempts:
                # 达到最大失败次数，禁言
                timeout_minutes = self.config.get("fail_timeout_minutes", [10, 60])
                
                if failed_attempts == max_attempts:
                    minutes = timeout_minutes[0] if len(timeout_minutes) > 0 else 10
                else:
                    minutes = timeout_minutes[1] if len(timeout_minutes) > 1 else 60
                
                self._set_user_timeout(guild.id, user.id, minutes)
                timeout_msg = f"由于多次答题失败，您被禁言 {minutes} 分钟" if language == "zh_cn" else f"Due to multiple quiz failures, you are timed out for {minutes} minutes"
                fail_msg += f"\n{timeout_msg}"
            else:
                remaining = max_attempts - failed_attempts
                remaining_msg = f"剩余尝试次数：{remaining}" if language == "zh_cn" else f"Remaining attempts: {remaining}"
                fail_msg += f"\n{remaining_msg}"
            
            await interaction.followup.send(fail_msg, ephemeral=True)

    async def _process_quiz_submission(self, session_id: str, interaction: discord.Interaction):
        """处理答题提交"""
        session_id = self._get_quiz_id_by_user(interaction.user.id)
        session = self._get_quiz_session(session_id)
        if not session:
            await interaction.response.send_message("❌ 答题会话已过期", ephemeral=True)
            return

        questions = session["questions"]
        answers = session["answers"]
        language = session["language"]

        guild = interaction.guild
        user = interaction.user
        guild_id = guild.id
        user_id = user.id

        if not guild or not user:
            await interaction.response.send_message("❌ 系统错误", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # 检查答案
        correct_count = 0
        for i, (question, user_answer) in enumerate(zip(questions, answers)):
            if user_answer is None:
                continue
            
            # 处理不同题型的答案
            question_type = question.get("type", "single_choice")
            correct_answer = question.get("answer", "")
            
            if question_type == "single_choice":
                if user_answer.strip().upper() == correct_answer.upper():
                    correct_count += 1
            elif question_type == "multiple_choice":
                # 多选题答案格式如 "ABCEF"，用户答案也应该是这种格式
                if sorted(user_answer.strip().upper()) == sorted(correct_answer.upper()):
                    correct_count += 1
            elif question_type == "fill_in_blank":
                if user_answer.strip() == correct_answer.strip():
                    correct_count += 1

        # 判定结果
        is_success = correct_count == len(questions)

        # 检查是否曾经通过答题
        data = self._get_user_data(guild.id, user.id)
        if data and data.get("last_success") is not None:
            has_passed = True
        else:
            has_passed = False

        # 保存记录
        self._save_user_attempt(guild_id, user_id, is_success)

        # 清除答题会话
        self._clear_quiz_session(session_id)

        if is_success:
            # 答题成功
            success_msg = f"🎉 恭喜！您已成功通过验证（{correct_count}/{len(questions)}）" if language == "zh_cn" else f"🎉 Congratulations! You have passed the verification ({correct_count}/{len(questions)})"
            
            # 添加身份组（使用服务器特定配置）
            try:
                buffer_mode = self.get_guild_config("buffer_mode", guild.id, True)
                buffer_role_id = self.get_guild_config("buffer_role_id", guild.id)
                upper_buffer_role_id = self.get_guild_config("upper_buffer_role_id", guild.id)
                verified_role_id = self.get_guild_config("verified_role_id", guild.id)
                
                if buffer_mode and buffer_role_id and buffer_role_id != "请填入缓冲区身份组ID":
                    # temporary disable upper buffer role
                    if has_passed and upper_buffer_role_id and False:
                        upper_buffer_role = guild.get_role(int(upper_buffer_role_id))
                        if upper_buffer_role:
                            # 检查是否启用同步模块
                            sync_cog = self.bot.get_cog("ServerSyncCommands")
                            if sync_cog:
                                await sync_cog.sync_add_role(guild, user, upper_buffer_role, "答题验证通过")
                            else:
                                await user.add_roles(upper_buffer_role, reason="答题验证通过")
                            success_msg += "\n✅ 已添加缓冲区身份组\n服务器当前处于缓冲准入模式，您可浏览资源区，但无法在答疑频道外发言\n您将在缓冲区等待3天，之后会自动转移到可正常发言的身份组。" if language == "zh_cn" else "\n✅ Buffer role added\nThe server is currently in buffer access mode, you can browse the resource area, but you can only speak in the slow-speed restricted answer channel.\nThe server will transfer buffer status users to the normal speaking identity group at the appropriate time.\nIf you want to leave the buffer zone early, and get the support channel speaking permission, you can go to https://discord.com/channels/1134557553011998840/1400260572070547666 to take the advanced quiz"

                    else:
                        role = guild.get_role(int(buffer_role_id))
                        if role:
                            # 检查是否启用同步模块
                            sync_cog = self.bot.get_cog("ServerSyncCommands")
                            if sync_cog:
                                await sync_cog.sync_add_role(guild, user, role, "答题验证通过")
                            else:
                                await user.add_roles(role, reason="答题验证通过")
                            success_msg += "\n✅ 已添加缓冲区身份组\n服务器当前处于缓冲准入模式，您可浏览资源区，但无法在服务器内发言\n您将在缓冲区等待5天，之后会自动转移到可正常发言的身份组。\n如果想要提前离开缓冲区，并获取答疑区发言权限，可以前往https://discord.com/channels/1134557553011998840/1400260572070547666 进行进阶答题" if language == "zh_cn" else "\n✅ Buffer role added\nThe server is currently in buffer access mode, you can browse the resource area, but you can only speak in the slow-speed restricted answer channel.\nThe server will transfer buffer status users to the normal speaking identity group at the appropriate time.\nIf you want to leave the buffer zone early, and get the support channel speaking permission, you can go to https://discord.com/channels/1134557553011998840/1400260572070547666 to take the advanced quiz"
                else:
                    role = guild.get_role(int(verified_role_id))
                    if role:
                        # 检查是否启用同步模块
                        sync_cog = self.bot.get_cog("ServerSyncCommands")
                        if sync_cog:
                            await sync_cog.sync_add_role(guild, user, role, "答题验证通过")
                        else:
                            await user.add_roles(role, reason="答题验证通过")
                        success_msg += "\n✅ 已添加已验证身份组" if language == "zh_cn" else "\n✅ Verified role added"
            except discord.Forbidden:
                error_msg = "\n⚠️ 无法添加身份组，请联系管理员" if language == "zh_cn" else "\n⚠️ Cannot add role, please contact administrators"
                success_msg += error_msg

            await interaction.followup.send(success_msg, ephemeral=True)
        else:
            # 答题失败
            failed_attempts = self._get_recent_failed_attempts(guild_id, user_id)
            max_attempts = self.config.get("max_attempts_per_period", 3)
            
            fail_msg = f"❌ 答题失败（{correct_count}正确/{len(questions)}题）" if language == "zh_cn" else f"❌ Quiz failed ({correct_count} correct /{len(questions)} questions)"
            
            if failed_attempts >= max_attempts:
                # 达到最大失败次数，设置冷却时间
                cooldown_minutes = self.config.get("fail_cooldown_minutes", [10, 60])
                
                if failed_attempts == max_attempts:
                    minutes = cooldown_minutes[0] if len(cooldown_minutes) > 0 else 10
                else:
                    minutes = cooldown_minutes[1] if len(cooldown_minutes) > 1 else 60
                
                self._set_user_quiz_cooldown(guild_id, user_id, minutes)
                cooldown_msg = f"由于多次答题失败，您需要冷却 {minutes} 分钟后才能再次答题" if language == "zh_cn" else f"Due to multiple quiz failures, you need to wait {minutes} minutes before taking the quiz again"
                fail_msg += f"\n{cooldown_msg}"
            else:
                remaining = max_attempts - failed_attempts
                remaining_msg = f"冷却前剩余尝试次数：{remaining}" if language == "zh_cn" else f"Remaining attempts before cooldown: {remaining}"
                fail_msg += f"\n{remaining_msg}"
            
            await interaction.followup.send(fail_msg, ephemeral=True)

    async def _get_user_questions(self, guild_id: int, user_id: int) -> Optional[List[Dict]]:
        """获取用户的题目"""
        cache_dir = pathlib.Path("data") / "thread_cache"
        cache_file = cache_dir / f"verify_questions_{guild_id}_{user_id}.json"
        
        if cache_file.exists():
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    async def _save_user_questions(self, guild_id: int, user_id: int, questions: List[Dict]):
        """保存用户的题目"""
        cache_dir = pathlib.Path("data") / "thread_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"verify_questions_{guild_id}_{user_id}.json"
        
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(questions, f, ensure_ascii=False, indent=2)

    async def _clear_user_questions(self, guild_id: int, user_id: int):
        """清除用户的题目"""
        cache_dir = pathlib.Path("data") / "thread_cache"
        cache_file = cache_dir / f"verify_questions_{guild_id}_{user_id}.json"
        
        if cache_file.exists():
            cache_file.unlink()

    async def start_quiz(self, interaction: discord.Interaction, language: str):
        """开始答题流程"""
        
        guild = interaction.guild
        user = interaction.user

        if not guild or not user:
            await interaction.response.send_message("❌ 系统错误", ephemeral=True)
            return

        # 检查是否在答题冷却期
        if self._is_user_in_quiz_cooldown(guild.id, user.id):
            remaining = self._get_quiz_cooldown_remaining(guild.id, user.id)
            cooldown_msg = f"您因多次答题错误需要冷却 {remaining} 分钟后才能再次答题" if language == "zh_cn" else f"You need to wait {remaining} minutes before taking the quiz again due to multiple failures."
            await interaction.response.send_message(f"❌ {cooldown_msg}", ephemeral=True)
            return

        # 检查是否已有身份组（使用服务器特定配置）
        buffer_role_id = self.get_guild_config("buffer_role_id", guild.id)
        verified_role_id = self.get_guild_config("verified_role_id", guild.id)
        
        if buffer_role_id and buffer_role_id != "请填入缓冲区身份组ID":
            buffer_role = guild.get_role(int(buffer_role_id))
            if buffer_role and buffer_role in user.roles:
                already_msg = "您已拥有相关身份组，无需重复验证" if language == "zh_cn" else "You already have the required role, no need to verify again."
                await interaction.response.send_message(f"❌ {already_msg}", ephemeral=True)
                return

        if verified_role_id and verified_role_id != "请填入已验证身份组ID":
            verified_role = guild.get_role(int(verified_role_id))
            if verified_role and verified_role in user.roles:
                already_msg = "您已拥有相关身份组，无需重复验证" if language == "zh_cn" else "You already have the required role, no need to verify again."
                await interaction.response.send_message(f"❌ {already_msg}", ephemeral=True)
                return

        # 随机选择题目
        questions_per_quiz = self.config.get("questions_per_quiz", 5)
        if len(self.questions) < questions_per_quiz:
            error_msg = "题目数量不足" if language == "zh_cn" else "Insufficient questions"
            await interaction.response.send_message(f"❌ {error_msg}", ephemeral=True)
            return

        selected_questions = random.sample(self.questions, questions_per_quiz)
        
        # 清除用户之前的答题会话
        self._clear_user_quiz_sessions(guild.id, user.id)
        
        # 创建新的答题会话
        session_id = self._create_quiz_session(guild.id, user.id, selected_questions, language)
        
        # 显示第一题
        view = QuizView(self, session_id)
        embed = await view.create_question_embed()
        await view.update_view_without_interaction()  # 初始化按钮
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class QuizView(discord.ui.View):
    """答题界面视图"""
    def __init__(self, cog: VerifyCommands, session_id: str):
        super().__init__(timeout=300)  # 5分钟超时
        self.cog = cog
        self.session_id = session_id
    
    async def create_question_embed(self) -> discord.Embed:
        """创建题目显示的embed"""
        session = self.cog._get_quiz_session(self.session_id)
        if not session:
            embed = discord.Embed(
                title="❌ 会话已过期",
                description="此答题界面已过期，请重新开始答题。",
                color=discord.Color.red()
            )
            return embed
        
        questions = session["questions"]
        current_idx = session["current_question"]
        language = session["language"]
        answers = session["answers"]
        
        if current_idx >= len(questions):
            current_idx = len(questions) - 1
        
        current_question = questions[current_idx]
        question_type = current_question.get("type", "single_choice")
        
        # 构建标题
        title = f"🎯 答题验证 ({current_idx + 1}/{len(questions)})" if language == "zh_cn" else f"🎯 Quiz Verification ({current_idx + 1}/{len(questions)})"
        
        embed = discord.Embed(title=title, color=discord.Color.blue())
        
        # 获取题目内容
        question_data = current_question.get(language, {})
        if isinstance(question_data, dict):
            question_text = question_data.get("question", "题目加载失败")
            choices = question_data.get("choices", [])
        else:
            question_text = question_data if question_data else "题目加载失败"
            choices = []
        
        # 显示题目
        embed.add_field(name="📝 题目", value=question_text, inline=False)
        
        # 显示选项（仅限选择题）
        if question_type in ["single_choice", "multiple_choice"] and choices:
            choices_text = "\n".join(choices)
            embed.add_field(name="📋 选项", value=choices_text, inline=False)
        
        # 显示当前答案
        current_answer = answers[current_idx]
        if current_answer is not None:
            answer_text = f"当前答案：{current_answer}" if language == "zh_cn" else f"Current answer: {current_answer}"
            embed.add_field(name="✅ 已选择", value=answer_text, inline=False)
        
        # 显示进度
        progress = f"进度：{current_idx + 1}/{len(questions)}" if language == "zh_cn" else f"Progress: {current_idx + 1}/{len(questions)}"
        embed.set_footer(text=progress)
        
        return embed
    
    async def update_view_without_interaction(self):
        """更新视图（不需要interaction）"""
        session = self.cog._get_quiz_session(self.session_id)
        if not session:
            return
        
        # 清除现有按钮
        self.clear_items()
        
        questions = session["questions"]
        current_idx = session["current_question"]
        language = session["language"]
        
        if current_idx >= len(questions):
            current_idx = len(questions) - 1
        
        current_question = questions[current_idx]
        question_type = current_question.get("type", "single_choice")
        
        # 添加题目类型相关的按钮
        if question_type == "single_choice":
            question_data = current_question.get(language, {})
            choices = question_data.get("choices", [])
            for choice in choices:
                # 提取选项标识符（A, B, C, D等）
                choice_id = choice.split('.')[0].strip()
                button = discord.ui.Button(
                    label=choice_id,
                    custom_id=f"choice_{choice_id}",
                    style=discord.ButtonStyle.success if session["answers"][current_idx] == choice_id else discord.ButtonStyle.secondary
                )
                button.callback = self._create_choice_callback(choice_id)
                self.add_item(button)
        
        elif question_type == "multiple_choice":
            question_data = current_question.get(language, {})
            choices = question_data.get("choices", [])
            current_answer = session["answers"][current_idx] or ""
            for choice in choices:
                # 提取选项标识符（A, B, C, D等）
                choice_id = choice.split('.')[0].strip()
                is_selected = choice_id in current_answer
                button = discord.ui.Button(
                    label=choice_id,
                    custom_id=f"multichoice_{choice_id}",
                    style=discord.ButtonStyle.success if is_selected else discord.ButtonStyle.secondary
                )
                button.callback = self._create_multichoice_callback(choice_id)
                self.add_item(button)
        
        elif question_type == "fill_in_blank":
            button = discord.ui.Button(
                label="填入答案" if language == "zh_cn" else "Fill Answer",
                custom_id="fill_blank",
                style=discord.ButtonStyle.primary
            )
            button.callback = self._fill_blank_callback
            self.add_item(button)
        
        # 添加导航按钮
        if current_idx > 0:
            prev_button = discord.ui.Button(
                label="⬅️ 上一题" if language == "zh_cn" else "⬅️ Previous",
                custom_id="prev_question",
                style=discord.ButtonStyle.secondary,
                row=1
            )
            prev_button.callback = self._prev_question_callback
            self.add_item(prev_button)
        
        if current_idx < len(questions) - 1:
            next_button = discord.ui.Button(
                label="下一题 ➡️" if language == "zh_cn" else "Next ➡️",
                custom_id="next_question",
                style=discord.ButtonStyle.secondary,
                row=1
            )
            next_button.callback = self._next_question_callback
            self.add_item(next_button)
        else:
            # 最后一题，显示提交按钮
            submit_button = discord.ui.Button(
                label="✅ 提交答案" if language == "zh_cn" else "✅ Submit",
                custom_id="submit_quiz",
                style=discord.ButtonStyle.success,
                row=1
            )
            submit_button.callback = self._submit_callback
            self.add_item(submit_button)
    
    async def update_view(self, interaction: discord.Interaction):
        """更新视图"""
        self.session_id = self.cog._get_quiz_id_by_user(interaction.user.id)
        session = self.cog._get_quiz_session(interaction.user.id)
        if not session:
            # 会话已过期
            embed = discord.Embed(
                title="❌ 此界面已过期",
                description="答题会话已过期或被新的答题覆盖，请重新开始答题。",
                color=discord.Color.red()
            )
            self.clear_items()
            await interaction.response.edit_message(embed=embed, view=self)
            return
        
        await self.update_view_without_interaction()
    
    def _create_choice_callback(self, choice_id: str):
        async def callback(interaction: discord.Interaction):
            self.session_id = self.cog._get_quiz_id_by_user(interaction.user.id)
            session = self.cog._get_quiz_session(interaction.user.id)
            if not session:
                await interaction.response.send_message("❌ 答题会话已过期", ephemeral=True)
                return
            
            """
            # 检查用户权限
            if interaction.user.id != session["user_id"]:
                await interaction.response.send_message("❌ 这不是您的答题界面", ephemeral=True)
                return
                """
            
            current_idx = session["current_question"]
            session["answers"][current_idx] = choice_id
            self.cog._update_quiz_session(self.session_id, answers=session["answers"])
            
            await self.update_view(interaction)
            embed = await self.create_question_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        
        return callback
    
    def _create_multichoice_callback(self, choice_id: str):
        async def callback(interaction: discord.Interaction):
            self.session_id = self.cog._get_quiz_id_by_user(interaction.user.id)
            session = self.cog._get_quiz_session(interaction.user.id)
            if not session:
                await interaction.response.send_message("❌ 答题会话已过期", ephemeral=True)
                return
            
            """
            # 检查用户权限
            if interaction.user.id != session["user_id"]:
                await interaction.response.send_message("❌ 这不是您的答题界面", ephemeral=True)
                return
                """
            
            current_idx = session["current_question"]
            current_answer = session["answers"][current_idx] or ""
            
            if choice_id in current_answer:
                # 取消选择
                current_answer = current_answer.replace(choice_id, "")
            else:
                # 添加选择
                current_answer += choice_id
            
            # 按字母顺序排序
            current_answer = "".join(sorted(set(current_answer)))
            session["answers"][current_idx] = current_answer
            self.cog._update_quiz_session(self.session_id, answers=session["answers"])
            
            await self.update_view(interaction)
            embed = await self.create_question_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        
        return callback
    
    async def _fill_blank_callback(self, interaction: discord.Interaction):
        self.session_id = self.cog._get_quiz_id_by_user(interaction.user.id)
        session = self.cog._get_quiz_session(interaction.user.id)
        if not session:
            await interaction.response.send_message("❌ 答题会话已过期", ephemeral=True)
            return
        
        """
        # 检查用户权限
        if interaction.user.id != session["user_id"]:
            await interaction.response.send_message("❌ 这不是您的答题界面", ephemeral=True)
            return
            """
        
        # 显示输入模态框
        modal = FillBlankModal(self, self.session_id)
        await interaction.response.send_modal(modal)
    
    async def _prev_question_callback(self, interaction: discord.Interaction):
        self.session_id = self.cog._get_quiz_id_by_user(interaction.user.id)
        session = self.cog._get_quiz_session(interaction.user.id)
        if not session:
            await interaction.response.send_message("❌ 答题会话已过期", ephemeral=True)
            return
        """
        # 检查用户权限
        if interaction.user.id != session["user_id"]:
            await interaction.response.send_message("❌ 这不是您的答题界面", ephemeral=True)
            return
            """
        
        current_idx = session["current_question"]
        if current_idx > 0:
            new_idx = current_idx - 1
            self.cog._update_quiz_session(self.session_id, current_question=new_idx)
            
            await self.update_view(interaction)
            embed = await self.create_question_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()
    
    async def _next_question_callback(self, interaction: discord.Interaction):
        self.session_id = self.cog._get_quiz_id_by_user(interaction.user.id)
        session = self.cog._get_quiz_session(interaction.user.id)
        if not session:
            await interaction.response.send_message("❌ 答题会话已过期", ephemeral=True)
            return
        
        # 检查用户权限
        """
        if interaction.user.id != session["user_id"]:
            await interaction.response.send_message("❌ 这不是您的答题界面", ephemeral=True)
            return
        """
        current_idx = session["current_question"]
        questions = session["questions"]
        if current_idx < len(questions) - 1:
            new_idx = current_idx + 1
            self.cog._update_quiz_session(self.session_id, current_question=new_idx)
            
            await self.update_view(interaction)
            embed = await self.create_question_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()
    
    async def _submit_callback(self, interaction: discord.Interaction):
        self.session_id = self.cog._get_quiz_id_by_user(interaction.user.id)
        session = self.cog._get_quiz_session(interaction.user.id)
        if not session:
            await interaction.response.send_message("❌ 答题会话已过期", ephemeral=True)
            return
        
        # 检查用户权限
        """
        if interaction.user.id != session["user_id"]:
            await interaction.response.send_message("❌ 这不是您的答题界面", ephemeral=True)
            return
        """
        await self.cog._process_quiz_submission(self.session_id, interaction)
    
    async def on_timeout(self):
        """超时处理"""
        self.cog._clear_quiz_session(self.session_id)


class FillBlankModal(discord.ui.Modal):
    """填空题输入模态框"""
    def __init__(self, quiz_view: QuizView, session_id: str):
        super().__init__(title="填写答案" if quiz_view.cog._get_quiz_session(session_id).get("language") == "zh_cn" else "Fill Answer")
        self.quiz_view = quiz_view
        self.session_id = session_id
        
        session = quiz_view.cog._get_quiz_session(session_id)
        language = session.get("language", "zh_cn") if session else "zh_cn"
        
        self.answer_input = discord.ui.TextInput(
            label="请输入答案" if language == "zh_cn" else "Please enter your answer",
            placeholder="输入您的答案..." if language == "zh_cn" else "Enter your answer...",
            required=True,
            max_length=100
        )
        self.add_item(self.answer_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        self.session_id = self.quiz_view.cog._get_quiz_id_by_user(interaction.user.id)
        session = self.quiz_view.cog._get_quiz_session(interaction.user.id)
        if not session:
            await interaction.response.send_message("❌ 答题会话已过期", ephemeral=True)
            return
        
        # 检查用户权限
        """
        if interaction.user.id != session["user_id"]:
            await interaction.response.send_message("❌ 这不是您的答题界面", ephemeral=True)
            return
        """
        current_idx = session["current_question"]
        answer = self.answer_input.value.strip()
        session["answers"][current_idx] = answer
        self.quiz_view.cog._update_quiz_session(self.session_id, answers=session["answers"])
        
        await self.quiz_view.update_view(interaction)
        embed = await self.quiz_view.create_question_embed()
        await interaction.response.edit_message(embed=embed, view=self.quiz_view)


class VerifyButtonView(discord.ui.View):
    """验证按钮视图"""
    def __init__(self, cog: VerifyCommands, language: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.language = language
        
        if language == "zh_cn":
            button = discord.ui.Button(
                label="🎯 开始答题",
                style=discord.ButtonStyle.primary,
                custom_id="verify:start_quiz:zh_cn"
            )
        else:
            button = discord.ui.Button(
                label="🎯 Start Quiz",
                style=discord.ButtonStyle.success,
                custom_id="verify:start_quiz:en_us"
            )
        
        button.callback = self._start_quiz_callback
        self.add_item(button)

    async def _start_quiz_callback(self, interaction: discord.Interaction):
        await self.cog.start_quiz(interaction, self.language)


async def setup(bot):
    await bot.add_cog(VerifyCommands(bot))
    