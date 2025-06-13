import asyncio
import json
import random
import pathlib
import datetime
from typing import List, Dict, Optional

import discord
from discord.ext import commands
from discord import app_commands

from src.utils.confirm_view import confirm_view


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
    
    def is_admin():
        async def predicate(interaction: discord.Interaction):
            try:
                guild = interaction.guild
                if not guild:
                    return False
                    
                cog = interaction.client.get_cog("VerifyCommands")
                if not cog:
                    return False
                config = getattr(cog, 'config', {})
                for admin in config.get('admins', []):
                    role = guild.get_role(admin)
                    if role:
                        if role in interaction.user.roles:
                            return True
                return False
            except Exception:
                return False
        return app_commands.check(predicate)

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
                "timeout_until": None
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
            "timeout_until": None
        }

    def _set_user_timeout(self, guild_id: int, user_id: int, minutes: int):
        """设置用户禁言时间"""
        data_dir = pathlib.Path("data") / "verify" / str(guild_id)
        data_dir.mkdir(parents=True, exist_ok=True)
        file_path = data_dir / f"{user_id}.json"
        
        user_data = self._get_user_data(guild_id, user_id)
        timeout_until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
        user_data["timeout_until"] = timeout_until.isoformat()
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)

    def _is_user_in_timeout(self, guild_id: int, user_id: int) -> bool:
        """检查用户是否在禁言期间"""
        user_data = self._get_user_data(guild_id, user_id)
        timeout_until = user_data.get("timeout_until")
        
        if timeout_until:
            timeout_time = datetime.datetime.fromisoformat(timeout_until)
            return datetime.datetime.now(datetime.timezone.utc) < timeout_time
        
        return False

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
            buffer_role_id = self.config.get("buffer_role_id")
            verified_role_id = self.config.get("verified_role_id")
            
            if not buffer_role_id or not verified_role_id:
                return
                
            if buffer_role_id == "请填入缓冲区身份组ID" or verified_role_id == "请填入已验证身份组ID":
                return
                
            buffer_role = guild.get_role(int(buffer_role_id))
            verified_role = guild.get_role(int(verified_role_id))
            
            if not buffer_role or not verified_role:
                return
                
            # 获取拥有缓冲区身份组的成员
            eligible_members = []
            current_time = datetime.datetime.now(datetime.timezone.utc)
            upgrade_threshold = datetime.timedelta(days=3)  # 3天后自动升级
            
            for member in buffer_role.members:
                if verified_role in member.roles:
                    continue  # 已经有verified角色，跳过
                    
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
            
            # 升级符合条件的成员
            for member, success_time in eligible_members:
                try:
                    # 检查是否启用同步模块
                    sync_cog = self.bot.get_cog("ServerSyncCommands")
                    if sync_cog:
                        await sync_cog.sync_add_role(guild, member, verified_role, "自动升级：缓冲区3天期满")
                        await sync_cog.sync_remove_role(guild, member, buffer_role, "自动升级：缓冲区3天期满")
                    else:
                        await member.add_roles(verified_role, reason="自动升级：缓冲区3天期满")
                        await member.remove_roles(buffer_role, reason="自动升级：缓冲区3天期满")
                    
                    if self.logger:
                        self.logger.info(f"自动升级成功: {member} (ID: {member.id}) 在服务器 {guild.name}")
                    
                    # 发送私聊通知
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
        # 启动自动升级任务
        asyncio.create_task(self._auto_upgrade_task())
        if self.logger:
            self.logger.info("自动升级任务已启动")

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
                "阅读上述规则后，请点击下方按钮，然后将答案填入命令中回答。",
                "使用命令：`/答题 <答案1> <答案2> <答案3> <答案4> <答案5>`"
            ]),
            color=discord.Color.blue()
        )

        # 创建英文embed
        en_embed = discord.Embed(
            title="🎯 Quiz Verification",
            description="\n".join([
                "After reading the rules, please click the button below and fill in the answers in the command.",
                "Use the command: `/answer <answer1> <answer2> <answer3> <answer4> <answer5>`"
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

    @app_commands.command(name="答题", description="回答验证题目（中文）")
    @app_commands.describe(
        ans1="第1题答案", ans2="第2题答案", ans3="第3题答案", ans4="第4题答案", ans5="第5题答案"
    )
    @app_commands.rename(ans1="答案1", ans2="答案2", ans3="答案3", ans4="答案4", ans5="答案5")
    async def answer_zh(self, interaction: discord.Interaction, 
                        ans1: str, ans2: str, ans3: str, ans4: str, ans5: str):
        answers = [ans1, ans2, ans3, ans4, ans5]
        await self._process_answers(interaction, answers, "zh_cn")

    @app_commands.command(name="answer", description="Answer verification questions (English)")
    @app_commands.describe(
        answer1="Answer to question 1", answer2="Answer to question 2", 
        answer3="Answer to question 3", answer4="Answer to question 4", answer5="Answer to question 5"
    )
    async def answer_en(self, interaction: discord.Interaction,
                       answer1: str, answer2: str, answer3: str, answer4: str, answer5: str):
        answers = [answer1, answer2, answer3, answer4, answer5]
        await self._process_answers(interaction, answers, "en_us")

    async def _process_answers(self, interaction: discord.Interaction, answers: List[str], language: str):
        """处理答题逻辑"""
        await interaction.response.defer(ephemeral=True)

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

        # 检查是否已有身份组
        buffer_role_id = self.config.get("buffer_role_id")
        verified_role_id = self.config.get("verified_role_id")
        
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

        # 保存记录
        self._save_user_attempt(guild.id, user.id, is_success)

        if is_success:
            # 答题成功
            success_msg = f"🎉 恭喜！您已成功通过验证（{correct_count}/5）" if language == "zh_cn" else f"🎉 Congratulations! You have passed the verification ({correct_count}/5)"
            
            # 添加身份组
            try:
                buffer_mode = self.config.get("buffer_mode", True)
                if buffer_mode and buffer_role_id and buffer_role_id != "请填入缓冲区身份组ID":
                    role = guild.get_role(int(buffer_role_id))
                    if role:
                        # 检查是否启用同步模块
                        sync_cog = self.bot.get_cog("ServerSyncCommands")
                        if sync_cog:
                            await sync_cog.sync_add_role(guild, user, role, "答题验证通过")
                        else:
                            await user.add_roles(role, reason="答题验证通过")
                        success_msg += "\n✅ 已添加缓冲区身份组\n服务器当前处于缓冲准入模式，您可浏览资源区，但只能在有慢速限制的答疑频道发言。\n服务器会适时将缓冲状态用户转移到可正常发言的身份组。" if language == "zh_cn" else "\n✅ Buffer role added\nThe server is currently in buffer access mode, you can browse the resource area, but you can only speak in the slow-speed restricted answer channel.\nThe server will transfer buffer status users to the normal speaking identity group at the appropriate time."
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

        # 检查是否在禁言期
        if self._is_user_in_timeout(guild.id, user.id):
            timeout_msg = "您因多次答题错误被临时禁言，请稍后再试" if language == "zh_cn" else "You are temporarily timed out due to multiple wrong answers. Please try again later."
            await interaction.response.send_message(f"❌ {timeout_msg}", ephemeral=True)
            return

        # 随机选择题目
        questions_per_quiz = self.config.get("questions_per_quiz", 5)
        if len(self.questions) < questions_per_quiz:
            error_msg = "题目数量不足" if language == "zh_cn" else "Insufficient questions"
            await interaction.response.send_message(f"❌ {error_msg}", ephemeral=True)
            return

        selected_questions = random.sample(self.questions, questions_per_quiz)
        
        # 保存用户题目
        await self._save_user_questions(guild.id, user.id, selected_questions)

        # 构建题目展示
        embed = discord.Embed(
            title="🎯 答题验证" if language == "zh_cn" else "🎯 Quiz Verification",
            color=discord.Color.blue()
        )

        question_text = ""
        for i, question in enumerate(selected_questions, 1):
            q_text = question.get("zh_cn" if language == "zh_cn" else "en_us", "题目加载失败")
            question_text += f"**{i}.** {q_text}\n\n"

        embed.description = question_text

        if language == "zh_cn":
            embed.add_field(
                name="📝 如何回答",
                value="使用命令：`/答题 <答案1> <答案2> <答案3> <答案4> <答案5>`",
                inline=False
            )
        else:
            embed.add_field(
                name="📝 How to Answer",
                value="Use command: `/answer <answer1> <answer2> <answer3> <answer4> <answer5>`",
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)


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