import asyncio
from discord.ext import commands
from discord import app_commands
import discord
import json
import datetime
import pathlib
from typing import List, Tuple

class EventCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = getattr(bot, 'logger', None)
        self.name = "赛事命令"
        # 初始化配置缓存
        self._config_cache = {}
        self._config_cache_mtime = None
        # 初始化views属性
        self.views = []

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

    def _load_views(self):
        """加载视图"""
        # 从data/event/views目录中加载所有视图
        views_dir = pathlib.Path("data/event/views.json")
        if views_dir.exists():
            with open(views_dir, "r", encoding="utf-8") as f:
                data = json.load(f)
                # 如果data是字典且包含"views"键，则使用"views"列表
                if isinstance(data, dict) and "views" in data:
                    self.views = data["views"]
                    for view in self.views:
                        self.bot.add_view(RoleButtonView(view["role_id"]))
                # 如果data直接是列表，则直接使用
                elif isinstance(data, list):
                    self.views = data
                    for view in self.views:
                        self.bot.add_view(RoleButtonView(view["role_id"]))
                else:
                    # 如果格式不正确，初始化为空列表
                    self.views = []
        else:
            # 如果文件不存在，初始化为空列表
            self.views = []
            # 创建目录（如果不存在）
            views_dir.parent.mkdir(parents=True, exist_ok=True)

    def _add_view(self, view):
        """添加视图"""
        self.bot.add_view(view)
        view_dict = {
            "role_id": view.role_id
        }
        # 确保self.views是列表，如果不存在则初始化
        if not hasattr(self, 'views') or not isinstance(self.views, list):
            self.views = []
        
        self.views.append(view_dict)
        views_dir = pathlib.Path("data/event/views.json")
        # 创建目录（如果不存在）
        views_dir.parent.mkdir(parents=True, exist_ok=True)
        
        # 保存为包含"views"键的字典结构，以便与可能的其他数据兼容
        data_to_save = {
            "views": self.views
        }
        
        with open(views_dir, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)

    @commands.Cog.listener()
    async def on_ready(self):
        self._load_views()
        if self.logger:
            self.logger.info("赛事模块已加载")

    def is_event_manager():
        async def predicate(ctx):
            try:
                cog = ctx.cog
                guild_id = ctx.guild.id if ctx.guild else None
                if not guild_id:
                    return False
                
                # 直接从主配置获取赛事管理员列表
                config = cog.config
                event_managers = config.get("event_managers", [])
                
                # 检查用户ID是否在管理员列表中
                if ctx.author.id in event_managers:
                    return True
                
                # 检查用户是否拥有管理员身份组
                for role_id in event_managers:
                    try:
                        role = ctx.guild.get_role(int(role_id))
                        if role and role in ctx.author.roles:
                            return True
                    except (ValueError, TypeError):
                        # 如果无法转换为int，说明是用户ID而非身份组ID
                        continue
                
                return False
            except Exception:
                return False
        return commands.check(predicate)

    event = app_commands.Group(name="赛事", description="赛事相关命令")

    @event.command(name="自助身份组发放", description="在当前频道创建一个自助获取/放弃身份组的embed和持久化按钮")
    @is_event_manager()
    @app_commands.describe(role="身份组", title="Embed标题", content="Embed内容", thumbnail="Embed缩略图（可选）")
    @app_commands.rename(title="标题", content="内容", thumbnail="缩略图")
    async def self_role(self, interaction: discord.Interaction,
                        role: discord.Role, title: str,
                        content: str, thumbnail: discord.Attachment = None):
        """创建自助身份组获取/放弃Embed和按钮"""
        guild = interaction.guild
        
        # 检查最高可管理权限
        config = self.config
        highest_id = config.get("highest_role_available")
        highest_role = guild.get_role(int(highest_id)) if highest_id else None
        if highest_role and role.position > highest_role.position:
            await interaction.response.send_message(
                "❌ 无法操作比最高可管理身份组更高的身份组", ephemeral=True)
            return
        # 构造Embed
        embed = discord.Embed(
            title=title, description=content,
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_footer(text="点击按钮以获取或放弃身份组")
        if thumbnail:
            embed.set_thumbnail(url=thumbnail.url)
        # 创建按钮视图
        view = RoleButtonView(role.id)
        self._add_view(view)
        # 发送消息并注册持久化视图
        await interaction.response.send_message(embed=embed, view=view)

    @event.command(name="检查发帖", description="检查指定身份组成员是否在指定频道发过贴")
    @is_event_manager()
    @app_commands.describe(role="身份组", channel="要检查的频道", channel2="要检查的频道2（可选）", channel3="要检查的频道3（可选）")
    @app_commands.rename(channel="频道", channel2="频道2", channel3="频道3")
    async def check_post(self, interaction: discord.Interaction,
                         role: discord.Role, channel: discord.ForumChannel, channel2: discord.ForumChannel = None, channel3: discord.ForumChannel = None):
        """检查指定身份组成员是否在指定频道发过贴"""
        guild = interaction.guild

        members = role.members
        if not members:
            await interaction.response.send_message("❌ 该身份组没有成员", ephemeral=True)
            return
        # 获取发帖成员
        threads = channel.threads
        before = None
        while True:
            archived_threads = [
                m async for m in channel.archived_threads(limit=100, before=before)
            ]
            if len(archived_threads) == 0:
                break
            before = archived_threads[-1].archive_timestamp
            threads.extend(archived_threads)
        if channel2:
            threads2 = channel2.threads
            before = None
            while True:
                archived_threads = [
                    m async for m in channel2.archived_threads(limit=100, before=before)
                ]
                if len(archived_threads) == 0:
                    break
                before = archived_threads[-1].archive_timestamp
                threads2.extend(archived_threads)
            threads.extend(threads2)
        if channel3:
            threads3 = channel3.threads
            before = None
            while True:
                archived_threads = [
                    m async for m in channel3.archived_threads(limit=100, before=before)
                ]
                if len(archived_threads) == 0:
                    break
                before = archived_threads[-1].archive_timestamp
                threads3.extend(archived_threads)
            threads.extend(threads3)
            
        posted = set()
        for thread in threads:
            if thread.owner_id in [m.id for m in members]:
                posted.add(thread.owner_id)
        missing = [m for m in members if m.id not in posted]
        # 分页展示未发帖成员
        title = "检查发帖结果"
        pages = []
        # 切分成员列表，保证每页不超过1024字符
        if missing:
            mentions = [m.mention for m in missing]
            chunks = []
            chunk = []
            length = 0
            for mention in mentions:
                part = mention if not chunk else "\n" + mention
                if length + len(part) > 1024:
                    chunks.append(chunk)
                    chunk = [mention]
                    length = len(mention)
                else:
                    chunk.append(mention)
                    length += len(part)
            if chunk:
                chunks.append(chunk)
        else:
            chunks = [["无"]]
        # 构建分页 Embed
        for idx, chunk in enumerate(chunks):
            embed_page = discord.Embed(title=title, color=discord.Color.orange())
            embed_page.add_field(name="频道", value=channel.mention, inline=False)
            embed_page.add_field(name="未发帖成员", value="\n".join(chunk), inline=False)
            embed_page.set_footer(text=f"第{idx+1}/{len(chunks)}页")
            pages.append(embed_page)
        # 发送带分页按钮的消息或单页消息
        if len(pages) == 1:
            await interaction.response.send_message(embed=pages[0], ephemeral=True)
        else:
            view = PaginationView(pages)
            await interaction.response.send_message(embed=pages[0], view=view, ephemeral=True)


class RoleButtonView(discord.ui.View):
    """自助身份组按钮视图"""
    def __init__(self, role_id: int):
        super().__init__(timeout=None)
        self.role_id = role_id
        add_btn = discord.ui.Button(
            label="获取身份组", style=discord.ButtonStyle.success,
            custom_id=f"event:role:add:{role_id}"
        )
        remove_btn = discord.ui.Button(
            label="放弃身份组", style=discord.ButtonStyle.danger,
            custom_id=f"event:role:remove:{role_id}"
        )
        add_btn.callback = self.add_callback
        remove_btn.callback = self.remove_callback
        self.add_item(add_btn)
        self.add_item(remove_btn)

    async def add_callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id)
        if not role:
            return await interaction.response.send_message("❌ 未找到身份组", ephemeral=True)
        try:
            await interaction.user.add_roles(role, reason="自助身份组获取")
            await interaction.response.send_message(
                f"✅ 已获取身份组 {role.mention}", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ 无法添加身份组", ephemeral=True)

    async def remove_callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id)
        if not role:
            return await interaction.response.send_message("❌ 未找到身份组", ephemeral=True)
        try:
            await interaction.user.remove_roles(role, reason="自助身份组放弃")
            await interaction.response.send_message(
                f"✅ 已放弃身份组 {role.mention}", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ 无法移除身份组", ephemeral=True)

# 添加分页视图，用于嵌入消息翻页
class PaginationView(discord.ui.View):
    """Embed 分页视图"""
    def __init__(self, pages):
        super().__init__(timeout=None)
        self.pages = pages
        self.current = 0
        # 上一页按钮
        self.prev_btn = discord.ui.Button(
            label="上一页", style=discord.ButtonStyle.secondary, disabled=True
        )
        # 下一页按钮
        self.next_btn = discord.ui.Button(
            label="下一页", style=discord.ButtonStyle.secondary,
            disabled=(len(pages) <= 1)
        )
        self.prev_btn.callback = self.prev_callback
        self.next_btn.callback = self.next_callback
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)

    async def prev_callback(self, interaction: discord.Interaction):
        self.current = max(0, self.current - 1)
        self.prev_btn.disabled = (self.current == 0)
        self.next_btn.disabled = False
        embed = self.pages[self.current]
        await interaction.response.edit_message(embed=embed, view=self)

    async def next_callback(self, interaction: discord.Interaction):
        self.current = min(len(self.pages) - 1, self.current + 1)
        self.prev_btn.disabled = False
        self.next_btn.disabled = (self.current == len(self.pages) - 1)
        embed = self.pages[self.current]
        await interaction.response.edit_message(embed=embed, view=self)


async def setup(bot):
    await bot.add_cog(EventCommands(bot))
