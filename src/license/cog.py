import asyncio
import json
from pathlib import Path

import discord
from discord import app_commands, ui
from discord.ext import commands

from src.utils.confirm_view import confirm_view_embed


# --- 数据模型与存储 ---

class LicenseConfig:
    """封装用户授权配置的数据类"""

    def __init__(self, user_id: int, data: dict = None):
        if data is None:
            data = {}
        self.user_id = user_id
        # bot_enabled: 用户是否启用本功能
        self.bot_enabled: bool = data.get('bot_enabled', True)
        # auto_post: 是否自动发布协议，否则就询问
        self.auto_post: bool = data.get('auto_post', False)
        # require_confirmation: 发布前是否需要二次确认，默认为 True
        self.require_confirmation: bool = data.get('require_confirmation', True)
        # license_details: 协议具体内容
        self.license_details: dict = data.get('license_details', {
            "reproduce": "询问作者",
            "derive": "询问作者",
            "commercial": "禁止",
            "attribution": f"<@{user_id}>",
            "notes": "无"
        })


class LicenseDB:
    """处理用户授权配置的读写"""

    def __init__(self):
        self.data_path = Path("data/licenses")
        self.data_path.mkdir(parents=True, exist_ok=True)

    def _get_user_file(self, user_id: int) -> Path:
        return self.data_path / f"{user_id}.json"

    def get_config(self, user_id: int) -> LicenseConfig:
        """获取用户的配置，如果不存在则返回默认配置"""
        user_file = self._get_user_file(user_id)
        if not user_file.exists():
            return LicenseConfig(user_id)
        try:
            with user_file.open('r', encoding='utf-8') as f:
                data = json.load(f)
            return LicenseConfig(user_id, data)
        except (json.JSONDecodeError, IOError):
            # 文件损坏或读取错误，返回默认值
            return LicenseConfig(user_id)

    def save_config(self, config: LicenseConfig):
        """保存用户的配置"""
        user_file = self._get_user_file(config.user_id)
        data = {
            "bot_enabled": config.bot_enabled,
            "auto_post": config.auto_post,
            "require_confirmation": config.require_confirmation,
            "license_details": config.license_details
        }
        with user_file.open('w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)


# --- 交互界面 (Modals & Views) ---

class LicenseEditModal(ui.Modal):
    """编辑授权协议的弹窗表单"""

    def __init__(self, db: LicenseDB, current_config: LicenseConfig, title="编辑你的默认授权协议"):
        super().__init__(title=title)
        self.db = db
        self.config = current_config

        self.reproduce = ui.TextInput(label="是否允许转载？", placeholder="例如：允许、禁止、需询问作者", default=self.config.license_details.get("reproduce"),
                                      max_length=50)
        self.derive = ui.TextInput(label="是否允许演绎/衍生创作？", placeholder="例如：允许、禁止、需询问作者", default=self.config.license_details.get("derive"),
                                   max_length=50)
        self.commercial = ui.TextInput(label="是否允许商业性使用？", placeholder="例如：允许、禁止", default=self.config.license_details.get("commercial"),
                                       max_length=50)
        self.attribution = ui.TextInput(label="署名要求", placeholder=f"例如：<@{self.config.user_id}>", default=self.config.license_details.get("attribution"),
                                        max_length=100)
        self.notes = ui.TextInput(label="附加说明/主页链接", placeholder="可在此处填写你的主页链接等", default=self.config.license_details.get("notes"),
                                  required=False, style=discord.TextStyle.paragraph)

        self.add_item(self.reproduce)
        self.add_item(self.derive)
        self.add_item(self.commercial)
        self.add_item(self.attribution)
        self.add_item(self.notes)

    async def on_submit(self, interaction: discord.Interaction):
        # 更新配置对象
        self.config.license_details = {
            "reproduce": self.reproduce.value,
            "derive": self.derive.value,
            "commercial": self.commercial.value,
            "attribution": self.attribution.value,
            "notes": self.notes.value or "无"
        }
        # 保存到文件
        self.db.save_config(self.config)

        embed = discord.Embed(
            title="✅ 默认授权协议已更新",
            description="你的设置已保存。现在你可以选择是否在当前帖子中发布这个协议。",
            color=discord.Color.green()
        )
        view = PostLicenseView(db=self.db, config=self.config, thread=interaction.channel)

        # 发送新的私密消息，这是对Modal提交的响应
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        # --- 新增：在这里清理原始消息的按钮 ---
        # modal的interaction可以用来编辑发起modal的那个组件所在的原始消息
        try:
            # 使用 edit_original_response 来编辑发起这个交互流程的原始消息
            await interaction.edit_original_response(content="✅ 协议编辑完成，请在下方新消息中操作。", view=None)
        except discord.HTTPException:
            # 如果原始消息找不到了或有其他问题，就忽略它
            pass


class InitialActionView(ui.View):
    """在新帖下询问作者操作的视图"""

    def __init__(self, db: LicenseDB, config: LicenseConfig, thread: discord.Thread):
        super().__init__(timeout=3600)  # 1小时后超时
        self.db = db
        self.config = config
        self.thread = thread
        self.owner_id = thread.owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ 这不是你的帖子，不能进行操作哦。", ephemeral=True)
            return False
        return True

    @ui.button(label="发布默认协议", style=discord.ButtonStyle.success, row=1)
    async def post_default(self, interaction: discord.Interaction, button: ui.Button):
        # --- 核心改动 ---
        # 无论是否需要确认，都先让原按钮失效，防止重复点击
        await interaction.response.defer()  # 延迟响应，给后续操作留出时间
        await interaction.edit_original_response(content="处理中...", view=None)

        license_embed = build_license_embed(self.config, interaction.user)

        # 如果用户关闭了二次确认，则直接发布
        if not self.config.require_confirmation:
            await self.thread.send(embed=license_embed)
            await interaction.followup.send("✅ 已在帖子下方发布你的默认授权协议。", ephemeral=True)
            self.stop()
            return

        # --- 二次确认流程 ---
        preview_embed = license_embed.copy()
        preview_embed.title = "🔍 协议预览与确认"
        preview_embed.description = "**你确定要以以下协议发布吗？**\n\n(此为预览，确认后将公开发布)"

        # 使用导入的函数发送私密确认
        confirmed = await confirm_view_embed(
            interaction,
            embed=preview_embed,
            timeout=120
        )

        if confirmed:
            await self.thread.send(embed=license_embed)
            # confirm_view_embed 已经发送了 "✅ 已确认..." 的消息，这里无需再发
        # 如果取消，confirm_view_embed 也会自动处理消息

        self.stop()

    # --- 新增的按钮 ---
    @ui.button(label="预览协议", style=discord.ButtonStyle.primary, row=0)
    async def preview_license(self, interaction: discord.Interaction, button: ui.Button):
        """发送一条临时的私密消息来展示当前的默认协议"""
        # 调用我们已有的辅助函数来构建协议 Embed
        embed = build_license_embed(self.config, interaction.user)
        embed.title = "👀 你的当前默认协议预览"  # 可以给个不同的标题以作区分

        # 将其作为一条仅发起交互者可见的消息发送
        await interaction.response.send_message(embed=embed, ephemeral=True)
        # 这个操作是独立的，不需要停止View，用户可以继续操作其他按钮

    # --- 新增结束 ---

    @ui.button(label="编辑协议", style=discord.ButtonStyle.primary, row=1)
    async def edit_license(self, interaction: discord.Interaction, button: ui.Button):
        # 这个按钮的唯一任务就是响应并弹出Modal
        modal = LicenseEditModal(self.db, self.config)
        await interaction.response.send_modal(modal)

        # 弹出Modal后，这个View的任务就完成了。
        # 我们让它停止监听，防止超时后discord报错。
        self.stop()

    @ui.button(label="机器人设置", style=discord.ButtonStyle.secondary, row=1)
    async def settings(self, interaction: discord.Interaction, button: ui.Button):
        view = SettingsView(db=self.db, config=self.config)
        embed = discord.Embed(
            title="⚙️ 机器人设置",
            description="请选择你希望机器人如何为你工作。",
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @ui.button(label="本次跳过", style=discord.ButtonStyle.secondary, row=1)
    async def skip_for_now(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(content="好的，你随时可以通过 `/license` 命令来设置你的授权协议。", view=None)
        self.stop()

    @ui.button(label="别再打扰我", style=discord.ButtonStyle.danger, row=1)
    async def disable_bot(self, interaction: discord.Interaction, button: ui.Button):
        """禁用机器人功能"""
        config = self.db.get_config(self.owner_id)
        config.bot_enabled = False
        self.db.save_config(config)
        await interaction.response.edit_message(
            content="好的，我以后不会再主动打扰你了。\n你可以随时使用 `/license settings` 命令重新启用我。",
            view=None
        )
        self.stop()

class PostLicenseView(ui.View):
    """用于在编辑后发布协议的简单视图"""

    def __init__(self, db: LicenseDB, config: LicenseConfig, thread: discord.Thread):
        super().__init__(timeout=600)
        self.db = db
        self.config = config
        self.thread = thread

    @ui.button(label="在帖子中发布", style=discord.ButtonStyle.success)
    async def post_now(self, interaction: discord.Interaction, button: ui.Button):
        # --- 核心改动 ---
        await interaction.response.defer()
        await interaction.edit_original_response(content="处理中...", view=None)

        license_embed = build_license_embed(self.config, interaction.user)

        if not self.config.require_confirmation:
            await self.thread.send(embed=license_embed)
            await interaction.followup.send("✅ 已发布！", ephemeral=True)
            self.stop()
            return

        # --- 二次确认流程 ---
        preview_embed = license_embed.copy()
        preview_embed.title = "🔍 协议预览与确认"
        preview_embed.description = "**你确定要以以下协议发布吗？**\n\n(此为预览，确认后将公开发布)"

        confirmed = await confirm_view_embed(
            interaction,
            embed=preview_embed,
            timeout=120
        )

        if confirmed:
            await self.thread.send(embed=license_embed)

        self.stop()

    @ui.button(label="关闭", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(content="好的。", view=None)
        self.stop()


class SettingsView(ui.View):
    """机器人行为设置视图"""

    def __init__(self, db: LicenseDB, config: LicenseConfig):
        super().__init__(timeout=180)
        self.db = db
        self.config = config

        # 根据当前状态更新按钮标签
        self.toggle_auto_post_button.label = "自动发布: " + ("✅" if config.auto_post else "❌")
        self.toggle_bot_enabled_button.label = "启用机器人: " + ("✅" if config.bot_enabled else "❌")
        self.toggle_confirmation_button.label = "发布前二次确认: " + ("✅" if config.require_confirmation else "❌")

    @ui.button(label="切换自动发布", style=discord.ButtonStyle.primary, row=0)
    async def toggle_auto_post_button(self, interaction: discord.Interaction, button: ui.Button):
        self.config.auto_post = not self.config.auto_post
        self.db.save_config(self.config)
        button.label = "自动发布: " + ("✅" if self.config.auto_post else "❌")
        await interaction.response.edit_message(content=f"设置已更新：**自动发布**已{'**开启**' if self.config.auto_post else '**关闭**'}。", view=self)

    @ui.button(label="切换机器人启用状态", style=discord.ButtonStyle.danger, row=0)
    async def toggle_bot_enabled_button(self, interaction: discord.Interaction, button: ui.Button):
        self.config.bot_enabled = not self.config.bot_enabled
        self.db.save_config(self.config)
        button.label = "启用机器人: " + ("✅" if self.config.bot_enabled else "❌")
        await interaction.response.edit_message(
            content=f"设置已更新：**机器人**已{'**启用**' if self.config.bot_enabled else '**禁用**'}。\n> 禁用后，机器人将不会在你的新帖下作出任何反应。",
            view=self)

    @ui.button(label="切换二次确认", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_confirmation_button(self, interaction: discord.Interaction, button: ui.Button):
        self.config.require_confirmation = not self.config.require_confirmation
        self.db.save_config(self.config)
        button.label = "发布前二次确认: " + ("✅" if self.config.require_confirmation else "❌")
        await interaction.response.edit_message(content=f"设置已更新：**发布前二次确认**已{'**开启**' if self.config.require_confirmation else '**关闭**'}。",
                                                view=self)


# --- 辅助函数 ---
def build_license_embed(config: LicenseConfig, author: discord.User) -> discord.Embed:
    """根据配置构建授权协议的Embed"""
    details = config.license_details
    embed = discord.Embed(
        title=f"📜 {author.display_name} 的内容授权协议",
        color=discord.Color.gold()
    )
    embed.set_thumbnail(url=author.display_avatar.url)
    embed.add_field(name="🔁 转载", value=details.get("reproduce", "未设置"), inline=True)
    embed.add_field(name="🎨 衍生创作", value=details.get("derive", "未设置"), inline=True)
    embed.add_field(name="💰 商业用途", value=details.get("commercial", "未设置"), inline=True)
    embed.add_field(name="✒️ 署名要求", value=details.get("attribution", "未设置"), inline=False)

    notes = details.get("notes")
    if notes and notes != "无":
        embed.add_field(name="📝 附加说明", value=notes, inline=False)

    embed.set_footer(text=f"该协议由作者设置 | 使用 /license 命令管理你的协议")
    return embed


class FirstTimeSetupView(ui.View):
    """引导新用户首次创建协议的视图"""

    def __init__(self, db: LicenseDB, owner_id: int):
        super().__init__(timeout=3600)  # 1小时后失效
        self.db = db
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ 这不是你的帖子，不能进行操作哦。", ephemeral=True)
            return False
        return True

    @ui.button(label="✨ 创建我的授权协议", style=discord.ButtonStyle.success)
    async def create_license(self, interaction: discord.Interaction, button: ui.Button):
        """点击后弹出创建协议的表单"""
        # 即便用户是新的，get_config 也会返回一个可用的默认配置对象
        config = self.db.get_config(self.owner_id)
        modal = LicenseEditModal(self.db, config, title="创建你的首个授权协议")
        await interaction.response.send_modal(modal)

        self.stop()

    @ui.button(label="本次跳过", style=discord.ButtonStyle.secondary)
    async def skip_for_now(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(content="好的，你随时可以通过 `/license` 命令来设置你的授权协议。", view=None)
        self.stop()

    @ui.button(label="别再打扰我", style=discord.ButtonStyle.danger, row=1)
    async def disable_bot(self, interaction: discord.Interaction, button: ui.Button):
        """禁用机器人功能"""
        config = self.db.get_config(self.owner_id)
        config.bot_enabled = False
        self.db.save_config(config)
        await interaction.response.edit_message(
            content="好的，我以后不会再主动打扰你了。\n你可以随时使用 `/license settings` 命令重新启用我。",
            view=None
        )
        self.stop()


# --- 主 Cog 类 ---
class LicenseCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = getattr(bot, 'logger', None)
        self.name = "授权协议助手"
        self.db = LicenseDB()

        # 从主配置加载要监控的论坛频道ID
        config_path = Path('config.json')
        self.monitored_channel_ids = []
        if config_path.exists():
            with config_path.open('r', encoding='utf-8') as f:
                app_config = json.load(f)
                self.monitored_channel_ids = app_config.get('license_cog', {}).get('monitored_channels', [])

    @commands.Cog.listener()
    async def on_ready(self):
        if self.logger:
            self.logger.info(f"授权协议助手已加载，监控 {len(self.monitored_channel_ids)} 个频道。")

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        """当有新帖子创建时触发"""
        # 检查是否为被监控的论坛频道
        if thread.parent_id not in self.monitored_channel_ids:
            return

        # 排除机器人自己创建的帖子
        if thread.owner_id == self.bot.user.id:
            return

        # 等待一小段时间，避免和用户自己的编辑冲突
        await asyncio.sleep(2)

        author_id = thread.owner_id
        config = self.db.get_config(author_id)

        # 如果用户禁用了机器人，则直接返回
        if not config.bot_enabled:
            return

        # 获取作者成员对象
        author = thread.guild.get_member(author_id)
        if not author:
            # 如果找不到成员，可能已离开服务器
            return

            # --- 核心逻辑改动：检查用户文件是否存在 ---
        user_config_file = self.db._get_user_file(author_id)

        if not user_config_file.exists():
            # --- 新用户路径 ---
            embed = discord.Embed(
                title=f"欢迎, {author.display_name}！我是内容授权助手",
                description=(
                    "我可以帮助你在每次发布作品后，轻松附上你的授权协议，保护你的创作权益。\n\n"
                    "点击下方按钮，开始创建你的第一份默认协议吧！"
                ),
                color=discord.Color.magenta()  # 使用醒目的颜色
            )
            embed.set_footer(text="这只需要一分钟！你之后可以随时用 /license 命令修改。")
            view = FirstTimeSetupView(self.db, author_id)
            await thread.send(content=author.mention, embed=embed, view=view)
            if self.logger:
                self.logger.info(f"为新用户 {author.display_name} 发送了首次设置引导。")
            return  # 结束该用户的处理流程

        # --- 老用户路径 (逻辑和之前一样) ---
        config = self.db.get_config(author_id)

        if config.auto_post:
            # 自动发布模式
            embed = build_license_embed(config, author)
            await thread.send(embed=embed)
            if self.logger:
                self.logger.info(f"为 {author.display_name} 的帖子 {thread.name} 自动发布了授权协议。")
        else:
            # 询问模式
            embed = discord.Embed(
                title=f"👋 你好, {author.display_name}！",
                description="我注意到你发布了一个新作品。你希望如何处理内容的授权协议呢？",
                color=discord.Color.blue()
            )
            # 修改提示语，使其符合实际情况
            embed.set_footer(text="只有帖主才能操作这些按钮。")
            view = InitialActionView(self.db, config, thread)

            # 发送一个公开但@帖主的消息，移除 ephemeral 参数
            await thread.send(content=f"{author.mention}", embed=embed, view=view)

            if self.logger:
                self.logger.info(f"向 {author.display_name} 发送了授权协议询问。")

    # --- 斜杠命令 ---
    license_group = app_commands.Group(name="license", description="管理你的内容授权协议")

    @license_group.command(name="edit", description="创建或修改你的默认授权协议")
    async def edit_license(self, interaction: discord.Interaction):
        """打开表单来编辑默认授权协议"""
        config = self.db.get_config(interaction.user.id)
        modal = LicenseEditModal(self.db, config)
        await interaction.response.send_modal(modal)

    @license_group.command(name="settings", description="配置授权助手机器人的行为")
    async def settings(self, interaction: discord.Interaction):
        """配置机器人是自动发布还是每次询问"""
        config = self.db.get_config(interaction.user.id)
        view = SettingsView(self.db, config)
        embed = discord.Embed(
            title="⚙️ 机器人设置",
            description="请选择你希望机器人如何为你工作。",
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @license_group.command(name="show", description="查看你当前的默认授权协议")
    async def show_license(self, interaction: discord.Interaction):
        """显示你当前的默认协议"""
        config = self.db.get_config(interaction.user.id)
        embed = build_license_embed(config, interaction.user)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(LicenseCog(bot))
