# -*- coding: utf-8 -*-
"""
授权协议助手 (LicenseCog)

本模块实现了一个 Discord Cog，旨在帮助服务器内的创作者管理其作品的内容授权协议。
主要功能包括：
- 在指定论坛频道中，当有新帖子（作品）发布时，自动向作者发送交互式提醒。
- 允许用户通过斜杠命令 (`/`) 或交互式按钮创建、编辑、查看和管理自己的默认授权协议。
- 支持标准的 Creative Commons (CC) 协议模板和完全自定义的协议。
- 提供精细的机器人行为设置，如启用/禁用、自动发布、发布前确认等。
- 所有交互均通过现代的 discord.py UI 组件（Views, Modals）实现，提供流畅的用户体验。

设计核心：
- 数据持久化：用户配置存储在 `data/licenses/` 目录下的 JSON 文件中，以用户ID命名。
- 缓存机制：`LicenseDB` 类实现了内存缓存，以减少频繁的磁盘I/O。
- 模块化UI：每个交互界面（如主面板、编辑中心、设置面板）都被封装在独立的 `discord.ui.View` 类中。
- 回调驱动逻辑：UI组件间的复杂流程通过传递回调函数 (callback) 来解耦和驱动，例如，一个视图完成其任务后，会调用传入的回调函数来触发下一步操作（如保存数据或切换到另一个视图）。
"""

import asyncio
import json
from pathlib import Path

import discord
from discord import app_commands, ui
from discord.ext import commands

# 假设这个工具函数位于其他地方，用于创建一个标准的确认视图
# from src.utils.confirm_view import confirm_view_embed # 在此文件中，我们使用了一个内联的简化版 CustomConfirmView

# --- 全局常量与配置 ---

# 用于在清理历史消息时识别本机器人发出的交互面板
HELPER_SIGNATURE = "授权协议助手"

# Creative Commons 协议的标准化数据。这是所有CC协议信息的“唯一真实来源”。
# 程序在生成CC协议Embed时会强制使用这里的数据，以保证协议的准确性。
# 格式：{ "显示名称": {"reproduce": "转载条款", "derive": "演绎条款", "commercial": "商业用途条款", "url": "官方协议链接"} }
CC_LICENSES = {
    "CC BY 4.0": {
        "reproduce": "允许，但需署名",
        "derive": "允许，但需署名",
        "commercial": "允许，但需署名",
        "url": "https://creativecommons.org/licenses/by/4.0/deed.zh-hans"
    },
    "CC BY-SA 4.0": {
        "reproduce": "允许，但需署名并以相同方式共享",
        "derive": "允许，但需署名并以相同方式共享",
        "commercial": "允许，但需署名并以相同方式共享",
        "url": "https://creativecommons.org/licenses/by-sa/4.0/deed.zh-hans"
    },
    "CC BY-NC 4.0": {
        "reproduce": "允许，但需署名且不得用于商业目的",
        "derive": "允许，但需署名且不得用于商业目的",
        "commercial": "禁止",
        "url": "https://creativecommons.org/licenses/by-nc/4.0/deed.zh-hans"
    },
    "CC BY-NC-SA 4.0": {
        "reproduce": "允许，但需署名、非商业性使用、并以相同方式共享",
        "derive": "允许，但需署名、非商业性使用、并以相同方式共享",
        "commercial": "禁止",
        "url": "https://creativecommons.org/licenses/by-nc-sa/4.0/deed.zh-hans"
    },
    "CC BY-ND 4.0": {
        "reproduce": "允许，但需署名且不得修改",
        "derive": "禁止",
        "commercial": "允许，但需署名且不得修改",
        "url": "https://creativecommons.org/licenses/by-nd/4.0/deed.zh-hans"
    },
    "CC BY-NC-ND 4.0": {
        "reproduce": "允许，但需署名、非商业性使用、且不得修改",
        "derive": "禁止",
        "commercial": "禁止",
        "url": "https://creativecommons.org/licenses/by-nc-nd/4.0/deed.zh-hans"
    },
}

# 协议编辑中心的通用说明文本，方便在多处复用。
HUB_VIEW_CONTENT = (
    "请选择你希望如何设置你的授权协议：\n\n"
    "📝 **创建或编辑自定义协议**\n"
    "> 在这里，你可以完全手动控制每一项条款。最终生成的将是你独有的“自定义协议”。\n\n"
    "📜 **应用一个标准的CC协议**\n"
    "> 从官方的 Creative Commons 协议中选择一个来应用。\n"
    "> **注意：** 选择后，你当前的设置将被一个标准的CC协议模板所**覆盖**。\n"
    "> CC协议的核心条款是标准化的，任何附加的限制性条款都可能被视为无效。\n"
    "> 了解更多： https://creativecommons.org"
)


def get_default_license_details(user_id: int) -> dict:
    """
    为新用户或重置用户生成一份默认的授权协议详情。
    Args:
        user_id: 用户的Discord ID，用于设置默认的署名。
    Returns:
        一个包含默认协议内容的字典。
    """
    return {
        "type": "custom",  # 默认类型为自定义
        "reproduce": "询问作者",
        "derive": "询问作者",
        "commercial": "禁止",
        "attribution": f"<@{user_id}>",  # 默认署名为@用户
        "notes": "无"
    }


# ============================================
#            命令与本地化配置
# ============================================
# 将所有斜杠命令的名称和描述集中在此处，便于未来进行本地化或统一修改。
COMMAND_CONFIG = {
    "group": {
        "name": "license",
        "description": "Manage your content license agreement"
    },
    "remind": {
        "name": "remind",
        "description": "Resend the license helper prompt in the current post"
    },
    "edit": {
        "name": "edit",
        "description": "Create or edit your default license agreement"
    },
    "settings": {
        "name": "settings",
        "description": "Configure the behavior of the license helper bot"
    },
    "show": {
        "name": "show",
        "description": "View your current default license agreement"
    }
}

COMMAND_CONFIG_ZH = {
    "group": {
        "name": "内容授权",
        "description": "管理你的内容授权协议"
    },
    "remind": {
        "name": "重新发送提醒",
        "description": "在当前帖子中重新发送授权助手提醒"
    },
    "edit": {
        "name": "编辑",
        "description": "创建或修改你的默认授权协议"
    },
    "settings": {
        "name": "设置",
        "description": "配置授权助手机器人的行为"
    },
    "show": {
        "name": "查看",
        "description": "查看你当前的默认授权协议"
    }
}

# 在代码中激活一套配置。当前选择中文版。
ACTIVE_COMMAND_CONFIG = COMMAND_CONFIG_ZH


# --- 数据模型与存储层 ---

class LicenseConfig:
    """
    数据类，用于封装单个用户的所有授权相关配置。
    它代表了从JSON文件加载或即将存入JSON文件的完整数据结构。
    """

    def __init__(self, user_id: int, data: dict = None):
        """
        初始化一个用户的配置对象。
        Args:
            user_id: 用户的Discord ID。
            data: 从JSON文件加载的原始字典数据。如果为None，则使用默认值。
        """
        if data is None:
            data = {}
        self.user_id: int = user_id
        # 用户是否启用本功能。如果禁用，则机器人不会在用户发帖时主动提醒。
        self.bot_enabled: bool = data.get('bot_enabled', True)
        # 是否自动发布协议。如果为True，发帖提醒时将不提供交互按钮，直接发布默认协议。
        # 注意：当前实现中，此选项未被完全利用，而是提供了“发布默认协议”按钮。
        self.auto_post: bool = data.get('auto_post', False)
        # 发布协议前是否需要用户二次确认。
        self.require_confirmation: bool = data.get('require_confirmation', True)
        # 协议的具体内容。
        self.license_details: dict = data.get('license_details', get_default_license_details(user_id))


class LicenseDB:
    """
    数据访问层，负责处理用户授权配置的读写操作。
    它抽象了对文件系统的直接访问，并实现了一个简单的内存缓存以提高性能。
    """

    def __init__(self):
        self.data_path = Path("data/licenses")
        self.data_path.mkdir(parents=True, exist_ok=True)
        # 缓存: {user_id: LicenseConfig}。避免每次请求都读取文件。
        self._cache: dict[int, LicenseConfig] = {}

    def _get_user_file(self, user_id: int) -> Path:
        """获取指定用户ID对应的JSON文件路径。"""
        return self.data_path / f"{user_id}.json"

    def get_config(self, user_id: int) -> LicenseConfig:
        """
        获取用户的配置对象。这是获取配置的唯一入口。
        流程:
        1. 检查缓存中是否存在该用户的配置，如果存在则直接返回。
        2. 如果缓存未命中，则尝试从文件加载。
        3. 如果文件不存在或解析失败，则创建一个新的默认配置。
        4. 将加载或创建的配置存入缓存，然后返回。
        """
        # 1. 查缓存
        if user_id in self._cache:
            return self._cache[user_id]

        # 2. 缓存未命中，从文件加载
        user_file = self._get_user_file(user_id)
        if not user_file.exists():
            config = LicenseConfig(user_id)  # 文件不存在，创建新的默认配置
        else:
            try:
                with user_file.open('r', encoding='utf-8') as f:
                    data = json.load(f)
                config = LicenseConfig(user_id, data)
            except (json.JSONDecodeError, IOError):
                config = LicenseConfig(user_id)  # 文件损坏或读取错误，使用默认配置

        # 3. 存入缓存
        self._cache[user_id] = config
        return config

    def save_config(self, config: LicenseConfig):
        """
        将用户的配置对象保存到文件，并同步更新缓存。
        这是保证数据一致性的关键：任何保存操作必须同时影响持久化存储和内存缓存。
        """
        user_file = self._get_user_file(config.user_id)
        data = {
            "bot_enabled": config.bot_enabled,
            "auto_post": config.auto_post,
            "require_confirmation": config.require_confirmation,
            "license_details": config.license_details
        }
        with user_file.open('w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

        # 关键：同时更新缓存
        self._cache[config.user_id] = config

    def delete_config(self, user_id: int):
        """
        删除用户的配置文件，并从缓存中移除。
        同样需要保证文件系统和缓存的一致性。
        """
        # 1. 删除文件
        user_file = self._get_user_file(user_id)
        if user_file.exists():
            try:
                user_file.unlink()
            except OSError as e:
                # 记录错误，但继续尝试清理缓存
                print(f"Error deleting file {user_file}: {e}")

        # 2. 从缓存中移除
        if user_id in self._cache:
            del self._cache[user_id]


# --- 交互界面层 (Modals & Views) ---

class LicenseEditHubView(ui.View):
    """
    授权协议编辑的“枢纽”视图。
    它本身不进行编辑，而是提供两个入口，将用户引导至“自定义编辑”或“CC协议选择”流程。
    这是一个典型的“路由器”或“分发器”视图模式。

    设计模式：
    - 回调函数 (`callback`, `on_cancel`)：此类不处理最终的数据保存逻辑，而是通过构造函数接收
      回调函数。当用户完成操作（如通过Modal提交）或取消时，它会调用这些回调，将控制权和
      结果数据交还给上层调用者（如 `InitialActionView` 或斜杠命令）。
    """

    def __init__(self, db: LicenseDB, config: LicenseConfig, callback: callable, on_cancel: callable):
        """
        Args:
            db: LicenseDB 实例，用于传递给子组件。
            config: 当前用户的配置，用于提供默认值。
            callback: 编辑成功后的回调函数，签名应为 `async def callback(interaction, new_details: dict)`。
            on_cancel: 用户点击取消按钮时的回调函数，签名应为 `async def on_cancel(interaction)`。
        """
        super().__init__(timeout=300)
        self.db = db
        self.config = config
        self.callback = callback
        self.on_cancel = on_cancel

    @ui.button(label="📝 使用自定义文本填写", style=discord.ButtonStyle.primary, row=0)
    async def set_with_custom(self, interaction: discord.Interaction, button: ui.Button):
        """点击此按钮，会弹出一个用于填写所有自定义协议条款的 Modal。"""
        # 创建 Modal，并将顶层回调函数 `self.callback` 传递给它。
        modal = CustomLicenseEditModal(self.db, self.config, callback=self.callback)
        await interaction.response.send_modal(modal)

    @ui.button(label="📜 从CC协议模板中选择", style=discord.ButtonStyle.secondary, row=0)
    async def set_with_cc(self, interaction: discord.Interaction, button: ui.Button):
        """点击此按钮，会将当前视图替换为 CC 协议选择视图。"""
        # 创建下一个视图，并将顶层回调 `self.callback` 传递给它。
        cc_view = CCLicenseSelectView(self.db, self.config, callback=self.callback)
        cc_select_content = (
            "你正在选择一个标准的CC协议模板。\n\n"
            "- 你选择的协议将**覆盖**你当前的授权设置。\n"
            "- 你可以修改后续弹出的“署名要求”和“附加说明”，但这些不会改变CC协议的核心条款。\n"
            "- 如果你想在CC协议的基础上做更多修改，请返回并选择“创建或编辑自定义协议”，然后手动输入你的条款。"
        )
        # 关键的UI流程：使用新视图替换当前视图来响应交互。
        await interaction.response.edit_message(
            content=cc_select_content,
            view=cc_view
        )

    @ui.button(label="取消", style=discord.ButtonStyle.danger, row=1)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        """点击取消，直接调用顶层的 `on_cancel` 回调。"""
        await self.on_cancel(interaction)


class AttributionNotesModal(ui.Modal, title="填写署名与备注"):
    """
    一个简单的 Modal，仅用于让用户填写“署名要求”和“附加说明”。
    在选择CC协议后弹出，用于补充非核心条款。
    """

    def __init__(self, default_attribution: str, default_notes: str, final_callback: callable):
        """
        Args:
            default_attribution: 默认显示的署名要求。
            default_notes: 默认显示的附加说明。
            final_callback: 用户提交 Modal 后的回调，签名应为 `async def callback(interaction, attribution: str, notes: str)`。
        """
        super().__init__()
        self.attribution = ui.TextInput(label="署名要求", default=default_attribution)
        self.notes = ui.TextInput(label="附加说明 (可选)", default=default_notes if default_notes != "无" else "", required=False,
                                  style=discord.TextStyle.paragraph)
        self.add_item(self.attribution)
        self.add_item(self.notes)
        self.final_callback = final_callback

    async def on_submit(self, interaction: discord.Interaction):
        """当用户提交时，调用最终回调并传入填写的数据。"""
        await self.final_callback(interaction, self.attribution.value, self.notes.value or "无")


class CustomLicenseEditModal(ui.Modal, title="编辑自定义授权协议"):
    """
    一个用于完整编辑自定义授权协议的 Modal。
    包含所有协议条款的文本输入框。
    """

    def __init__(self, db: LicenseDB, current_config: LicenseConfig, callback: callable):
        """
        Args:
            db: LicenseDB 实例。
            current_config: 当前用户配置，用于填充默认值。
            callback: 提交后的回调，签名应为 `async def callback(interaction, new_details: dict)`。
        """
        super().__init__()
        self.db = db
        self.config = current_config
        self.callback = callback  # 存储顶层回调

        details = current_config.license_details
        self.reproduce = ui.TextInput(label="是否允许转载？", default=details.get("reproduce"), max_length=100)
        self.derive = ui.TextInput(label="是否允许演绎？", default=details.get("derive"), max_length=100)
        self.commercial = ui.TextInput(label="是否允许商业性使用？", default=details.get("commercial"), max_length=100)
        self.attribution = ui.TextInput(label="署名要求", default=details.get("attribution", f"<@{self.config.user_id}>"), max_length=100)
        self.notes = ui.TextInput(label="附加说明 (可选)", default=details.get("notes", "无") if details.get("notes", "无") != "无" else "", required=False,
                                  style=discord.TextStyle.paragraph)

        # Discord Modal 最多只能有5个输入框
        self.add_item(self.reproduce)
        self.add_item(self.derive)
        self.add_item(self.commercial)
        self.add_item(self.attribution)
        self.add_item(self.notes)

    async def on_submit(self, interaction: discord.Interaction):
        """用户提交时，构建新的协议详情字典，并调用顶层回调。"""
        new_details = {
            "type": "custom",  # 明确标记为自定义协议
            "reproduce": self.reproduce.value,
            "derive": self.derive.value,
            "commercial": self.commercial.value,
            "attribution": self.attribution.value,
            "notes": self.notes.value or "无"
        }
        # 调用从 LicenseEditHubView -> CustomLicenseEditModal 一路传递下来的回调函数
        await self.callback(interaction, new_details)


class CCLicenseSelectView(ui.View):
    """
    让用户通过下拉菜单选择一个标准CC协议的视图。

    设计模式：
    - 级联交互：用户在此视图中选择一个CC协议后，并不会立即结束流程。而是会触发 `handle_selection`，
      该方法会弹出一个 `AttributionNotesModal` 来收集额外信息（署名、备注）。
    - 中介回调 (`modal_submit_callback`)：为了将CC协议选择结果和Modal的填写结果合并，
      `handle_selection` 定义了一个临时的 "中介" 回调函数。这个中介函数负责组合所有数据，
      然后调用最顶层的回调函数。
    """

    def __init__(self, db: LicenseDB, config: LicenseConfig, callback: callable):
        super().__init__(timeout=300)
        self.db = db
        self.config = config
        self.callback = callback  # 存储顶层回调

        options = [discord.SelectOption(label=name, value=name) for name in CC_LICENSES.keys()]
        self.add_item(ui.Select(placeholder="选择一个CC协议...", options=options, custom_id="cc_select"))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """
        这个方法在 `discord.py` 内部被调用，用于在处理具体组件前进行检查。
        我们用它来捕获下拉菜单的选择事件，并分发到处理函数。
        """
        if interaction.data.get("custom_id") == "cc_select":
            # 这是一个 Select 交互，我们自己处理它
            await self.handle_selection(interaction)
        return True  # 允许交互继续

    async def handle_selection(self, interaction: discord.Interaction):
        """处理用户在下拉菜单中的选择。"""
        selected_cc = interaction.data["values"][0]
        cc_data = CC_LICENSES[selected_cc]

        # 定义一个“中介”回调函数，它将被传递给下一个 Modal。
        # 它的作用是：等待 Modal 提交，然后将 Modal 的数据与当前选择的CC协议数据合并。
        async def modal_submit_callback(modal_interaction, attribution, notes):
            # 组合来自 CC 模板和 Modal 的数据
            final_details = {
                "type": selected_cc,
                "reproduce": cc_data["reproduce"],
                "derive": cc_data["derive"],
                "commercial": cc_data["commercial"],
                "attribution": attribution,
                "notes": notes or "无"
            }
            # 最后，调用最初传入的顶层回调函数，将最终结果传递出去
            await self.callback(modal_interaction, final_details)

        # 弹出 Modal，用于填写署名和备注，并将我们的“中介”回调传给它。
        modal = AttributionNotesModal(
            default_attribution=self.config.license_details.get("attribution", f"<@{interaction.user.id}>"),
            default_notes=self.config.license_details.get("notes", "无"),
            final_callback=modal_submit_callback
        )
        await interaction.response.send_modal(modal)


class ConfirmPostView(ui.View):
    """
    一个通用的、用于最终确认操作的视图。
    常见于“预览并发布”的场景。
    """

    def __init__(self, author_id: int, on_confirm: callable, on_cancel: callable):
        """
        Args:
            author_id: 授权进行操作的用户ID。
            on_confirm: 点击确认按钮时调用的回调，签名 `async def on_confirm(interaction)`。
            on_cancel: 点击取消/返回按钮时调用的回调，签名 `async def on_cancel(interaction)`。
        """
        super().__init__(timeout=300)
        self.author_id = author_id
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """确保只有指定的用户可以点击按钮。"""
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 这不是你的确认按钮哦。", ephemeral=True)
            return False
        return True

    @ui.button(label="✅ 确认发布", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        """调用确认回调。"""
        await self.on_confirm(interaction)

    @ui.button(label="❌ 返回", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        """调用取消回调。"""
        await self.on_cancel(interaction)


class InitialActionView(ui.View):
    """
    这是用户发帖后看到的主要交互面板（针对已注册用户）。
    提供了所有核心操作的入口：直接发布、临时编辑后发布、永久编辑、预览、设置等。
    """

    def __init__(self, cog: 'LicenseCog', db: LicenseDB, config: LicenseConfig, thread: discord.Thread):
        super().__init__(timeout=3600)  # 较长的超时时间，给用户充分的反应时间
        self.cog = cog
        self.db = db
        self.config = config
        self.thread = thread
        self.owner_id = thread.owner_id
        # 缓存原始的Embed，以便在各种操作后可以方便地“返回主菜单”。
        self.original_embed = self.build_original_embed()

    def build_original_embed(self) -> discord.Embed:
        """构建主交互面板的Embed。"""
        # 注意：这里可能需要从 self.cog.bot 获取最新的用户信息，因为 display_name 可能改变
        user = self.cog.bot.get_user(self.owner_id)
        display_name = user.display_name if user else "创作者"

        embed = discord.Embed(
            title=f"👋 你好, {display_name}！",
            description="我注意到你发布了一个新作品。你希望如何处理内容的授权协议呢？",
            color=discord.Color.blue()
        )
        cmd_name = ACTIVE_COMMAND_CONFIG["group"]["name"]
        cmd_name_remind = ACTIVE_COMMAND_CONFIG["remind"]["name"]
        embed.set_footer(text=f"{HELPER_SIGNATURE} | 如果按钮失效，请使用 `/{cmd_name} {cmd_name_remind}`")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """确保只有帖子作者可以操作。"""
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ 这不是你的帖子，不能进行操作哦。", ephemeral=True)
            return False
        return True

    # --- 核心UI流程方法 ---

    async def show_confirmation_view(self, interaction: discord.Interaction, config_to_show: LicenseConfig):
        """
        显示预览和确认发布的界面。这是一个可复用的流程。
        Args:
            interaction: 触发此流程的交互。
            config_to_show: 需要被预览和发布的 `LicenseConfig` 对象。
        """
        final_embed = build_license_embed(config_to_show, interaction.user)
        preview_embed = final_embed.copy()
        preview_embed.title = f"🔍 预览：{preview_embed.title}"
        preview_embed.description = "**请预览你将要发布的协议。**\n确认后将发布到帖子中，并关闭此面板。"

        # 定义确认和取消按钮的具体行为
        async def do_post(post_interaction: discord.Interaction):
            """确认后的操作：在帖子中发布协议并关闭面板。"""
            await self.thread.send(embed=final_embed)
            await post_interaction.response.edit_message(
                content="✅ 协议已发布。", embed=None, view=None
            )
            self.stop()  # 停止此 InitialActionView 的监听

        async def do_cancel(cancel_interaction: discord.Interaction):
            """取消后的操作：返回主菜单。"""
            await self.back_to_main_menu(cancel_interaction)

        # 创建并显示确认视图
        confirm_view = ConfirmPostView(
            author_id=interaction.user.id,
            on_confirm=do_post,
            on_cancel=do_cancel
        )
        # 用预览Embed和确认视图替换当前界面
        await interaction.response.edit_message(embed=preview_embed, view=confirm_view)

    async def back_to_main_menu(self, interaction: discord.Interaction):
        """
        一个可复用的方法，用于将UI完全恢复到初始的主菜单状态。
        """
        if not self.original_embed:
            self.original_embed = self.build_original_embed()

        # 核心：用原始的Embed和自身(self, 即InitialActionView)来编辑消息，实现“返回”效果。
        await interaction.response.edit_message(
            content=None,  # 清除可能存在的上层文本，如“你正在编辑...”
            embed=self.original_embed,
            view=self
        )

    # --- 按钮定义 ---

    @ui.button(label="发布默认协议", style=discord.ButtonStyle.success, row=0)
    async def post_default(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：直接使用用户保存的默认配置进行发布流程。"""
        await self.show_confirmation_view(interaction, self.config)

    @ui.button(label="编辑并发布(仅本次)", style=discord.ButtonStyle.primary, row=0)
    async def edit_and_post_once(self, interaction: discord.Interaction, button: ui.Button):
        """
        按钮：临时编辑协议并发布，不保存更改到用户的默认配置。
        设计模式：定义临时的回调函数，传递给编辑枢纽视图。
        """

        # 定义编辑完成后的行为：使用临时的协议配置进入确认流程。
        async def on_edit_complete(edit_interaction: discord.Interaction, temp_details: dict):
            # 创建一个临时的配置对象来承载这次的编辑结果
            temp_config = LicenseConfig(self.owner_id)
            temp_config.license_details = temp_details
            # 使用这个临时配置来显示预览和确认
            await self.show_confirmation_view(edit_interaction, temp_config)

        # 定义取消编辑的行为：返回主菜单。
        async def on_edit_cancel(cancel_interaction: discord.Interaction):
            await self.back_to_main_menu(cancel_interaction)

        hub_view = LicenseEditHubView(
            db=self.db, config=self.config,
            callback=on_edit_complete,
            on_cancel=on_edit_cancel
        )

        # 用编辑枢纽视图替换主菜单视图
        await interaction.response.edit_message(
            content=(
                "你正在为你**本次发布**编辑一个临时协议。\n"
                "这个操作**不会**更改你保存的默认协议。\n"
                f"{HUB_VIEW_CONTENT}"
            ),
            embed=None,  # 清理掉主菜单的Embed
            view=hub_view
        )

    @ui.button(label="永久编辑默认协议", style=discord.ButtonStyle.secondary, row=1)
    async def edit_default_license(self, interaction: discord.Interaction, button: ui.Button):
        """
        按钮：编辑并永久保存用户的默认协议。
        """

        # 定义编辑完成后的行为：保存配置，然后返回主菜单。
        async def on_edit_complete(edit_interaction: discord.Interaction, new_details: dict):
            # 1. 保存数据到数据库
            self.config.license_details = new_details
            self.db.save_config(self.config)

            # 2. 返回主菜单，并发送一个私密的确认消息
            await self.back_to_main_menu(edit_interaction)
            await edit_interaction.followup.send("✅ 你的默认协议已永久保存！", ephemeral=True)

        # 定义取消编辑的行为：返回主菜单。
        async def on_edit_cancel(cancel_interaction: discord.Interaction):
            await self.back_to_main_menu(cancel_interaction)

        hub_view = LicenseEditHubView(
            db=self.db, config=self.config,
            callback=on_edit_complete,
            on_cancel=on_edit_cancel
        )

        # 用编辑枢纽视图替换主菜单视图
        await interaction.response.edit_message(
            content=(
                "你正在**永久编辑**你的默认协议。\n"
                "保存后，这将成为你未来的默认设置。\n"
                f"{HUB_VIEW_CONTENT}"
            ),
            embed=None,
            view=hub_view
        )

    @ui.button(label="预览协议", style=discord.ButtonStyle.primary, row=0)
    async def preview_license(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：以一条临时的、只有自己能看到的消息来预览当前默认协议。"""
        # defer() 只是为了确认交互，防止超时。
        await interaction.response.defer(thinking=False, ephemeral=True)

        embed = build_license_embed(self.config, interaction.user)
        embed.title = "👀 你的当前默认协议预览"

        # 使用 followup.send 发送私密消息。这是最可靠的发送 ephemeral 消息的方式。
        await interaction.followup.send(embed=embed, ephemeral=True)

    @ui.button(label="机器人设置", style=discord.ButtonStyle.secondary, row=1)
    async def settings(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：打开独立的机器人行为设置面板。"""
        # 这个逻辑和斜杠命令 `/内容授权 设置` 完全一样
        config = self.db.get_config(interaction.user.id)
        view = SettingsView(self.db, config, self.cog)

        embed = discord.Embed(
            title="⚙️ 机器人设置",
            description="在这里管理授权助手的所有行为。\n完成后，点击下方的“关闭面板”即可。",
            color=discord.Color.blurple()
        )
        # 发送一个全新的、只有自己可见的消息作为设置面板
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @ui.button(label="本次跳过", style=discord.ButtonStyle.secondary, row=1)
    async def skip_for_now(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：关闭交互面板，不执行任何操作。"""
        cmd_name = ACTIVE_COMMAND_CONFIG["group"]["name"]
        await interaction.response.edit_message(
            content=f"好的，你随时可以通过 `/{cmd_name}` 命令来设置你的授权协议。",
            embed=None, view=None
        )
        self.stop()

    @ui.button(label="别再打扰我", style=discord.ButtonStyle.danger, row=1)
    async def disable_bot(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：禁用机器人，机器人将不再主动发送提醒。"""
        config = self.db.get_config(self.owner_id)
        config.bot_enabled = False
        self.db.save_config(config)

        cmd_name = ACTIVE_COMMAND_CONFIG["group"]["name"]
        cmd_name_setting = ACTIVE_COMMAND_CONFIG["settings"]["name"]
        await interaction.response.edit_message(
            content=f"好的，我以后不会再主动打扰你了。\n你可以随时使用 `/{cmd_name} {cmd_name_setting}` 命令重新启用我。",
            embed=None, view=None
        )
        self.stop()


class SettingsView(ui.View):
    """
    机器人行为设置的独立面板视图。
    用户可以在这里开关各项功能。

    设计模式：
    - 状态自更新：每个开关按钮被点击后，会更新后台数据，然后调用 `update_button_labels` 和
      `interaction.response.edit_message(view=self)` 来刷新自身，从而在界面上即时反映出
      新的状态（如 ✅ 和 ❌ 的切换），提供了良好的交互反馈。
    - 独立确认流程：对于危险操作（重置、删除数据），它不会直接执行，而是会弹出另一个
      临时的、独立的确认视图（`ConfirmPostView`），防止用户误操作。
    """

    def __init__(self, db: 'LicenseDB', config: 'LicenseConfig', cog: 'LicenseCog'):
        super().__init__(timeout=600)
        self.db = db
        self.config = config
        self.cog = cog  # 传入Cog实例，主要为了访问 logger
        self.update_button_labels()  # 初始化时设置正确的按钮标签

    def update_button_labels(self):
        """根据当前的 `self.config` 状态，更新按钮上的标签和表情符号。"""
        self.toggle_auto_post_button.label = f"自动发布: {'✅' if self.config.auto_post else '❌'}"
        self.toggle_bot_enabled_button.label = f"机器人总开关: {'✅' if self.config.bot_enabled else '❌'}"
        self.toggle_confirmation_button.label = f"发布前二次确认: {'✅' if self.config.require_confirmation else '❌'}"

    @ui.button(label="切换自动发布", style=discord.ButtonStyle.primary, row=0)
    async def toggle_auto_post_button(self, interaction: discord.Interaction, button: ui.Button):
        """切换“自动发布”选项。"""
        self.config.auto_post = not self.config.auto_post
        self.db.save_config(self.config)
        self.update_button_labels()
        # 用更新后的自己重新渲染视图，以实时更新按钮标签
        await interaction.response.edit_message(view=self)

    @ui.button(label="切换机器人总开关", style=discord.ButtonStyle.primary, row=0)
    async def toggle_bot_enabled_button(self, interaction: discord.Interaction, button: ui.Button):
        """切换“机器人总开关”选项。"""
        self.config.bot_enabled = not self.config.bot_enabled
        self.db.save_config(self.config)
        self.update_button_labels()
        await interaction.response.edit_message(view=self)

    @ui.button(label="切换发布前二次确认", style=discord.ButtonStyle.primary, row=1)
    async def toggle_confirmation_button(self, interaction: discord.Interaction, button: ui.Button):
        """切换“发布前二次确认”选项。"""
        self.config.require_confirmation = not self.config.require_confirmation
        self.db.save_config(self.config)
        self.update_button_labels()
        await interaction.response.edit_message(view=self)

    @ui.button(label="重置我的协议", style=discord.ButtonStyle.danger, row=2)
    async def reset_license(self, interaction: discord.Interaction, button: ui.Button):
        """重置用户的授权协议为默认值，这是一个危险操作，需要二次确认。"""

        async def on_confirm(confirm_interaction: discord.Interaction):
            # 确认后，执行重置操作
            self.config.license_details = get_default_license_details(self.config.user_id)
            self.db.save_config(self.config)
            await confirm_interaction.response.edit_message(content="✅ 你的授权协议已重置为默认值。", embed=None, view=None)

        async def on_cancel(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(content="🚫 操作已取消。", embed=None, view=None)

        # 发起一个独立的、临时的确认流程
        confirm_view = ConfirmPostView(interaction.user.id, on_confirm, on_cancel)
        await interaction.response.send_message(
            "**⚠️ 警告：** 此操作会将你的默认协议恢复为初始设置！\n请确认你的操作：",
            view=confirm_view,
            ephemeral=True
        )

    @ui.button(label="删除所有数据", style=discord.ButtonStyle.danger, row=2)
    async def delete_data(self, interaction: discord.Interaction, button: ui.Button):
        """删除用户在本机器人中的所有数据，这是一个非常危险的操作，需要二次确认。"""

        async def on_confirm(confirm_interaction: discord.Interaction):
            # 执行真正的删除操作
            try:
                self.db.delete_config(self.config.user_id)
            except OSError as e:
                if self.cog.logger: self.cog.logger.error(f"删除用户数据文件失败: {self.config.user_id}, 错误: {e}")
                await confirm_interaction.response.edit_message(content=f"❌ 删除数据时发生错误！请联系管理员。", view=None)
                return

            # 成功后，更新确认消息并尝试删除原设置面板
            await confirm_interaction.response.edit_message(content="🗑️ **你的所有数据已被永久删除。**", view=None)
            try:
                # interaction 是 SettingsView 的交互，不是 confirm_interaction 的
                await interaction.delete_original_response()
            except discord.NotFound:
                pass  # 如果找不到了就算了
            self.stop()  # 停止当前 SettingsView 的生命周期

        async def on_cancel(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(content="🚫 操作已取消，你的数据安然无恙。", view=None)

        # 创建并发送带有强烈警告的独立确认视图
        confirm_view = ConfirmPostView(interaction.user.id, on_confirm, on_cancel)
        await interaction.response.send_message(
            "**🚨 终极警告：此操作不可逆！🚨**\n\n"
            "你确定要**永久删除**你保存在本机器人中的所有数据吗？这包括：\n"
            "- 你保存的默认授权协议\n"
            "- 所有的机器人行为设置\n\n"
            "**此操作无法撤销！请再次确认！**",
            view=confirm_view,
            ephemeral=True
        )

    @ui.button(label="关闭面板", style=discord.ButtonStyle.secondary, row=3)
    async def close_panel(self, interaction: discord.Interaction, button: ui.Button):
        """关闭（即删除）这个设置面板消息。"""
        await interaction.response.defer()  # 先响应，防止超时
        await interaction.delete_original_response()
        self.stop()


# --- 辅助函数 ---
def build_license_embed(config: LicenseConfig, author: discord.User) -> discord.Embed:
    """
    根据给定的配置对象和作者信息，构建一个美观的授权协议 Embed。

    核心安全逻辑：
    如果协议类型是CC协议，此函数会强制使用 `CC_LICENSES` 全局常量中的条款文本来渲染Embed，
    忽略用户可能在 `license_details` 中保存的自定义文本。这确保了CC协议的标准化和正确性，
    防止用户创建出“伪CC协议”。

    Args:
        config: 用户的 `LicenseConfig` 对象。
        author: 发布内容的用户对象 (`discord.User` 或 `discord.Member`)。

    Returns:
        一个配置好的 `discord.Embed` 对象。
    """
    saved_details = config.license_details
    license_type = saved_details.get("type", "custom")

    # 创建一个用于展示的字典副本，这是我们将要操作的对象
    display_details = saved_details.copy()

    # --- 核心安全阀逻辑 ---
    if license_type in CC_LICENSES:
        # 如果是CC协议，强制用常量覆盖核心条款，防止数据污染或不一致
        standard_terms = CC_LICENSES[license_type]
        display_details["reproduce"] = standard_terms["reproduce"]
        display_details["derive"] = standard_terms["derive"]
        display_details["commercial"] = standard_terms["commercial"]
        display_details["url"] = standard_terms["url"]  # 确保URL也是正确的
    else:
        # 如果不是已知的CC协议，则统一视为 'custom'
        license_type = "custom"
        display_details["type"] = "custom"

    # --- 开始构建 Embed ---
    embed = discord.Embed(
        title=f"📜 {author.display_name} 的内容授权协议",
        color=discord.Color.gold()
    )
    embed.set_thumbnail(url=author.display_avatar.url)

    if license_type != "custom":
        embed.add_field(
            name="📄 协议类型 (License Type)",
            value=f"**[{license_type}]({display_details['url']})**",  # 链接到官方协议
            inline=False
        )
        embed.description = f"本内容采用 **{license_type}** 国际许可协议进行许可。点击上方链接查看完整协议。"
    else:
        embed.add_field(
            name="📄 协议类型 (License Type)",
            value="**自定义协议 (Custom License)**",
            inline=False
        )

    # 添加核心条款字段
    embed.add_field(name="🔁 转载", value=display_details.get("reproduce", "未设置"), inline=True)
    embed.add_field(name="🎨 衍生创作", value=display_details.get("derive", "未设置"), inline=True)
    embed.add_field(name="💰 商业用途", value=display_details.get("commercial", "未设置"), inline=True)
    embed.add_field(name="✒️ 署名要求", value=display_details.get("attribution", "未设置"), inline=False)

    notes = display_details.get("notes")
    if notes and notes.strip() and notes != "无":
        embed.add_field(name="📝 附加说明", value=notes, inline=False)

    cmd_name = ACTIVE_COMMAND_CONFIG["group"]["name"]
    embed.set_footer(text=f"该协议由作者设置 | 使用 `/{cmd_name}` 命令管理你的协议")

    return embed


class FirstTimeSetupView(ui.View):
    """
    新用户第一次与机器人交互时看到的欢迎和引导视图。
    主要目的是引导用户完成首次协议创建。
    """

    def __init__(self, cog: 'LicenseCog', db: 'LicenseDB', owner_id: int, thread: discord.Thread):
        super().__init__(timeout=3600)
        self.cog = cog
        self.db = db
        self.owner_id = owner_id
        self.thread = thread

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """确保只有帖子作者可以操作。"""
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ 这不是你的帖子，不能进行操作哦。", ephemeral=True)
            return False
        return True

    @ui.button(label="✨ 创建我的授权协议", style=discord.ButtonStyle.success)
    async def create_license(self, interaction: discord.Interaction, button: ui.Button):
        """
        按钮：引导新用户创建他们的第一个默认协议。
        设计模式：此流程完成后，会将当前的 `FirstTimeSetupView` 替换为标准的 `InitialActionView`，
        使用户的体验与老用户保持一致，无需为新用户编写一套完全独立的后续逻辑。
        """
        config = self.db.get_config(self.owner_id)  # 获取一个默认配置

        # 定义创建完成后的行为：保存数据，然后用标准的主交互面板替换当前欢迎界面
        async def on_create_complete(create_interaction: discord.Interaction, new_details: dict):
            # a. 保存数据
            config.license_details = new_details
            self.db.save_config(config)

            # b. 创建标准的主交互面板视图
            main_view = InitialActionView(self.cog, self.db, config, self.thread)

            # c. 用主交互面板替换当前的欢迎界面
            await create_interaction.response.edit_message(
                content=None,  # 清理掉之前的欢迎文字
                embed=main_view.original_embed,
                view=main_view
            )
            # 此后，交互的控制权交给了 main_view

        # 定义取消创建的行为：返回欢迎界面
        async def on_create_cancel(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(
                embed=interaction.message.embeds[0], view=self
            )

        # 创建并显示编辑枢纽视图
        hub_view = LicenseEditHubView(
            db=self.db, config=config,
            callback=on_create_complete,
            on_cancel=on_create_cancel
        )
        await interaction.response.edit_message(
            content=(
                "太棒了！请创建你的第一份默认协议。\n"
                "这将成为你未来发布作品时的默认设置。\n"
                f"{HUB_VIEW_CONTENT}"
            ),
            embed=None,
            view=hub_view
        )

    @ui.button(label="本次跳过", style=discord.ButtonStyle.secondary)
    async def skip_for_now(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：关闭欢迎面板。"""
        cmd_name = ACTIVE_COMMAND_CONFIG["group"]["name"]
        await interaction.response.edit_message(
            content=f"好的，你随时可以通过 `/{cmd_name}` 命令来设置你的授权协议。",
            embed=None, view=None
        )
        self.stop()

    @ui.button(label="别再打扰我", style=discord.ButtonStyle.danger, row=1)
    async def disable_bot(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：直接禁用机器人。"""
        config = self.db.get_config(self.owner_id)
        config.bot_enabled = False
        self.db.save_config(config)
        cmd_name = ACTIVE_COMMAND_CONFIG["group"]["name"]
        # 注意：原代码中 "setting" 有拼写错误，应为 "settings"
        cmd_name_setting = ACTIVE_COMMAND_CONFIG["settings"]["name"]
        await interaction.response.edit_message(
            content=f"好的，我以后不会再主动打扰你了。\n你可以随时使用 `/{cmd_name} {cmd_name_setting}` 命令重新启用我。",
            embed=None, view=None
        )
        self.stop()


# --- 主 Cog 类 ---
class LicenseCog(commands.Cog):
    """
    授权协议助手的主Cog类。
    负责监听事件、注册斜杠命令，并将所有业务逻辑串联起来。
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = getattr(bot, 'logger', None)  # 优雅地获取注入的logger
        self.name = "授权协议助手"
        self.db = LicenseDB()  # 初始化数据库访问层

        # 从主配置文件 `config.json` 加载要监控的论坛频道ID列表
        config_path = Path('config.json')
        self.monitored_channel_ids = []
        if config_path.exists():
            with config_path.open('r', encoding='utf-8') as f:
                app_config = json.load(f)
                self.monitored_channel_ids = app_config.get('license_cog', {}).get('monitored_channels', [])

    @commands.Cog.listener()
    async def on_ready(self):
        """当Cog加载并准备好时，在日志中打印信息。"""
        if self.logger:
            self.logger.info(f"✅ 授权协议助手(LicenseCog)已加载，监控 {len(self.monitored_channel_ids)} 个论坛频道。")

    # --- 私有辅助方法 ---

    async def _save_and_confirm_callback(self, interaction: discord.Interaction, user_id: int, new_details: dict):
        """
        一个标准化的回调函数，用于处理从UI编辑流程中传来的数据。
        它的职责是：保存数据，并向用户发送操作成功的确认消息。
        """
        config = self.db.get_config(user_id)
        config.license_details = new_details
        self.db.save_config(config)

        try:
            # 使用 followup.send 发送私密确认消息，以避免与原始交互（如Modal提交）冲突
            await interaction.followup.send("✅ 你的默认协议已更新并保存！", ephemeral=True)
            # 尝试清理发起此流程的UI消息（如编辑枢纽面板）
            if not interaction.is_expired():
                await interaction.edit_original_response(content="✅ 操作完成！", view=None, embed=None)
        except discord.NotFound:
            # 如果原始消息已被删除或找不到了，就忽略
            pass
        except Exception as e:
            if self.logger:
                self.logger.warning(f"在_save_and_confirm_callback中发送确认消息时出错: {e}")

    async def _cleanup_previous_helpers(self, thread: discord.Thread):
        """
        清理指定帖子中所有由本机器人发送的、过时的交互面板。
        这在用户请求“重新发送提醒”时非常有用，可以避免界面混乱。
        """
        try:
            # 异步遍历帖子历史消息
            async for message in thread.history(limit=50):
                # 检查消息作者是否是机器人自己
                if message.author.id == self.bot.user.id and message.embeds:
                    embed = message.embeds[0]
                    # 通过Embed页脚中的签名来精确识别，避免误删其他消息
                    if embed.footer and embed.footer.text and HELPER_SIGNATURE in embed.footer.text:
                        await message.delete()
        except discord.HTTPException as e:
            if self.logger:
                self.logger.warning(f"清理助手消息时出错 (HTTPException): {e}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"清理助手消息时发生未知错误: {e}")

    async def _send_helper_message(self, thread: discord.Thread):
        """
        向指定帖子发送核心的交互式助手消息。
        此方法会判断用户是新用户还是老用户，并发送相应的视图 (`FirstTimeSetupView` 或 `InitialActionView`)。
        """
        author_id = thread.owner_id
        # 尝试获取帖子作者的成员对象，如果作者已离开服务器，则不执行任何操作
        author = thread.guild.get_member(author_id)
        if not author:
            if self.logger: self.logger.info(f"无法在服务器 {thread.guild.id} 中找到帖子作者 {author_id}。")
            return

        # 检查用户是否已禁用机器人
        config = self.db.get_config(author_id)
        if not config.bot_enabled:
            return

        user_config_file = self.db._get_user_file(author_id)

        # 判断是新用户还是老用户
        if not user_config_file.exists():
            # 新用户流程：发送欢迎和首次设置视图
            embed = discord.Embed(
                title=f"欢迎, {author.display_name}！我是内容授权助手",
                description=(
                    "我可以帮助你在每次发布作品后，轻松附上你的授权协议，保护你的创作权益。\n\n"
                    "点击下方按钮，开始创建你的第一份默认协议吧！"
                ),
                color=discord.Color.magenta()
            )
            embed.set_footer(text=HELPER_SIGNATURE)
            view = FirstTimeSetupView(db=self.db, cog=self, owner_id=author_id, thread=thread)
            await thread.send(content=author.mention, embed=embed, view=view)
        else:
            # 老用户流程：发送标准的主操作面板
            view = InitialActionView(self, self.db, config, thread)
            # `build_original_embed` 在 view 的 __init__ 中被调用
            await thread.send(content=author.mention, embed=view.original_embed, view=view)

    # --- 事件监听器 ---

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        """
        当在被监控的论坛频道中创建新帖子时触发。
        """
        # 检查1: 是否是受监控的频道
        # 检查2: 发帖人不是机器人自己
        if thread.parent_id not in self.monitored_channel_ids or thread.owner_id == self.bot.user.id:
            return

        # 稍作延迟，避免机器人响应过快显得突兀，或在Discord API事件传播中出现竞争条件
        await asyncio.sleep(2)

        # 调用核心发送逻辑
        await self._send_helper_message(thread)

    # --- 斜杠命令组 ---
    license_group = app_commands.Group(
        name=ACTIVE_COMMAND_CONFIG["group"]["name"],
        description=ACTIVE_COMMAND_CONFIG["group"]["description"]
    )

    @license_group.command(
        name=ACTIVE_COMMAND_CONFIG["remind"]["name"],
        description=ACTIVE_COMMAND_CONFIG["remind"]["description"]
    )
    async def remind_me(self, interaction: discord.Interaction):
        """命令：在当前帖子中重新召唤协议助手面板。"""
        # 检查命令是否在帖子（Thread）中使用
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ 此命令只能在帖子（子区）中使用。", ephemeral=True)
            return

        thread = interaction.channel
        # 检查权限：只有帖子所有者或有管理权限的成员可以执行
        is_owner = (interaction.user.id == thread.owner_id)
        can_manage = interaction.user.guild_permissions.manage_threads
        if not is_owner and not can_manage:
            await interaction.response.send_message("❌ 你不是该帖子的所有者，也没有管理权限。", ephemeral=True)
            return

        await interaction.response.send_message("✅ 好的，正在清理旧提醒并重新发送...", ephemeral=True)

        # 1. 清理旧面板
        await self._cleanup_previous_helpers(thread)
        # 2. 重新发送新面板
        await self._send_helper_message(thread)

    @license_group.command(
        name=ACTIVE_COMMAND_CONFIG["edit"]["name"],
        description=ACTIVE_COMMAND_CONFIG["edit"]["description"]
    )
    async def edit_license(self, interaction: discord.Interaction):
        """命令：打开一个私密的面板来编辑用户的默认授权协议。"""
        config = self.db.get_config(interaction.user.id)

        # 定义编辑完成后的行为：调用标准保存回调
        async def on_edit_complete(edit_interaction: discord.Interaction, new_details: dict):
            await self._save_and_confirm_callback(edit_interaction, interaction.user.id, new_details)

        # 定义取消编辑的行为：清理UI
        async def on_edit_cancel(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(content="操作已取消。", view=None, embed=None)

        # 创建并发送编辑枢纽视图
        hub_view = LicenseEditHubView(
            db=self.db, config=config,
            callback=on_edit_complete,
            on_cancel=on_edit_cancel
        )
        await interaction.response.send_message(
            content=(
                "你正在**永久编辑**你的默认协议。\n"
                "保存后，这将成为你未来的默认设置。\n"
                f"{HUB_VIEW_CONTENT}"
            ),
            view=hub_view,
            ephemeral=True  # 确保此编辑面板只有用户自己可见
        )

    @license_group.command(
        name=ACTIVE_COMMAND_CONFIG["settings"]["name"],
        description=ACTIVE_COMMAND_CONFIG["settings"]["description"]
    )
    async def settings(self, interaction: discord.Interaction):
        """命令：打开一个私密的机器人行为设置面板。"""
        config = self.db.get_config(interaction.user.id)
        view = SettingsView(self.db, config, self)

        embed = discord.Embed(
            title="⚙️ 机器人设置",
            description="在这里管理授权助手的所有行为。\n完成后，点击下方的“关闭面板”即可。",
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @license_group.command(
        name=ACTIVE_COMMAND_CONFIG["show"]["name"],
        description=ACTIVE_COMMAND_CONFIG["show"]["description"]
    )
    async def show_license(self, interaction: discord.Interaction):
        """命令：以私密消息的方式显示用户当前的默认协议。"""
        config = self.db.get_config(interaction.user.id)
        embed = build_license_embed(config, interaction.user)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    """标准的Cog加载入口点。"""
    await bot.add_cog(LicenseCog(bot))