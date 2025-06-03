import discord
import sqlite3
import json
import hashlib
import pathlib
import re
from typing import Optional, Dict, Any, Tuple
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta


class AnonymousFeedbackCog(commands.Cog):
    feedback = app_commands.Group(name="匿名反馈", description="匿名反馈功能")
    author_feedback = app_commands.Group(name="帖主", description="帖主反馈管理功能")
    admin_feedback = app_commands.Group(name="匿名反馈管理", description="匿名反馈管理功能")

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger
        self.name = "匿名反馈系统"
        
        self.db_path = pathlib.Path("data") / "anonymous_feedback.db"
        self.db_path.parent.mkdir(exist_ok=True)
        self._init_database()
        
        self._config_cache = {}
        self._config_cache_mtime = None
        
        self.pending_file_requests = {}
        
        self.image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
        self.file_extensions = {'.pdf', '.doc', '.docx', '.txt', '.zip', '.rar', '.7z', '.mp4', '.mp3', '.xlsx', '.xls', '.ppt', '.pptx'}
        self.max_file_size = 25 * 1024 * 1024
    
    def _init_database(self):
        """初始化数据库表"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_cookie TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    is_banned INTEGER DEFAULT 0,
                    warning_count INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_feedback_id INTEGER NOT NULL,
                    user_cookie TEXT NOT NULL,
                    guild_id INTEGER NOT NULL,
                    target_url TEXT NOT NULL,
                    target_thread_id INTEGER NOT NULL,
                    content_type TEXT NOT NULL,
                    content TEXT,
                    file_url TEXT,
                    message_id INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    is_deleted INTEGER DEFAULT 0,
                    FOREIGN KEY (user_cookie) REFERENCES users (user_cookie)
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS guild_sequences (
                    guild_id INTEGER PRIMARY KEY,
                    next_feedback_id INTEGER DEFAULT 1
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS downvote_records (
                    message_id INTEGER PRIMARY KEY,
                    feedback_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    downvote_count INTEGER DEFAULT 0,
                    FOREIGN KEY (feedback_id) REFERENCES feedback (id)
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS warning_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_cookie TEXT NOT NULL,
                    guild_id INTEGER NOT NULL,
                    warning_type TEXT NOT NULL,
                    feedback_id INTEGER,
                    moderator_id INTEGER,
                    reason TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_cookie) REFERENCES users (user_cookie),
                    FOREIGN KEY (feedback_id) REFERENCES feedback (id)
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS author_warnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_cookie TEXT NOT NULL,
                    guild_id INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    warning_count INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_cookie, author_id),
                    FOREIGN KEY (user_cookie) REFERENCES users (user_cookie)
                )
            ''')
            
            conn.execute('CREATE INDEX IF NOT EXISTS idx_feedback_guild_thread ON feedback (guild_id, target_thread_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_users_guild_user ON users (guild_id, user_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_author_warnings_cookie_author ON author_warnings (user_cookie, author_id)')
            
            conn.commit()
            
            if self.logger:
                self.logger.info("匿名反馈系统 - 数据库初始化完成")
    
    @property
    def config(self):
        """读取配置文件并缓存"""
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
    
    def is_admin(self, user: discord.Member) -> bool:
        """检查用户是否为管理员"""
        config = self.config
        
        admin_list = config.get('admins', [])
        
        # 首先检查用户ID（直接匹配）
        if user.id in admin_list:
            return True
        
        # 然后检查身份组ID（兼容旧配置）
        for admin_id in admin_list:
            try:
                role = user.guild.get_role(int(admin_id))
                if role and role in user.roles:
                    return True
            except (ValueError, TypeError):
                # 如果无法转换为int，说明可能是用户ID而非身份组ID
                continue
            
        return False

    def _get_user_cookie(self, user_id: int, guild_id: int) -> str:
        """生成用户cookie（匿名标识）"""
        return hashlib.sha256(f"{user_id}:{guild_id}:anonymous_feedback".encode()).hexdigest()[:16]

    def _register_user(self, user_id: int, guild_id: int) -> str:
        """注册用户并返回cookie"""
        cookie = self._get_user_cookie(user_id, guild_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('INSERT OR IGNORE INTO users (user_cookie, user_id, guild_id) VALUES (?, ?, ?)',
                        (cookie, user_id, guild_id))
            return cookie
    
    def _get_recent_feedback_count_in_thread(self, cookie: str, thread_id: int, hours: int = 24) -> int:
        """获取用户在特定帖子中最近24小时的反馈数量"""
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
        with sqlite3.connect(self.db_path) as conn:
            result = conn.execute('''
                SELECT COUNT(*) FROM feedback 
                WHERE user_cookie = ? AND target_thread_id = ? 
                AND created_at > ? AND is_deleted = 0
            ''', (cookie, thread_id, cutoff_time.isoformat())).fetchone()
        return result[0] if result else 0
    
    def _get_author_warning_count(self, cookie: str, author_id: int) -> int:
        """获取用户对特定帖主的警告次数"""
        with sqlite3.connect(self.db_path) as conn:
            result = conn.execute(
                'SELECT warning_count FROM author_warnings WHERE user_cookie = ? AND author_id = ?',
                (cookie, author_id)
            ).fetchone()
            return result[0] if result else 0
    
    def _add_author_warning(self, cookie: str, author_id: int, warning_type: str = "report", 
                           feedback_id: int = None, moderator_id: int = None, reason: str = None):
        """增加用户对特定帖主的警告次数"""
        with sqlite3.connect(self.db_path) as conn:
            # 获取用户的guild_id
            guild_result = conn.execute('SELECT guild_id FROM users WHERE user_cookie = ?', (cookie,)).fetchone()
            if not guild_result:
                return 0
            guild_id = guild_result[0]
            
            # 增加或创建按帖主的警告记录
            conn.execute('''
                INSERT OR REPLACE INTO author_warnings (user_cookie, guild_id, author_id, warning_count, updated_at)
                VALUES (?, ?, ?, COALESCE((SELECT warning_count FROM author_warnings WHERE user_cookie = ? AND author_id = ?), 0) + 1, CURRENT_TIMESTAMP)
            ''', (cookie, guild_id, author_id, cookie, author_id))
            
            # 获取更新后的警告次数
            new_count = conn.execute(
                'SELECT warning_count FROM author_warnings WHERE user_cookie = ? AND author_id = ?',
                (cookie, author_id)
            ).fetchone()[0]
            
            # 记录警告详情到通用记录表
            conn.execute('''
                INSERT INTO warning_records (user_cookie, guild_id, warning_type, feedback_id, moderator_id, reason)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (cookie, guild_id, f"{warning_type}_author_{author_id}", feedback_id, moderator_id, reason))
            
            # 记录日志
            if self.logger:
                self.logger.info(f"匿名反馈系统 - 帖主警告记录: cookie={cookie[:8]}, author_id={author_id}, count={new_count}, type={warning_type}")
            
            return new_count
    
    def _reduce_author_warning(self, cookie: str, author_id: int, reduce_count: int = 1) -> tuple[int, int]:
        """减少用户对特定帖主的警告次数，返回(减少前次数, 减少后次数)"""
        with sqlite3.connect(self.db_path) as conn:
            # 获取当前警告次数
            result = conn.execute(
                'SELECT warning_count FROM author_warnings WHERE user_cookie = ? AND author_id = ?',
                (cookie, author_id)
            ).fetchone()
            
            if not result or result[0] == 0:
                return 0, 0
            
            old_count = result[0]
            new_count = max(0, old_count - reduce_count)
            
            # 更新警告次数
            conn.execute('''
                UPDATE author_warnings SET warning_count = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_cookie = ? AND author_id = ?
            ''', (new_count, cookie, author_id))
            
            if self.logger:
                self.logger.info(f"匿名反馈系统 - 减少帖主警告: cookie={cookie[:8]}, author_id={author_id}, {old_count}->{new_count}")
            
            return old_count, new_count
    
    def _is_banned_from_author(self, cookie: str, author_id: int) -> bool:
        """检查用户是否被特定帖主封禁（警告次数>=3）"""
        return self._get_author_warning_count(cookie, author_id) >= 3
    
    def _mark_feedback_deleted(self, feedback_id: int):
        """标记反馈为已删除"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE feedback SET is_deleted = 1 WHERE id = ?', (feedback_id,))
            if self.logger:
                self.logger.info(f"匿名反馈系统 - 反馈标记删除: feedback_id={feedback_id}")

    def _parse_discord_url(self, url: str) -> Optional[tuple]:
        """解析Discord链接"""
        match = re.match(r'https://discord\.com/channels/(\d+)/(\d+)/(\d+)', url)
        return (int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None
    
    def _check_user_permissions(self, cookie: str, thread_id: int, guild_id: int) -> tuple[bool, str]:
        """检查用户权限，返回(是否允许, 错误消息)"""
        # 检查用户是否被全局封禁
        with sqlite3.connect(self.db_path) as conn:
            user_data = conn.execute('SELECT is_banned, warning_count FROM users WHERE user_cookie = ?', (cookie,)).fetchone()
            if user_data and user_data[0]:  # is_banned = 1
                return False, "❌ 您已被管理员封禁，无法使用匿名反馈功能"
        
        # 获取线程信息以确定帖主
        thread = None
        author_id = None
        
        try:
            # 尝试从Discord API获取线程信息
            guild = self.bot.get_guild(guild_id)
            if guild:
                thread = guild.get_thread(thread_id)
                if thread and hasattr(thread, 'owner_id'):
                    author_id = thread.owner_id
        except Exception as e:
            if self.logger:
                self.logger.warning(f"匿名反馈系统 - 获取线程信息失败: {e}")
        
        # 检查是否被特定帖主封禁
        if author_id and self._is_banned_from_author(cookie, author_id):
            warning_count = self._get_author_warning_count(cookie, author_id)
            return False, f"❌ 您已被该帖主封禁（{warning_count}次警告），无法在其帖子下发送匿名反馈"
        
        # 检查频率限制
        if self._get_recent_feedback_count_in_thread(cookie, thread_id) >= 20:
            return False, "❌ 您在此帖子中24小时内的反馈次数已达上限（20次），请稍后再试"
        
        return True, ""
    
    async def _validate_thread_author(self, interaction: discord.Interaction, feedback: dict) -> tuple[bool, str, Optional[discord.Thread]]:
        """验证帖主身份，返回(是否通过, 错误消息, 目标线程)"""
        thread_id = feedback.get('target_thread_id')
        if not thread_id:
            return False, "❌ 反馈记录缺少线程信息", None
        
        # 确保thread_id是有效的数字
        try:
            thread_id = int(thread_id)
            if thread_id <= 0:
                raise ValueError("无效的线程ID")
        except (ValueError, TypeError):
            if self.logger:
                self.logger.error(f"匿名反馈系统 - 无效的线程ID: {thread_id}, 类型: {type(thread_id)}")
            return False, f"❌ 反馈记录包含无效的线程ID: {thread_id}", None
        
        # 获取线程对象
        thread = await self._get_thread_by_id(interaction.guild.id, thread_id)
        if not thread:
            return False, f"❌ 无法访问线程 ID: {thread_id}", None
        
        # 检查是否为论坛帖子
        if not isinstance(thread, discord.Thread) or not hasattr(thread, 'owner_id'):
            return False, "❌ 该功能仅限论坛帖子使用", None
        
        # 检查帖主身份
        if thread.owner_id != interaction.user.id:
            return False, f"❌ 您不是该帖帖主\n帖主: <@{thread.owner_id}>", thread
        
        return True, "", thread
    
    async def _send_user_notification(self, user_id: int, message: str):
        """发送用户通知"""
        try:
            user = await self.bot.fetch_user(user_id)
            await user.send(message)
            if self.logger:
                self.logger.info(f"匿名反馈系统 - 用户通知发送成功: user_id={user_id}")
        except Exception as e:
            if self.logger:
                self.logger.warning(f"匿名反馈系统 - 无法发送用户通知到{user_id}: {e}")
    
    async def _get_thread_by_id(self, guild_id: int, thread_id: int) -> Optional[discord.Thread]:
        """根据ID获取线程"""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return None
        
        # 尝试直接获取线程
        thread = guild.get_thread(thread_id)
        if thread:
            return thread
        
        # 遍历所有频道寻找线程
        for channel in guild.channels:
            if hasattr(channel, 'threads'):
                try:
                    for thread in channel.threads:
                        if thread.id == thread_id:
                            return thread
                        
                    # 检查归档线程
                    async for thread in channel.archived_threads(limit=100):
                        if thread.id == thread_id:
                            return thread
                except:
                    continue
        
        return None
    
    async def _send_feedback(self, thread: discord.Thread, content: str, file_url: str = None, guild_feedback_id: int = None):
        """发送反馈消息并返回消息对象"""
        # 格式化反馈编号为6位数
        formatted_id = f"{guild_feedback_id:06d}" if guild_feedback_id else "000000"
        
        # 获取当前时间
        now = datetime.now(timezone.utc)
        
        embed = discord.Embed(
            title="📫 匿名反馈",
            description=content if content else "（文件反馈）",
            color=discord.Color.blue(),
            timestamp=now
        )
        
        # 设置footer，去掉多余的时间显示
        footer_text = f"反馈编号: {formatted_id} | 👎 达到10个自动删除"
        embed.set_footer(text=footer_text)
        
        if file_url:
            embed.set_image(url=file_url)
        
        return await thread.send(embed=embed)
    
    # 事件监听器
    @commands.Cog.listener()
    async def on_ready(self):
        if self.logger:
            self.logger.info("匿名反馈系统 - 模块已加载")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """处理私聊文件反馈"""
        # 忽略bot消息和非私聊消息
        if message.author.bot or not isinstance(message.channel, discord.DMChannel):
            return
        
        # 检查是否有附件
        if not message.attachments:
            return
        
        # 清理过期请求
        self._cleanup_expired_requests()
        
        # 检查是否有pending request
        user_id = message.author.id
        if user_id not in self.pending_file_requests:
            return
        
        request = self.pending_file_requests[user_id]
        
        # 检查是否过期
        if (datetime.now(timezone.utc) - request['timestamp']).total_seconds() > 300:  # 5分钟
            del self.pending_file_requests[user_id]
            try:
                await message.author.send("❌ 文件反馈请求已过期（超过5分钟），请重新使用命令")
            except:
                pass
            return
        
        # 处理第一个附件
        attachment = message.attachments[0]
        expected_type = request['type']
        
        # 验证文件
        is_valid, error_msg = self._validate_file(attachment, expected_type)
        if not is_valid:
            try:
                await message.author.send(error_msg)
            except:
                pass
            return
        
        # 获取文件URL
        file_url = attachment.url
        
        try:
            # 添加反馈记录到数据库
            feedback_id = await self._create_file_feedback_record(request, file_url, attachment.filename)
            
            # 获取目标线程
            thread = await self._get_thread_by_id(request['guild_id'], request['thread_id'])
            if not thread:
                try:
                    await message.author.send("❌ 无法访问目标帖子，反馈失败")
                except:
                    pass
                return
            
            # 发送反馈到线程
            sent_message = await self._send_feedback(
                thread, 
                request.get('description') or (message.content if message.content.strip() else None),
                file_url, 
                request['guild_feedback_id']
            )
            
            # 更新消息ID
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('UPDATE feedback SET message_id = ? WHERE id = ?', (sent_message.id, feedback_id))
            
            # 清理pending request
            del self.pending_file_requests[user_id]
            
            type_text = "图片" if expected_type == "image" else "文件"
            try:
                await message.author.send(f"✅ {type_text}反馈已发送！反馈编号: {request['guild_feedback_id']:06d}")
            except:
                pass
            
            if self.logger:
                self.logger.info(f"匿名反馈系统 - 文件反馈发送成功: guild_id={request['guild_id']}, feedback_id={request['guild_feedback_id']}, user={user_id}, type={expected_type}")
                
        except Exception as e:
            if self.logger:
                self.logger.error(f"匿名反馈系统 - 处理文件反馈失败: {e}")
            try:
                await message.author.send(f"❌ 发送失败: {str(e)}")
            except:
                pass

    async def _create_file_feedback_record(self, request: dict, file_url: str, filename: str) -> int:
        """创建文件反馈记录"""
        # 构建内容描述
        content_parts = []
        if request.get('description'):
            content_parts.append(request['description'])
        content_parts.append(f"文件名: {filename}")
        content = " | ".join(content_parts)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                INSERT INTO feedback (guild_feedback_id, user_cookie, guild_id, target_url, target_thread_id, content_type, file_url, content)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                request['guild_feedback_id'], 
                request['user_cookie'], 
                request['guild_id'], 
                request['target_url'], 
                request['thread_id'], 
                request['type'], 
                file_url,
                content
            ))
            return cursor.lastrowid

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """处理踩数反应"""
        if payload.emoji.name == "👎" and not payload.member.bot:
            try:
                await self._process_downvote_reaction(payload)
            except Exception as e:
                if self.logger:
                    self.logger.error(f"匿名反馈系统 - 处理踩数反应时出错: {e}")

    async def _process_downvote_reaction(self, payload: discord.RawReactionActionEvent):
        """处理踩数反应的核心逻辑"""
        # 查找对应的反馈记录
        with sqlite3.connect(self.db_path) as conn:
            feedback_result = conn.execute('''
                SELECT f.*, u.user_id FROM feedback f
                JOIN users u ON f.user_cookie = u.user_cookie
                WHERE f.message_id = ? AND f.is_deleted = 0
            ''', (payload.message_id,)).fetchone()
            
            if not feedback_result:
                return
                
            # 增加踩数
            conn.execute('''
                INSERT OR REPLACE INTO downvote_records (message_id, feedback_id, guild_id, downvote_count)
                VALUES (?, ?, ?, COALESCE((SELECT downvote_count FROM downvote_records WHERE message_id = ?), 0) + 1)
            ''', (payload.message_id, feedback_result[0], payload.guild_id, payload.message_id))
            
            # 获取当前踩数
            downvote_count = conn.execute('SELECT downvote_count FROM downvote_records WHERE message_id = ?', 
                                        (payload.message_id,)).fetchone()[0]
            
            # 检查是否达到阈值
            if downvote_count >= 10:
                await self._handle_downvote_threshold(feedback_result, downvote_count, payload)

    async def _handle_downvote_threshold(self, feedback_data: tuple, downvote_count: int, payload: discord.RawReactionActionEvent):
        """处理达到踩数阈值的情况"""
        feedback_id, guild_feedback_id, user_cookie = feedback_data[0], feedback_data[1], feedback_data[2]
        target_thread_id = feedback_data[5]
        user_id = feedback_data[11]
        
        # 获取帖主ID
        thread = await self._get_thread_by_id(payload.guild_id, target_thread_id)
        if not thread or not hasattr(thread, 'owner_id'):
            if self.logger:
                self.logger.warning(f"匿名反馈系统 - 无法确定反馈#{guild_feedback_id}的帖主，跳过按帖主封禁")
            return
        
        author_id = thread.owner_id
        
        # 使用新的按帖主警告机制
        warning_count = self._add_author_warning(user_cookie, author_id, "downvote", feedback_id, None, f"反馈#{guild_feedback_id}被踩{downvote_count}次")
        
        # 标记反馈为已删除
        self._mark_feedback_deleted(feedback_id)
        
        if warning_count >= 3:
            # 达到封禁阈值
            await self._send_user_notification(
                user_id, 
                f"⚠️ 您的匿名反馈被删除，这是您在该帖主帖子下的第{warning_count}次警告\n"
                f"原因：反馈收到{downvote_count}个👎举报\n\n"
                f"由于累计警告已达到3次，您已被该帖主封禁，无法在其所有帖子下发送匿名反馈。如有异议请联系管理员。"
            )
        else:
            # 仅警告
            await self._send_user_notification(
                user_id, 
                f"⚠️ 您的匿名反馈被删除，这是您在该帖主帖子下的第{warning_count}次警告\n"
                f"原因：反馈收到{downvote_count}个👎举报\n\n"
                f"请注意改善反馈质量，在该帖主帖子下累计3次警告将被封禁。"
            )
        
        # 删除消息
        try:
            channel = self.bot.get_channel(payload.channel_id)
            if channel:
                message = await channel.fetch_message(payload.message_id)
                await message.delete()
                if self.logger:
                    self.logger.info(f"匿名反馈系统 - 删除消息: message_id={payload.message_id}, 踩数={downvote_count}, 帖主警告={warning_count}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"匿名反馈系统 - 删除消息失败: {e}")

    # 基本功能命令
    @feedback.command(name="消息", description="发送匿名文字反馈")
    @app_commands.describe(内容="反馈内容")
    async def send_text_feedback(self, interaction: discord.Interaction, 内容: str):
        await interaction.response.defer(ephemeral=True)
        
        # 自动获取当前帖子链接
        帖子链接 = self._get_current_thread_url(interaction)
        if not 帖子链接:
            await interaction.followup.send("❌ 此命令只能在论坛频道的帖子中使用", ephemeral=True)
            return
        
        # 验证链接格式
        parsed = self._parse_discord_url(帖子链接)
        if not parsed:
            await interaction.followup.send("❌ 无法解析当前帖子链接", ephemeral=True)
            return
        
        guild_id, thread_id, message_id = parsed
        
        # 验证是否在正确的服务器
        if guild_id != interaction.guild.id:
            await interaction.followup.send("❌ 只能对当前服务器的帖子进行反馈", ephemeral=True)
            return
        
        # 获取线程并验证是否为论坛帖子
        thread = await self._get_thread_by_id(guild_id, thread_id)
        if not thread or not isinstance(thread, discord.Thread):
            await interaction.followup.send("❌ 该功能仅限在论坛频道下的帖子中使用", ephemeral=True)
            return
        
        # 注册用户并获取cookie
        cookie = self._register_user(interaction.user.id, guild_id)
        
        # 检查用户权限
        is_allowed, error_msg = self._check_user_permissions(cookie, thread_id, guild_id)
        if not is_allowed:
            await interaction.followup.send(error_msg, ephemeral=True)
            return
        
        # 生成反馈编号
        with sqlite3.connect(self.db_path) as conn:
            # 获取下一个反馈编号
            result = conn.execute('SELECT next_feedback_id FROM guild_sequences WHERE guild_id = ?', (guild_id,)).fetchone()
            guild_feedback_id = result[0] if result else 1
            
            # 更新序列号
            conn.execute('INSERT OR REPLACE INTO guild_sequences (guild_id, next_feedback_id) VALUES (?, ?)',
                        (guild_id, guild_feedback_id + 1))
        
            # 添加反馈记录
            cursor = conn.execute('''
                INSERT INTO feedback (guild_feedback_id, user_cookie, guild_id, target_url, target_thread_id, content_type, content)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (guild_feedback_id, cookie, guild_id, 帖子链接, thread_id, "text", 内容))
            
            feedback_id = cursor.lastrowid
            
        # 发送反馈
        try:
            sent_message = await self._send_feedback(thread, 内容, guild_feedback_id=guild_feedback_id)
            
            # 更新消息ID
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('UPDATE feedback SET message_id = ? WHERE id = ?', (sent_message.id, feedback_id))
            
            if self.logger:
                self.logger.info(f"匿名反馈系统 - 匿名反馈发送成功: guild_id={guild_id}, feedback_id={guild_feedback_id}, user={interaction.user.id}")
            
            await interaction.followup.send(f"✅ 匿名反馈已发送！反馈编号: {guild_feedback_id:06d}", ephemeral=True)
        except Exception as e:
            if self.logger:
                self.logger.error(f"匿名反馈系统 - 发送反馈失败: {e}")
            await interaction.followup.send(f"❌ 发送失败: {str(e)}", ephemeral=True)
    
    @feedback.command(name="图片", description="发送匿名图片反馈")
    @app_commands.describe(说明="图片说明（可选）")
    async def send_image_feedback(self, interaction: discord.Interaction, 说明: str = None):
        # 自动获取当前帖子链接
        帖子链接 = self._get_current_thread_url(interaction)
        if not 帖子链接:
            await interaction.response.send_message("❌ 此命令只能在论坛频道的帖子中使用", ephemeral=True)
            return
        await self._handle_file_feedback_setup(interaction, 帖子链接, "image", 说明)
    
    @feedback.command(name="文件", description="发送匿名文件反馈")
    @app_commands.describe(说明="文件说明（可选）")
    async def send_file_feedback(self, interaction: discord.Interaction, 说明: str = None):
        # 自动获取当前帖子链接
        帖子链接 = self._get_current_thread_url(interaction)
        if not 帖子链接:
            await interaction.response.send_message("❌ 此命令只能在论坛频道的帖子中使用", ephemeral=True)
            return
        await self._handle_file_feedback_setup(interaction, 帖子链接, "file", 说明)

    def _get_current_thread_url(self, interaction: discord.Interaction) -> Optional[str]:
        """获取当前帖子的URL"""
        # 检查是否在线程中
        if not isinstance(interaction.channel, discord.Thread):
            return None
        
        # 检查是否为论坛帖子
        parent = interaction.channel.parent
        if not isinstance(parent, discord.ForumChannel):
            return None
        
        # 生成帖子URL - 使用帖子的starter_message ID
        thread = interaction.channel
        if thread.starter_message:
            message_id = thread.starter_message.id
        else:
            # 如果没有starter_message，使用thread_id作为message_id
            message_id = thread.id
            
        return f"https://discord.com/channels/{interaction.guild.id}/{thread.id}/{message_id}"

    async def _handle_file_feedback_setup(self, interaction: discord.Interaction, 帖子链接: str, file_type: str, 说明: str = None):
        """设置文件反馈的时间窗口"""
        # 检查是否已经响应过
        if interaction.response.is_done():
            if self.logger:
                self.logger.warning("交互已经响应过，跳过")
            return
            
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.errors.NotFound:
            if self.logger:
                self.logger.error("交互已过期，无法响应")
            return
        except Exception as e:
            if self.logger:
                self.logger.error(f"匿名反馈系统 - 响应交互失败: {e}")
            return
        
        # 验证链接格式
        parsed = self._parse_discord_url(帖子链接)
        if not parsed:
            try:
                await interaction.followup.send("❌ 无效的Discord链接格式", ephemeral=True)
            except:
                pass
            return
        
        guild_id, thread_id, message_id = parsed
        
        # 验证服务器
        if guild_id != interaction.guild.id:
            try:
                await interaction.followup.send("❌ 只能对当前服务器的帖子进行反馈", ephemeral=True)
            except:
                pass
            return
        
        # 验证论坛帖子
        thread = await self._get_thread_by_id(guild_id, thread_id)
        if not thread or not isinstance(thread, discord.Thread):
            try:
                await interaction.followup.send("❌ 该功能仅限在论坛频道下的帖子中使用", ephemeral=True)
            except:
                pass
            return
        
        cookie = self._register_user(interaction.user.id, guild_id)
        
        # 检查用户权限
        is_allowed, error_msg = self._check_user_permissions(cookie, thread_id, guild_id)
        if not is_allowed:
            try:
                await interaction.followup.send(error_msg, ephemeral=True)
            except:
                pass
            return
        
        # 生成反馈编号
        with sqlite3.connect(self.db_path) as conn:
            # 获取下一个反馈编号
            result = conn.execute('SELECT next_feedback_id FROM guild_sequences WHERE guild_id = ?', (guild_id,)).fetchone()
            guild_feedback_id = result[0] if result else 1
            
            # 更新序列号
            conn.execute('INSERT OR REPLACE INTO guild_sequences (guild_id, next_feedback_id) VALUES (?, ?)',
                        (guild_id, guild_feedback_id + 1))
        
        # 清理过期的pending requests
        self._cleanup_expired_requests()
        
        # 创建pending request
        self.pending_file_requests[interaction.user.id] = {
            'target_url': 帖子链接,
            'thread_id': thread_id,
            'guild_id': guild_id,
            'type': file_type,
            'timestamp': datetime.now(timezone.utc),
            'guild_feedback_id': guild_feedback_id,
            'user_cookie': cookie,
            'description': 说明
        }
        
        # 发送简单提示
        type_text = "图片" if file_type == "image" else "文件"
        format_list = "jpg、png、gif、webp等图片格式" if file_type == "image" else "pdf、doc、txt、zip、mp4、mp3等文件格式"
        
        description_hint = f"\n💬 说明：{说明}" if 说明 else ""
        
        try:
            await interaction.followup.send(
                f"📎 **{type_text}反馈已准备就绪**\n\n"
                f"请在 **5分钟内** 私聊机器人发送{type_text}即可完成匿名反馈\n"
                f"📋 支持格式：{format_list}\n"
                f"📏 大小限制：25MB以内{description_hint}\n\n"
                f"💡 无需包含任何链接，直接发送{type_text}即可！", 
                ephemeral=True
            )
        except:
            pass
        
        if self.logger:
            self.logger.info(f"匿名反馈系统 - 创建{type_text}反馈请求: user={interaction.user.id}, feedback_id={guild_feedback_id}, description={说明}")

    def _cleanup_expired_requests(self):
        """清理过期的pending requests"""
        now = datetime.now(timezone.utc)
        expired_users = []
        
        for user_id, request in self.pending_file_requests.items():
            if (now - request['timestamp']).total_seconds() > 300:  # 5分钟
                expired_users.append(user_id)
        
        for user_id in expired_users:
            del self.pending_file_requests[user_id]
            if self.logger:
                self.logger.info(f"匿名反馈系统 - 清理过期文件请求: user={user_id}")

    def _validate_file(self, attachment: discord.Attachment, expected_type: str) -> tuple[bool, str]:
        """验证文件格式和大小"""
        filename = attachment.filename.lower()
        file_ext = pathlib.Path(filename).suffix.lower()
        
        # 检查文件大小
        if attachment.size > self.max_file_size:
            return False, f"❌ 文件大小超过限制（{attachment.size / 1024 / 1024:.1f}MB > 25MB）"
        
        # 检查文件格式
        if expected_type == "image":
            if file_ext not in self.image_extensions:
                return False, f"❌ 不支持的图片格式：{file_ext}\n支持格式：{', '.join(self.image_extensions)}"
        else:  # file
            all_extensions = self.file_extensions | self.image_extensions
            if file_ext not in all_extensions:
                return False, f"❌ 不支持的文件格式：{file_ext}\n支持格式：{', '.join(sorted(all_extensions))}"
        
        return True, ""

    # ===== 管理员功能 =====
    @admin_feedback.command(name="封禁", description="封禁用户使用匿名反馈功能（管理员专用）")
    @app_commands.describe(用户="要封禁的用户", 原因="封禁原因")
    async def admin_ban_user(self, interaction: discord.Interaction, 用户: discord.Member, 原因: str = "违规行为"):
        """管理员封禁用户"""
        if not self.is_admin(interaction.user):
            await interaction.response.send_message("❌ 此命令仅限管理员使用", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        guild_id = interaction.guild.id
        cookie = self._get_user_cookie(用户.id, guild_id)
        
        # 记录管理员封禁
        with sqlite3.connect(self.db_path) as conn:
            # 获取用户当前警告数
            user_data = conn.execute('SELECT warning_count FROM users WHERE user_cookie = ?', (cookie,)).fetchone()
            current_warnings = user_data[0] if user_data else 0
            
            # 直接设置为封禁状态（3次警告）
            conn.execute('''
                INSERT OR REPLACE INTO users (user_cookie, user_id, guild_id, warning_count, is_banned)
                VALUES (?, ?, ?, 3, 1)
            ''', (cookie, 用户.id, guild_id))
            
            # 记录警告详情
            conn.execute('''
                INSERT INTO warning_records (user_cookie, guild_id, warning_type, moderator_id, reason)
                VALUES (?, ?, ?, ?, ?)
            ''', (cookie, guild_id, "admin_ban", interaction.user.id, 原因))
        
        # 通知被封禁用户
        await self._send_user_notification(
            用户.id,
            f"🚫 您已被管理员 <@{interaction.user.id}> 封禁，无法使用匿名反馈功能。\n"
            f"原因：{原因}\n"
            f"如有异议请联系其他管理员。"
        )
        
        await interaction.followup.send(f"✅ 已封禁用户 {用户.mention} 的匿名反馈功能", ephemeral=True)
        
        if self.logger:
            self.logger.info(f"匿名反馈系统 - 管理员封禁用户: admin={interaction.user.id}, target={用户.id}, reason={原因}")

    @admin_feedback.command(name="查询反馈", description="查询匿名反馈详情（管理员专用）")
    @app_commands.describe(反馈编号="反馈编号（6位数字）")
    async def admin_query_feedback(self, interaction: discord.Interaction, 反馈编号: int):
        """管理员查询匿名反馈详情"""
        if not self.is_admin(interaction.user):
            await interaction.response.send_message("❌ 此命令仅限管理员使用", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        guild_id = interaction.guild.id
        
        # 查询反馈信息
        with sqlite3.connect(self.db_path) as conn:
            feedback_data = conn.execute('''
                SELECT f.id, f.guild_feedback_id, f.target_url, f.target_thread_id, 
                       f.content_type, f.content, f.file_url, f.message_id, f.created_at,
                       u.user_id, f.is_deleted
                FROM feedback f
                JOIN users u ON f.user_cookie = u.user_cookie
                WHERE f.guild_id = ? AND f.guild_feedback_id = ?
            ''', (guild_id, 反馈编号)).fetchone()
        
        if not feedback_data:
            await interaction.followup.send(f"❌ 未找到反馈编号 {反馈编号:06d}", ephemeral=True)
            return
        
        (feedback_id, guild_feedback_id, target_url, target_thread_id, 
         content_type, content, file_url, message_id, created_at, user_id, is_deleted) = feedback_data
        
        # 构建响应
        embed = discord.Embed(
            title=f"🔍 反馈详情 #{guild_feedback_id:06d}",
            color=discord.Color.red() if is_deleted else discord.Color.blue()
        )
        
        embed.add_field(name="📍 发送者", value=f"<@{user_id}> (ID: {user_id})", inline=True)
        embed.add_field(name="📅 时间", value=f"<t:{int(datetime.fromisoformat(created_at.replace('Z', '+00:00')).timestamp())}:F>", inline=True)
        embed.add_field(name="🏷️ 状态", value="已删除" if is_deleted else "正常", inline=True)
        
        embed.add_field(name="🔗 目标帖子", value=f"[点击跳转]({target_url})", inline=False)
        
        if content:
            embed.add_field(name="📝 内容", value=content[:1000] + ("..." if len(content) > 1000 else ""), inline=False)
        
        if file_url:
            embed.add_field(name="📎 文件", value=f"[查看文件]({file_url})", inline=False)
        
        if message_id:
            embed.add_field(name="💬 消息ID", value=str(message_id), inline=True)
        
        embed.set_footer(text=f"反馈ID: {feedback_id} | 查询者: {interaction.user}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        if self.logger:
            self.logger.info(f"匿名反馈系统 - 管理员查询反馈: admin={interaction.user.id}, feedback_id={guild_feedback_id}")

    @admin_feedback.command(name="删除反馈", description="删除匿名反馈（管理员专用）")
    @app_commands.describe(反馈编号="反馈编号（6位数字）", 原因="删除原因")
    async def admin_delete_feedback(self, interaction: discord.Interaction, 反馈编号: int, 原因: str = "违规内容"):
        """管理员删除匿名反馈"""
        if not self.is_admin(interaction.user):
            await interaction.response.send_message("❌ 此命令仅限管理员使用", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        guild_id = interaction.guild.id
        
        # 查询反馈信息
        with sqlite3.connect(self.db_path) as conn:
            feedback_data = conn.execute('''
                SELECT f.id, f.message_id, f.target_thread_id, u.user_id, f.is_deleted, u.user_cookie
                FROM feedback f
                JOIN users u ON f.user_cookie = u.user_cookie
                WHERE f.guild_id = ? AND f.guild_feedback_id = ?
            ''', (guild_id, 反馈编号)).fetchone()
        
        if not feedback_data:
            await interaction.followup.send(f"❌ 未找到反馈编号 {反馈编号:06d}", ephemeral=True)
            return
        
        feedback_id, message_id, target_thread_id, user_id, is_deleted, user_cookie = feedback_data
        
        if is_deleted:
            await interaction.followup.send(f"❌ 反馈 #{反馈编号:06d} 已被删除", ephemeral=True)
            return
        
        # 获取帖主ID
        thread = await self._get_thread_by_id(guild_id, target_thread_id)
        if not thread or not hasattr(thread, 'owner_id'):
            await interaction.followup.send("❌ 无法确定帖主信息，删除失败", ephemeral=True)
            if self.logger:
                self.logger.warning(f"匿名反馈系统 - 管理员删除反馈#{反馈编号}失败：无法确定帖主")
            return
        
        author_id = thread.owner_id
        
        # 标记为已删除
        self._mark_feedback_deleted(feedback_id)
        
        # 删除Discord消息
        if message_id:
            try:
                if thread:
                    message = await thread.fetch_message(message_id)
                    await message.delete()
            except discord.NotFound:
                pass
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"匿名反馈系统 - 删除反馈消息失败: {e}")
        
        # 使用按帖主的警告机制（与踩数和帖主封禁统一）
        warning_count = self._add_author_warning(user_cookie, author_id, "admin_delete", feedback_id, interaction.user.id, 原因)
        
        # 根据警告次数发送不同的通知
        if warning_count >= 3:
            # 达到封禁阈值
            await self._send_user_notification(
                user_id,
                f"🚫 您的匿名反馈#{反馈编号:06d}因 {原因} 被管理员删除\n"
                f"这是您在该帖主帖子下的第{warning_count}次警告\n\n"
                f"由于累计警告已达到3次，您已被该帖主封禁，无法在其所有帖子下发送匿名反馈。如有异议请联系管理员。"
            )
            result_message = f"✅ 已删除反馈 #{反馈编号:06d} 并封禁用户 <@{user_id}>（该用户在帖主 <@{author_id}> 下累计{warning_count}次警告）"
        else:
            # 仅警告
            await self._send_user_notification(
                user_id,
                f"⚠️ 您的匿名反馈#{反馈编号:06d}因 {原因} 被管理员删除\n"
                f"这是您在该帖主帖子下的第{warning_count}次警告\n\n"
                f"请注意改善反馈质量，在该帖主帖子下累计3次警告将被封禁。"
            )
            result_message = f"✅ 已删除反馈 #{反馈编号:06d} 并警告用户 <@{user_id}>（{warning_count}/3次，帖主: <@{author_id}>）"
        
        await interaction.followup.send(result_message, ephemeral=True)
        
        if self.logger:
            self.logger.info(f"匿名反馈系统 - 管理员删除反馈: admin={interaction.user.id}, feedback_id={反馈编号}, author_id={author_id}, warnings={warning_count}, reason={原因}")

    @admin_feedback.command(name="用户统计", description="查看用户反馈统计（管理员专用）")
    @app_commands.describe(用户="要查询的用户")
    async def admin_user_stats(self, interaction: discord.Interaction, 用户: discord.Member):
        """管理员查看用户反馈统计"""
        if not self.is_admin(interaction.user):
            await interaction.response.send_message("❌ 此命令仅限管理员使用", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        guild_id = interaction.guild.id
        cookie = self._get_user_cookie(用户.id, guild_id)
        
        with sqlite3.connect(self.db_path) as conn:
            # 获取基本统计
            total_count = conn.execute(
                'SELECT COUNT(*) FROM feedback WHERE user_cookie = ? AND guild_id = ?',
                (cookie, guild_id)
            ).fetchone()[0]
            
            deleted_count = conn.execute(
                'SELECT COUNT(*) FROM feedback WHERE user_cookie = ? AND guild_id = ? AND is_deleted = 1',
                (cookie, guild_id)
            ).fetchone()[0]
            
            warning_count = conn.execute(
                'SELECT warning_count FROM users WHERE user_cookie = ?',
                (cookie,)
            ).fetchone()
            warning_count = warning_count[0] if warning_count else 0
            
            # 获取最近反馈
            recent_feedback = conn.execute('''
                SELECT guild_feedback_id, target_thread_id, created_at, is_deleted
                FROM feedback 
                WHERE user_cookie = ? AND guild_id = ? 
                ORDER BY created_at DESC LIMIT 5
            ''', (cookie, guild_id)).fetchall()
        
        embed = discord.Embed(
            title=f"📊 用户反馈统计",
            description=f"**用户:** {用户.mention} (ID: {用户.id})",
            color=discord.Color.orange()
        )
        
        embed.add_field(name="📝 总反馈数", value=str(total_count), inline=True)
        embed.add_field(name="🗑️ 被删除数", value=str(deleted_count), inline=True)
        embed.add_field(name="⚠️ 警告次数", value=str(warning_count), inline=True)
        
        # 最近反馈
        if recent_feedback:
            recent_text = ""
            for fb_id, thread_id, created_at, is_deleted in recent_feedback:
                status = "🗑️" if is_deleted else "✅"
                time_stamp = datetime.fromisoformat(created_at.replace('Z', '+00:00')).timestamp()
                recent_text += f"{status} #{fb_id:06d} - <t:{int(time_stamp)}:R>\n"
            embed.add_field(name="🕒 最近反馈", value=recent_text, inline=False)
        
        embed.set_footer(text=f"查询者: {interaction.user}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        if self.logger:
            self.logger.info(f"匿名反馈系统 - 管理员查询用户统计: admin={interaction.user.id}, target={用户.id}")

    # ===== 帖主功能 =====
    @author_feedback.command(name="溯源反馈", description="查看自己帖子中的匿名反馈者身份（仅帖主可用）")
    @app_commands.describe(反馈编号="反馈编号（6位数字）")
    async def author_trace_feedback(self, interaction: discord.Interaction, 反馈编号: int):
        """帖主溯源匿名反馈"""
        await interaction.response.defer(ephemeral=True)
        
        guild_id = interaction.guild.id
        
        # 查询反馈信息
        with sqlite3.connect(self.db_path) as conn:
            feedback_data = conn.execute('''
                SELECT f.id, f.target_thread_id, f.target_url, f.content_type, 
                       f.content, f.file_url, f.created_at, u.user_id, f.is_deleted
                FROM feedback f
                JOIN users u ON f.user_cookie = u.user_cookie
                WHERE f.guild_id = ? AND f.guild_feedback_id = ?
            ''', (guild_id, 反馈编号)).fetchone()
        
        if not feedback_data:
            await interaction.followup.send(f"❌ 未找到反馈编号 {反馈编号:06d}", ephemeral=True)
            return
        
        (feedback_id, target_thread_id, target_url, content_type, 
         content, file_url, created_at, user_id, is_deleted) = feedback_data
        
        # 验证是否为帖主
        is_valid, error_msg, thread = await self._validate_thread_author(interaction, {
            'target_thread_id': target_thread_id,
            'guild_id': guild_id
        })
        
        if not is_valid:
            await interaction.followup.send(error_msg, ephemeral=True)
            return
        
        # 构建溯源信息
        embed = discord.Embed(
            title=f"🔍 帖主溯源 #{反馈编号:06d}",
            description=f"**目标帖子:** {thread.mention}",
            color=discord.Color.red() if is_deleted else discord.Color.green()
        )
        
        embed.add_field(name="👤 反馈者", value=f"<@{user_id}> (ID: {user_id})", inline=True)
        embed.add_field(name="📅 时间", value=f"<t:{int(datetime.fromisoformat(created_at.replace('Z', '+00:00')).timestamp())}:F>", inline=True)
        embed.add_field(name="🏷️ 状态", value="已删除" if is_deleted else "正常", inline=True)
        
        if content:
            embed.add_field(name="📝 内容", value=content[:1000] + ("..." if len(content) > 1000 else ""), inline=False)
        
        if file_url:
            embed.add_field(name="📎 文件", value=f"[查看文件]({file_url})", inline=False)
        
        embed.set_footer(text="⚠️ 此信息仅供帖主参考，请勿滥用")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        if self.logger:
            self.logger.info(f"匿名反馈系统 - 帖主溯源反馈: author={interaction.user.id}, feedback_id={反馈编号}, target_user={user_id}")

    @author_feedback.command(name="封禁反馈用户", description="封禁用户在自己帖子下发送匿名反馈（仅帖主可用）")
    @app_commands.describe(反馈编号="反馈编号（6位数字）", 原因="封禁原因")
    async def author_block_user(self, interaction: discord.Interaction, 反馈编号: int, 原因: str = "不当反馈"):
        """帖主封禁用户"""
        await interaction.response.defer(ephemeral=True)
        
        guild_id = interaction.guild.id
        
        # 查询反馈信息
        with sqlite3.connect(self.db_path) as conn:
            feedback_data = conn.execute('''
                SELECT f.id, f.target_thread_id, u.user_id, u.user_cookie
                FROM feedback f
                JOIN users u ON f.user_cookie = u.user_cookie
                WHERE f.guild_id = ? AND f.guild_feedback_id = ?
            ''', (guild_id, 反馈编号)).fetchone()
        
        if not feedback_data:
            await interaction.followup.send(f"❌ 未找到反馈编号 {反馈编号:06d}", ephemeral=True)
            return
        
        feedback_id, target_thread_id, user_id, user_cookie = feedback_data
        
        # 验证是否为帖主
        is_valid, error_msg, thread = await self._validate_thread_author(interaction, {
            'target_thread_id': target_thread_id,
            'guild_id': guild_id
        })
        
        if not is_valid:
            await interaction.followup.send(error_msg, ephemeral=True)
            return
        
        # 增加警告次数（针对该帖主）
        self._add_author_warning(user_cookie, interaction.user.id, "author_block", feedback_id, interaction.user.id, 原因)
        warning_count = self._get_author_warning_count(user_cookie, interaction.user.id)
        
        # 通知被封禁用户
        if warning_count >= 3:
            await self._send_user_notification(
                user_id,
                f"🚫 您已被帖主 <@{interaction.user.id}> 封禁，无法在其帖子下发送匿名反馈。\n"
                f"原因：{原因}\n"
                f"如有异议请联系管理员。"
            )
            result_msg = f"✅ 已封禁用户 <@{user_id}>，其无法再在您的帖子下发送反馈"
        else:
            await self._send_user_notification(
                user_id,
                f"⚠️ 您的反馈#{反馈编号:06d}被帖主标记。\n"
                f"原因：{原因}\n"
                f"这是您在该帖主下的第{warning_count}次警告，累计3次将被封禁。"
            )
            result_msg = f"✅ 已对用户 <@{user_id}> 发出警告（{warning_count}/3次）"
        
        await interaction.followup.send(result_msg, ephemeral=True)
        
        if self.logger:
            self.logger.info(f"匿名反馈系统 - 帖主封禁用户: author={interaction.user.id}, target={user_id}, warnings={warning_count}, reason={原因}")

    @author_feedback.command(name="减少警告", description="减少用户警告次数（仅帖主可用）")
    @app_commands.describe(用户="要减少警告的用户", 次数="减少的次数")
    async def author_reduce_warning(self, interaction: discord.Interaction, 用户: discord.Member, 次数: int = 1):
        """帖主减少用户警告"""
        await interaction.response.defer(ephemeral=True)
        
        guild_id = interaction.guild.id
        cookie = self._get_user_cookie(用户.id, guild_id)
        
        # 检查当前线程
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.followup.send("❌ 此命令只能在帖子中使用", ephemeral=True)
            return
        
        thread = interaction.channel
        if thread.owner_id != interaction.user.id and not self.is_admin(interaction.user):
            await interaction.followup.send(f"❌ 您不是该帖帖主\n帖主: <@{thread.owner_id}>", ephemeral=True)
            return
        
        # 验证次数参数
        if 次数 <= 0:
            await interaction.followup.send("❌ 减少次数必须大于0", ephemeral=True)
            return
        
        # 获取当前警告次数
        old_count = self._get_author_warning_count(cookie, interaction.user.id)
        
        if old_count == 0:
            await interaction.followup.send(f"❌ 用户 {用户.mention} 未被您警告过", ephemeral=True)
            return
        
        # 减少警告次数
        actual_reduce = min(次数, old_count)
        old_count, new_count = self._reduce_author_warning(cookie, interaction.user.id, actual_reduce)
        
        # 通知用户
        if new_count == 0:
            await self._send_user_notification(
                用户.id,
                f"✅ 帖主 <@{interaction.user.id}> 已清除您的所有警告，您现在可以正常在其帖子下发送匿名反馈了。"
            )
            result_msg = f"✅ 已清除用户 {用户.mention} 的所有警告"
        else:
            await self._send_user_notification(
                用户.id,
                f"📉 帖主 <@{interaction.user.id}> 已减少您的警告次数：{old_count} → {new_count}"
            )
            result_msg = f"✅ 已减少用户 {用户.mention} 的警告次数：{old_count} → {new_count}"
        
        await interaction.followup.send(result_msg, ephemeral=True)
        
        if self.logger:
            self.logger.info(f"匿名反馈系统 - 帖主减少警告: author={interaction.user.id}, target={用户.id}, {old_count}→{new_count}")


async def setup(bot):
    await bot.add_cog(AnonymousFeedbackCog(bot)) 