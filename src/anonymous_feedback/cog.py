import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import hashlib
import os
import json
import asyncio
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
import pathlib
import aiohttp
import io


class AnonymousFeedbackCog(commands.Cog):
    feedback = app_commands.Group(name="匿名反馈", description="匿名反馈功能")
    author_feedback = app_commands.Group(name="匿名反馈-帖主", description="帖主反馈管理功能")
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
            
            # 溯源记录表（保留，仅管理员使用）
            conn.execute('''
                CREATE TABLE IF NOT EXISTS trace_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    feedback_id INTEGER NOT NULL,
                    guild_feedback_id INTEGER NOT NULL,
                    traced_user_cookie TEXT NOT NULL,
                    traced_user_id INTEGER NOT NULL,
                    tracer_id INTEGER NOT NULL,
                    tracer_type TEXT NOT NULL,
                    guild_id INTEGER NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (feedback_id) REFERENCES feedback (id),
                    FOREIGN KEY (traced_user_cookie) REFERENCES users (user_cookie)
                )
            ''')
            
            # 新增：帖主全局封禁表
            conn.execute('''
                CREATE TABLE IF NOT EXISTS author_global_bans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    author_id INTEGER NOT NULL,
                    banned_user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    reason TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(author_id, banned_user_id, guild_id)
                )
            ''')
            
            # 新增：帖主禁用匿名功能表
            conn.execute('''
                CREATE TABLE IF NOT EXISTS author_anonymous_disabled (
                    author_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    reason TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(author_id, guild_id)
                )
            ''')
            
            conn.execute('CREATE INDEX IF NOT EXISTS idx_feedback_guild_thread ON feedback (guild_id, target_thread_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_users_guild_user ON users (guild_id, user_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_author_warnings_cookie_author ON author_warnings (user_cookie, author_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_trace_records_traced_user ON trace_records (traced_user_id, guild_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_trace_records_feedback ON trace_records (feedback_id)')
            # 新增索引
            conn.execute('CREATE INDEX IF NOT EXISTS idx_author_global_bans ON author_global_bans (author_id, banned_user_id, guild_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_author_anonymous_disabled ON author_anonymous_disabled (author_id, guild_id)')
            
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
            user_data = conn.execute('SELECT is_banned, warning_count, user_id FROM users WHERE user_cookie = ?', (cookie,)).fetchone()
            if user_data and user_data[0]:  # is_banned = 1
                return False, "❌ 您已被管理员封禁，无法使用匿名反馈功能"
        
        # 获取线程信息以确定帖主
        thread = None
        author_id = None
        user_id = user_data[2] if user_data else None
        
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
        
        if author_id:
            # 检查帖主是否禁用了匿名功能
            if self._is_anonymous_disabled_by_author(author_id, guild_id):
                return False, "❌ 该帖主已禁用匿名反馈功能"
            
            # 检查是否被帖主全局封禁
            if user_id and self._is_globally_banned_by_author(user_id, author_id, guild_id):
                return False, "❌ 您已被该帖主全局封禁，无法在其任何帖子下发送匿名反馈"
            
            # 检查是否被特定帖主封禁（原有的三次警告机制）
            if self._is_banned_from_author(cookie, author_id):
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
        
        # 设置footer，修改踩数阈值显示为6个
        footer_text = f"反馈编号: {formatted_id} | 👎 达到6个自动删除"
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
            if downvote_count >= 6:  # 从10改为6
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

    # 基本功能命令 - 合并为一个命令
    @feedback.command(name="发送", description="发送匿名反馈（支持文字、图片、文件）")
    @app_commands.describe(
        内容="反馈内容（必填）",
        图片1="第一张图片（可选）",
        图片2="第二张图片（可选）", 
        图片3="第三张图片（可选）",
        图片4="第四张图片（可选）",
        图片5="第五张图片（可选）",
        文件1="第一个文件附件（可选）",
        文件2="第二个文件附件（可选）",
        文件3="第三个文件附件（可选）"
    )
    async def send_feedback(self, interaction: discord.Interaction, 
                           内容: str,
                           图片1: discord.Attachment = None,
                           图片2: discord.Attachment = None,
                           图片3: discord.Attachment = None,
                           图片4: discord.Attachment = None,
                           图片5: discord.Attachment = None,
                           文件1: discord.Attachment = None,
                           文件2: discord.Attachment = None,
                           文件3: discord.Attachment = None):
        """发送匿名反馈（支持多图片和多文件）"""
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
        
        # 收集所有附件
        attachments = []
        images = [图片1, 图片2, 图片3, 图片4, 图片5]
        files = [文件1, 文件2, 文件3]
        
        for img in images:
            if img:
                attachments.append(('image', img))
        
        for file in files:
            if file:
                attachments.append(('file', file))
        
        # 验证附件
        validated_attachments = []
        for att_type, attachment in attachments:
            if att_type == 'image':
                if not attachment.content_type.startswith('image/'):
                    await interaction.followup.send(f"❌ {attachment.filename} 不是有效的图片文件！", ephemeral=True)
                    return
            else:  # file
                filename = attachment.filename.lower()
                file_ext = pathlib.Path(filename).suffix.lower()
                all_extensions = self.file_extensions | self.image_extensions
                
                if file_ext not in all_extensions:
                    await interaction.followup.send(f"❌ 不支持的文件格式：{file_ext}\n支持格式：{', '.join(sorted(all_extensions))}", ephemeral=True)
                    return
            
            # 检查文件大小
            if attachment.size > self.max_file_size:
                await interaction.followup.send(f"❌ {attachment.filename} 大小超过限制（{attachment.size / 1024 / 1024:.1f}MB > 25MB）", ephemeral=True)
                return
            
            validated_attachments.append((att_type, attachment))
        
        # 生成反馈编号
        with sqlite3.connect(self.db_path) as conn:
            # 获取下一个反馈编号
            result = conn.execute('SELECT next_feedback_id FROM guild_sequences WHERE guild_id = ?', (guild_id,)).fetchone()
            guild_feedback_id = result[0] if result else 1
            
            # 更新序列号
            conn.execute('INSERT OR REPLACE INTO guild_sequences (guild_id, next_feedback_id) VALUES (?, ?)',
                        (guild_id, guild_feedback_id + 1))
        
            # 构建内容描述
            content_parts = [内容]
            file_urls = []
            
            for att_type, attachment in validated_attachments:
                if att_type == 'image':
                    content_parts.append(f"图片: {attachment.filename}")
                    file_urls.append(attachment.url)
                else:
                    content_parts.append(f"文件: {attachment.filename}")
                    file_urls.append(attachment.url)
            
            full_content = " | ".join(content_parts)
        
            # 添加反馈记录
            content_type = "mixed" if validated_attachments else "text"
            file_url_json = json.dumps(file_urls) if file_urls else None
            
            cursor = conn.execute('''
                INSERT INTO feedback (guild_feedback_id, user_cookie, guild_id, target_url, target_thread_id, content_type, content, file_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (guild_feedback_id, cookie, guild_id, 帖子链接, thread_id, content_type, full_content, file_url_json))
            
            feedback_id = cursor.lastrowid
            
        # 发送反馈
        try:
            sent_message = await self._send_enhanced_feedback(thread, 内容, validated_attachments, guild_feedback_id)
            
            # 更新消息ID
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('UPDATE feedback SET message_id = ? WHERE id = ?', (sent_message.id, feedback_id))
            
            if self.logger:
                self.logger.info(f"匿名反馈系统 - 匿名反馈发送成功: guild_id={guild_id}, feedback_id={guild_feedback_id}, user={interaction.user.id}, attachments={len(validated_attachments)}")
            
            attachment_desc = ""
            if validated_attachments:
                image_count = sum(1 for att_type, _ in validated_attachments if att_type == 'image')
                file_count = sum(1 for att_type, _ in validated_attachments if att_type == 'file')
                parts = []
                if image_count > 0:
                    parts.append(f"{image_count}张图片")
                if file_count > 0:
                    parts.append(f"{file_count}个文件")
                attachment_desc = f"（包含{' + '.join(parts)}）"
            
            await interaction.followup.send(f"✅ 匿名反馈已发送！反馈编号: {guild_feedback_id:06d}{attachment_desc}", ephemeral=True)
        except Exception as e:
            if self.logger:
                self.logger.error(f"匿名反馈系统 - 发送反馈失败: {e}")
            await interaction.followup.send(f"❌ 发送失败: {str(e)}", ephemeral=True)
    
    async def _send_enhanced_feedback(self, thread: discord.Thread, content: str, attachments: list, guild_feedback_id: int):
        """发送增强反馈消息（支持多图片直接显示和多文件）"""
        # 格式化反馈编号为6位数
        formatted_id = f"{guild_feedback_id:06d}"
        
        # 获取当前时间
        now = datetime.now(timezone.utc)
        
        # 分离图片和文件
        image_attachments = [att for att_type, att in attachments if att_type == 'image']
        file_attachments = [att for att_type, att in attachments if att_type == 'file']
        
        # 创建主embed
        main_embed = discord.Embed(
            title="📫 匿名反馈",
            description=content,
            color=discord.Color.blue(),
            timestamp=now
        )
        
        # 设置footer
        footer_text = f"反馈编号: {formatted_id} | 👎 达到6个自动删除"
        main_embed.set_footer(text=footer_text)
        
        # 准备要发送的文件列表（让图片直接显示）
        discord_files = []
        
        try:
            # 下载图片并创建Discord文件对象
            async with aiohttp.ClientSession() as session:
                for i, img_att in enumerate(image_attachments):
                    try:
                        async with session.get(img_att.url) as resp:
                            if resp.status == 200:
                                img_data = await resp.read()
                                # 创建文件对象，保持原文件名
                                discord_file = discord.File(
                                    io.BytesIO(img_data), 
                                    filename=f"image_{i+1}_{img_att.filename}"
                                )
                                discord_files.append(discord_file)
                    except Exception as e:
                        if self.logger:
                            self.logger.warning(f"匿名反馈系统 - 下载图片失败: {e}")
                        # 如果下载失败，回退到链接方式
                        if i == 0:
                            main_embed.set_image(url=img_att.url)
                        else:
                            main_embed.add_field(
                                name=f"🖼️ 图片{i+1}", 
                                value=f"[{img_att.filename}]({img_att.url})", 
                                inline=True
                            )
                
                # 处理非图片文件（提供下载链接）
                if file_attachments:
                    file_links = []
                    for att in file_attachments:
                        filename = att.filename
                        file_ext = pathlib.Path(filename).suffix.lower()
                        
                        # 对于图片格式的文件，也尝试直接显示
                        if file_ext in self.image_extensions:
                            try:
                                async with session.get(att.url) as resp:
                                    if resp.status == 200:
                                        img_data = await resp.read()
                                        discord_file = discord.File(
                                            io.BytesIO(img_data), 
                                            filename=f"file_{filename}"
                                        )
                                        discord_files.append(discord_file)
                                        continue
                            except:
                                pass
                        
                        # 普通文件显示为下载链接
                        file_links.append(f"📎 [{filename}]({att.url})")
                    
                    if file_links:
                        main_embed.add_field(name="📁 附件文件", value="\n".join(file_links), inline=False)
        
        except Exception as e:
            if self.logger:
                self.logger.error(f"匿名反馈系统 - 处理附件失败: {e}")
            # 回退到原始链接方式
            if image_attachments:
                main_embed.set_image(url=image_attachments[0].url)
                if len(image_attachments) > 1:
                    additional_images = []
                    for i, att in enumerate(image_attachments[1:], 2):
                        additional_images.append(f"[图片{i}]({att.url})")
                    main_embed.add_field(name="📷 更多图片", value=" | ".join(additional_images), inline=False)
            
            if file_attachments:
                file_links = []
                for att in file_attachments:
                    file_links.append(f"📎 [{att.filename}]({att.url})")
                main_embed.add_field(name="📁 附件文件", value="\n".join(file_links), inline=False)
        
        # 发送消息
        if discord_files:
            # 如果有文件，一起发送
            return await thread.send(embed=main_embed, files=discord_files)
        else:
            # 只有embed
            return await thread.send(embed=main_embed)

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
                       u.user_id, f.is_deleted, u.user_cookie
                FROM feedback f
                JOIN users u ON f.user_cookie = u.user_cookie
                WHERE f.guild_id = ? AND f.guild_feedback_id = ?
            ''', (guild_id, 反馈编号)).fetchone()
        
        if not feedback_data:
            await interaction.followup.send(f"❌ 未找到反馈编号 {反馈编号:06d}", ephemeral=True)
            return
        
        (feedback_id, guild_feedback_id, target_url, target_thread_id, 
         content_type, content, file_url, message_id, created_at, user_id, is_deleted, user_cookie) = feedback_data
        
        # 记录管理员溯源操作
        self._record_trace_operation(feedback_id, guild_feedback_id, user_cookie, user_id, 
                                   interaction.user.id, "admin", guild_id)
        
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
            result_message = f"✅ 已删除反馈 #{反馈编号:06d} 并封禁该匿名用户（在帖主 <@{author_id}> 下累计{warning_count}次警告）"
        else:
            # 仅警告
            await self._send_user_notification(
                user_id,
                f"⚠️ 您的匿名反馈#{反馈编号:06d}因 {原因} 被管理员删除\n"
                f"这是您在该帖主帖子下的第{warning_count}次警告\n\n"
                f"请注意改善反馈质量，在该帖主帖子下累计3次警告将被封禁。"
            )
            result_message = f"✅ 已删除反馈 #{反馈编号:06d} 并警告该匿名用户（{warning_count}/3次，帖主: <@{author_id}>）"
        
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

    def _record_trace_operation(self, feedback_id: int, guild_feedback_id: int, traced_user_cookie: str, 
                               traced_user_id: int, tracer_id: int, tracer_type: str, guild_id: int):
        """记录溯源操作"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT INTO trace_records (feedback_id, guild_feedback_id, traced_user_cookie, traced_user_id, 
                                         tracer_id, tracer_type, guild_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (feedback_id, guild_feedback_id, traced_user_cookie, traced_user_id, tracer_id, tracer_type, guild_id))
            
            if self.logger:
                self.logger.info(f"匿名反馈系统 - 记录溯源操作: feedback_id={guild_feedback_id}, traced_user={traced_user_id}, tracer={tracer_id}, type={tracer_type}")

    # 新增：用户查询溯源记录功能
    @feedback.command(name="查询溯源记录", description="查看管理员是否溯源了您的匿名反馈")
    @app_commands.describe(
        时间范围="查询时间范围",
        反馈编号="特定反馈编号（可选）"
    )
    @app_commands.choices(时间范围=[
        app_commands.Choice(name="最近7天", value="7"),
        app_commands.Choice(name="最近30天", value="30"),
        app_commands.Choice(name="最近90天", value="90"),
        app_commands.Choice(name="全部记录", value="all")
    ])
    async def query_trace_records(self, interaction: discord.Interaction, 
                                 时间范围: app_commands.Choice[str], 
                                 反馈编号: int = None):
        """用户查询自己反馈的溯源记录（仅管理员溯源）"""
        await interaction.response.defer(ephemeral=True)
        
        guild_id = interaction.guild.id
        user_id = interaction.user.id
        
        # 防滥用：检查查询频率（每用户每小时最多查询3次）
        cache_key = f"trace_query_{user_id}_{guild_id}"
        current_time = datetime.now(timezone.utc)
        
        if not hasattr(self, '_query_cache'):
            self._query_cache = {}
        
        if cache_key in self._query_cache:
            last_queries = self._query_cache[cache_key]
            # 清理1小时前的记录
            recent_queries = [t for t in last_queries if (current_time - t).total_seconds() < 3600]
            
            if len(recent_queries) >= 3:
                next_available = min(recent_queries) + timedelta(hours=1)
                await interaction.followup.send(
                    f"❌ 查询过于频繁，请在 <t:{int(next_available.timestamp())}:R> 后再试\n"
                    f"💡 为防止滥用，每小时最多查询3次", 
                    ephemeral=True
                )
            return
        
            self._query_cache[cache_key] = recent_queries + [current_time]
        else:
            self._query_cache[cache_key] = [current_time]
        
        # 构建查询条件（仅查询管理员溯源）
        time_condition = ""
        params = [user_id, guild_id]
        
        if 时间范围.value != "all":
            days = int(时间范围.value)
            cutoff_time = current_time - timedelta(days=days)
            time_condition = "AND tr.created_at > ?"
            params.append(cutoff_time.isoformat())
        
        feedback_condition = ""
        if 反馈编号:
            feedback_condition = "AND tr.guild_feedback_id = ?"
            params.append(反馈编号)
        
        # 查询溯源记录（仅管理员）
        with sqlite3.connect(self.db_path) as conn:
            trace_records = conn.execute(f'''
                SELECT tr.guild_feedback_id, tr.tracer_id, tr.tracer_type, tr.created_at,
                       f.target_thread_id, f.content_type, f.is_deleted
                FROM trace_records tr
                JOIN feedback f ON tr.feedback_id = f.id
                WHERE tr.traced_user_id = ? AND tr.guild_id = ? AND tr.tracer_type = 'admin' {time_condition} {feedback_condition}
                ORDER BY tr.created_at DESC
                LIMIT 20
            ''', params).fetchall()
        
        if not trace_records:
            time_desc = f"最近{时间范围.value}天" if 时间范围.value != "all" else "全部时间"
            feedback_desc = f"反馈#{反馈编号:06d}" if 反馈编号 else "您的反馈"
            await interaction.followup.send(f"📭 {time_desc}内没有管理员溯源过{feedback_desc}", ephemeral=True)
            return
        
        # 构建响应
        embed = discord.Embed(
            title="🔍 管理员溯源记录查询",
            description=f"以下是管理员溯源您匿名反馈的记录",
            color=discord.Color.orange()
        )
        
        # 统计信息
        admin_count = len(trace_records)
        
        embed.add_field(name="📊 统计", 
                       value=f"管理员溯源: {admin_count}次", 
                       inline=True)
        
        time_desc = f"最近{时间范围.value}天" if 时间范围.value != "all" else "全部记录"
        embed.add_field(name="⏰ 时间范围", value=time_desc, inline=True)
        embed.add_field(name="📝 记录数量", value=f"{len(trace_records)}/20", inline=True)
        
        # 详细记录
        records_text = ""
        for record in trace_records[:10]:  # 最多显示10条
            guild_feedback_id, tracer_id, tracer_type, created_at, thread_id, content_type, is_deleted = record
            
            # 使用Discord时间戳格式
            try:
                if created_at.endswith('Z'):
                    trace_time = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                elif 'T' in created_at and ('+' in created_at or created_at.endswith('Z')):
                    trace_time = datetime.fromisoformat(created_at)
                else:
                    trace_time = datetime.fromisoformat(created_at).replace(tzinfo=timezone.utc)
                
                time_str = f"<t:{int(trace_time.timestamp())}:R>"
            except:
                time_str = "时间解析失败"
            
            # 反馈状态
            status_emoji = "🗑️" if is_deleted else "✅"
            
            records_text += f"👑 **#{guild_feedback_id:06d}** - 管理员 <@{tracer_id}> {time_str} {status_emoji}\n"
        
        if records_text:
            embed.add_field(name="📋 详细记录", value=records_text, inline=False)
        
        if len(trace_records) > 10:
            embed.add_field(name="💡 提示", value=f"还有 {len(trace_records) - 10} 条记录未显示", inline=False)
        
        embed.set_footer(text=f"查询者: {interaction.user} | 🔒 此信息仅您可见")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        if self.logger:
            self.logger.info(f"匿名反馈系统 - 用户查询管理员溯源记录: user={user_id}, records={len(trace_records)}, range={时间范围.value}")

    # 新增：用户删除自己的匿名反馈
    @feedback.command(name="删除反馈", description="删除自己发送的匿名反馈")
    @app_commands.describe(反馈编号="要删除的反馈编号（6位数字）")
    async def delete_own_feedback(self, interaction: discord.Interaction, 反馈编号: int):
        """用户删除自己的匿名反馈"""
        await interaction.response.defer(ephemeral=True)
        
        guild_id = interaction.guild.id
        user_id = interaction.user.id
        cookie = self._get_user_cookie(user_id, guild_id)
        
        # 查询反馈信息
        with sqlite3.connect(self.db_path) as conn:
            feedback_data = conn.execute('''
                SELECT f.id, f.message_id, f.target_thread_id, f.is_deleted, f.created_at
                FROM feedback f
                WHERE f.guild_id = ? AND f.guild_feedback_id = ? AND f.user_cookie = ?
            ''', (guild_id, 反馈编号, cookie)).fetchone()
        
        if not feedback_data:
            await interaction.followup.send(f"❌ 未找到您发送的反馈编号 {反馈编号:06d}", ephemeral=True)
            return
        
        feedback_id, message_id, target_thread_id, is_deleted, created_at = feedback_data
        
        if is_deleted:
            await interaction.followup.send(f"❌ 反馈 #{反馈编号:06d} 已被删除", ephemeral=True)
            return
        
        # 简化时间处理 - 直接使用数据库时间戳进行计算
        try:
            # 将数据库时间转换为时间戳，用于Discord显示
            if created_at.endswith('Z'):
                feedback_time = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            elif 'T' in created_at and ('+' in created_at or created_at.endswith('Z')):
                feedback_time = datetime.fromisoformat(created_at)
            else:
                # 假设为UTC时间
                feedback_time = datetime.fromisoformat(created_at).replace(tzinfo=timezone.utc)
            
            # 检查是否超过24小时
            current_time = datetime.now(timezone.utc)
            time_diff = (current_time - feedback_time).total_seconds()
            
            if time_diff > 24 * 3600:  # 24小时
                feedback_timestamp = int(feedback_time.timestamp())
                await interaction.followup.send(
                    f"❌ 反馈 #{反馈编号:06d} 发送已超过24小时，无法删除\n"
                    f"💡 反馈发送时间：<t:{feedback_timestamp}:F>", 
                    ephemeral=True
                )
            return
        
        except Exception as e:
            if self.logger:
                self.logger.error(f"匿名反馈系统 - 时间处理失败: {e}, created_at={created_at}")
            await interaction.followup.send("❌ 时间处理失败，无法删除", ephemeral=True)
            return
        
        # 标记为已删除
        self._mark_feedback_deleted(feedback_id)
        
        # 删除Discord消息
        if message_id:
            try:
                thread = await self._get_thread_by_id(guild_id, target_thread_id)
                if thread:
                    message = await thread.fetch_message(message_id)
                    await message.delete()
                    if self.logger:
                        self.logger.info(f"匿名反馈系统 - 用户删除自己的反馈消息: message_id={message_id}, feedback_id={反馈编号}, user={user_id}")
            except discord.NotFound:
                pass  # 消息已被删除
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"匿名反馈系统 - 用户删除反馈消息失败: {e}")
        
        await interaction.followup.send(f"✅ 已删除您的匿名反馈 #{反馈编号:06d}", ephemeral=True)
        
        if self.logger:
            self.logger.info(f"匿名反馈系统 - 用户删除自己反馈: user={user_id}, feedback_id={反馈编号}")

    # 修改：帖主功能 - 移除溯源功能，添加全局管理功能
    @author_feedback.command(name="全局封禁用户", description="全局封禁用户在您的所有帖子下使用匿名反馈（仅帖主可用）")
    @app_commands.describe(用户="要封禁的用户", 原因="封禁原因")
    async def author_global_ban_user(self, interaction: discord.Interaction, 用户: discord.Member, 原因: str = "违规行为"):
        """帖主全局封禁用户"""
        await interaction.response.defer(ephemeral=True)
        
        # 检查是否在帖子中使用
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.followup.send("❌ 此命令只能在您的帖子中使用", ephemeral=True)
            return
        
        thread = interaction.channel
        if thread.owner_id != interaction.user.id:
            await interaction.followup.send(f"❌ 您不是该帖帖主\n帖主: <@{thread.owner_id}>", ephemeral=True)
            return
        
        guild_id = interaction.guild.id
        
        # 检查是否已经封禁
        if self._is_globally_banned_by_author(用户.id, interaction.user.id, guild_id):
            await interaction.followup.send(f"❌ 用户 {用户.mention} 已被您全局封禁", ephemeral=True)
            return
        
        # 添加全局封禁记录
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO author_global_bans (author_id, banned_user_id, guild_id, reason)
                VALUES (?, ?, ?, ?)
            ''', (interaction.user.id, 用户.id, guild_id, 原因))
        
        # 通知被封禁用户
            await self._send_user_notification(
            用户.id,
            f"🚫 您已被帖主 <@{interaction.user.id}> 全局封禁，无法在其任何帖子下使用匿名反馈功能。\n"
                f"原因：{原因}\n"
                f"如有异议请联系管理员。"
            )
        
        await interaction.followup.send(f"✅ 已全局封禁用户 {用户.mention}，其无法在您的任何帖子下使用匿名反馈", ephemeral=True)
        
        if self.logger:
            self.logger.info(f"匿名反馈系统 - 帖主全局封禁用户: author={interaction.user.id}, target={用户.id}, reason={原因}")
    
    @author_feedback.command(name="解除全局封禁", description="解除用户的全局封禁（仅帖主可用）")
    @app_commands.describe(用户="要解封的用户")
    async def author_global_unban_user(self, interaction: discord.Interaction, 用户: discord.Member):
        """帖主解除全局封禁"""
        await interaction.response.defer(ephemeral=True)
        
        # 检查是否在帖子中使用
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.followup.send("❌ 此命令只能在您的帖子中使用", ephemeral=True)
            return
        
        thread = interaction.channel
        if thread.owner_id != interaction.user.id:
            await interaction.followup.send(f"❌ 您不是该帖帖主\n帖主: <@{thread.owner_id}>", ephemeral=True)
            return
        
        guild_id = interaction.guild.id
        
        # 检查是否已经封禁
        if not self._is_globally_banned_by_author(用户.id, interaction.user.id, guild_id):
            await interaction.followup.send(f"❌ 用户 {用户.mention} 未被您全局封禁", ephemeral=True)
            return
        
        # 移除全局封禁记录
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                DELETE FROM author_global_bans 
                WHERE author_id = ? AND banned_user_id = ? AND guild_id = ?
            ''', (interaction.user.id, 用户.id, guild_id))
        
        # 通知被解封用户
            await self._send_user_notification(
            用户.id,
            f"✅ 帖主 <@{interaction.user.id}> 已解除对您的全局封禁，您现在可以在其帖子下使用匿名反馈功能了。"
        )
        
        await interaction.followup.send(f"✅ 已解除用户 {用户.mention} 的全局封禁", ephemeral=True)
        
        if self.logger:
            self.logger.info(f"匿名反馈系统 - 帖主解除全局封禁: author={interaction.user.id}, target={用户.id}")
    
    @author_feedback.command(name="禁用匿名功能", description="禁用您所有帖子的匿名反馈功能（仅帖主可用）")
    @app_commands.describe(原因="禁用原因")
    async def author_disable_anonymous(self, interaction: discord.Interaction, 原因: str = "不接受匿名反馈"):
        """帖主禁用匿名功能"""
        await interaction.response.defer(ephemeral=True)
        
        # 检查是否在帖子中使用
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.followup.send("❌ 此命令只能在您的帖子中使用", ephemeral=True)
            return
        
        thread = interaction.channel
        if thread.owner_id != interaction.user.id:
            await interaction.followup.send(f"❌ 您不是该帖帖主\n帖主: <@{thread.owner_id}>", ephemeral=True)
            return
        
        guild_id = interaction.guild.id
        
        # 检查是否已经禁用
        if self._is_anonymous_disabled_by_author(interaction.user.id, guild_id):
            await interaction.followup.send("❌ 您已经禁用了匿名反馈功能", ephemeral=True)
            return
        
        # 添加禁用记录
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO author_anonymous_disabled (author_id, guild_id, reason)
                VALUES (?, ?, ?)
            ''', (interaction.user.id, guild_id, 原因))
        
        await interaction.followup.send(f"✅ 已禁用您所有帖子的匿名反馈功能\n原因：{原因}", ephemeral=True)
        
        if self.logger:
            self.logger.info(f"匿名反馈系统 - 帖主禁用匿名功能: author={interaction.user.id}, reason={原因}")
    
    @author_feedback.command(name="启用匿名功能", description="重新启用您所有帖子的匿名反馈功能（仅帖主可用）")
    async def author_enable_anonymous(self, interaction: discord.Interaction):
        """帖主启用匿名功能"""
        await interaction.response.defer(ephemeral=True)
        
        # 检查是否在帖子中使用
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.followup.send("❌ 此命令只能在您的帖子中使用", ephemeral=True)
            return
        
        thread = interaction.channel
        if thread.owner_id != interaction.user.id:
            await interaction.followup.send(f"❌ 您不是该帖帖主\n帖主: <@{thread.owner_id}>", ephemeral=True)
            return
        
        guild_id = interaction.guild.id
        
        # 检查是否已经启用
        if not self._is_anonymous_disabled_by_author(interaction.user.id, guild_id):
            await interaction.followup.send("❌ 您的匿名反馈功能本来就是启用状态", ephemeral=True)
            return
        
        # 移除禁用记录
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                DELETE FROM author_anonymous_disabled 
                WHERE author_id = ? AND guild_id = ?
            ''', (interaction.user.id, guild_id))
        
        await interaction.followup.send("✅ 已重新启用您所有帖子的匿名反馈功能", ephemeral=True)
        
        if self.logger:
            self.logger.info(f"匿名反馈系统 - 帖主启用匿名功能: author={interaction.user.id}")

    def _is_globally_banned_by_author(self, user_id: int, author_id: int, guild_id: int) -> bool:
        """检查用户是否被帖主全局封禁"""
        with sqlite3.connect(self.db_path) as conn:
            result = conn.execute(
                'SELECT 1 FROM author_global_bans WHERE author_id = ? AND banned_user_id = ? AND guild_id = ?',
                (author_id, user_id, guild_id)
            ).fetchone()
            return result is not None
    
    def _is_anonymous_disabled_by_author(self, author_id: int, guild_id: int) -> bool:
        """检查帖主是否禁用了匿名功能"""
        with sqlite3.connect(self.db_path) as conn:
            result = conn.execute(
                'SELECT 1 FROM author_anonymous_disabled WHERE author_id = ? AND guild_id = ?',
                (author_id, guild_id)
            ).fetchone()
            return result is not None

    @author_feedback.command(name="删除反馈并警告", description="删除匿名反馈并警告用户（仅帖主可用）")
    @app_commands.describe(反馈编号="反馈编号（6位数字）", 原因="删除原因")
    async def author_delete_and_warn_user(self, interaction: discord.Interaction, 反馈编号: int, 原因: str = "不当反馈"):
        """帖主删除反馈并警告用户"""
        await interaction.response.defer(ephemeral=True)
        
        guild_id = interaction.guild.id
        
        # 查询反馈信息
        with sqlite3.connect(self.db_path) as conn:
            feedback_data = conn.execute('''
                SELECT f.id, f.target_thread_id, f.message_id, u.user_id, u.user_cookie, f.is_deleted
                FROM feedback f
                JOIN users u ON f.user_cookie = u.user_cookie
                WHERE f.guild_id = ? AND f.guild_feedback_id = ?
            ''', (guild_id, 反馈编号)).fetchone()
        
        if not feedback_data:
            await interaction.followup.send(f"❌ 未找到反馈编号 {反馈编号:06d}", ephemeral=True)
            return
        
        feedback_id, target_thread_id, message_id, user_id, user_cookie, is_deleted = feedback_data
        
        # 检查反馈是否已被删除
        if is_deleted:
            await interaction.followup.send(f"❌ 反馈 #{反馈编号:06d} 已被删除", ephemeral=True)
            return
        
        # 验证是否为帖主
        is_valid, error_msg, thread = await self._validate_thread_author(interaction, {
            'target_thread_id': target_thread_id,
            'guild_id': guild_id
        })
        
        if not is_valid:
            await interaction.followup.send(error_msg, ephemeral=True)
            return
        
        # 标记反馈为已删除
        self._mark_feedback_deleted(feedback_id)
        
        # 删除Discord消息
        if message_id:
            try:
                if thread:
                    message = await thread.fetch_message(message_id)
                    await message.delete()
                    if self.logger:
                        self.logger.info(f"匿名反馈系统 - 帖主删除反馈消息: message_id={message_id}, feedback_id={反馈编号}")
            except discord.NotFound:
                pass  # 消息已被删除
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"匿名反馈系统 - 帖主删除反馈消息失败: {e}")
        
        # 增加警告次数（针对该帖主）
        warning_count = self._add_author_warning(user_cookie, interaction.user.id, "author_delete", feedback_id, interaction.user.id, 原因)
        
        # 通知被警告用户
        if warning_count >= 3:
            await self._send_user_notification(
                user_id,
                f"🚫 您的匿名反馈#{反馈编号:06d}被帖主删除。\n"
                f"原因：{原因}\n"
                f"这是您在该帖主帖子下的第{warning_count}次警告\n\n"
                f"由于累计警告已达到3次，您已被该帖主封禁，无法在其所有帖子下发送匿名反馈。如有异议请联系管理员。"
            )
            result_msg = f"✅ 已删除反馈 #{反馈编号:06d} 并封禁该匿名用户（累计{warning_count}次警告）"
        else:
            await self._send_user_notification(
                user_id,
                f"⚠️ 您的匿名反馈#{反馈编号:06d}被帖主删除。\n"
                f"原因：{原因}\n"
                f"这是您在该帖主帖子下的第{warning_count}次警告\n\n"
                f"请注意改善反馈质量，在该帖主帖子下累计3次警告将被封禁。"
            )
            result_msg = f"✅ 已删除反馈 #{反馈编号:06d} 并警告该匿名用户（{warning_count}/3次）"
        
        await interaction.followup.send(result_msg, ephemeral=True)
        
        if self.logger:
            self.logger.info(f"匿名反馈系统 - 帖主删除反馈并警告: author={interaction.user.id}, feedback_id={反馈编号}, target={user_id}, warnings={warning_count}, reason={原因}")


async def setup(bot):
    await bot.add_cog(AnonymousFeedbackCog(bot)) 