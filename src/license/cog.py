import asyncio
import json
from pathlib import Path

import discord
from discord import app_commands, ui
from discord.ext import commands

from src.utils.confirm_view import confirm_view_embed

# --- 放在文件的靠前位置，比如在数据模型类之前 ---
HELPER_SIGNATURE = "授权协议助手"
# 定义通用的CC协议，方便引用
# 格式：{ "显示名称": {"转载": "...", "演绎": "...", "商业": "..."} }
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
    """返回一份标准的、全新的默认授权协议详情字典"""
    return {
        "type": "custom",
        "reproduce": "询问作者",
        "derive": "询问作者",
        "commercial": "禁止",
        "attribution": f"<@{user_id}>",
        "notes": "无"
    }


# ============================================
#            命令与本地化配置
# ============================================
COMMAND_CONFIG = {
    "group": {
        "name": "license",
        "description": "管理你的内容授权协议"
    },
    "remind": {
        "name": "remind",
        "description": "在当前帖子中重新发送授权助手提醒"
    },
    "edit": {
        "name": "edit",
        "description": "创建或修改你的默认授权协议"
    },
    "settings": {
        "name": "settings",
        "description": "配置授权助手机器人的行为"
    },
    "show": {
        "name": "show",
        "description": "查看你当前的默认授权协议"
    }
}

# 如果你想完全使用中文，可以这样配置：
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

# 在代码中，我们选择一套配置来使用
# 为了演示，我们使用中文版
ACTIVE_COMMAND_CONFIG = COMMAND_CONFIG_ZH


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
    """处理用户授权配置的读写（V2版：带内存缓存）"""

    def __init__(self):
        self.data_path = Path("data/licenses")
        self.data_path.mkdir(parents=True, exist_ok=True)
        # --- 引入缓存 ---
        self._cache: dict[int, LicenseConfig] = {}

    def _get_user_file(self, user_id: int) -> Path:
        return self.data_path / f"{user_id}.json"

    def get_config(self, user_id: int) -> LicenseConfig:
        """
        获取用户的配置。优先从缓存读取，否则从文件加载。
        这是获取用户配置的唯一入口。
        """
        # 1. 查缓存
        if user_id in self._cache:
            return self._cache[user_id]

        # 2. 缓存未命中，从文件加载
        user_file = self._get_user_file(user_id)
        if not user_file.exists():
            # 文件不存在，创建新的默认配置
            config = LicenseConfig(user_id)
        else:
            try:
                with user_file.open('r', encoding='utf-8') as f:
                    data = json.load(f)
                config = LicenseConfig(user_id, data)
            except (json.JSONDecodeError, IOError):
                # 文件损坏，使用默认配置
                config = LicenseConfig(user_id)

        # 3. 存入缓存
        self._cache[user_id] = config
        return config

    def save_config(self, config: LicenseConfig):
        """
        保存用户的配置到文件，并更新缓存。
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

        # --- 关键：同时更新缓存 ---
        self._cache[config.user_id] = config

    def delete_config(self, user_id: int):
        """
        删除用户的配置文件和缓存。
        """
        # 1. 删除文件
        user_file = self._get_user_file(user_id)
        if user_file.exists():
            try:
                user_file.unlink()
            except OSError as e:
                # 可以选择在这里打日志或抛出异常
                print(f"Error deleting file {user_file}: {e}")
                # 即使文件删除失败，我们依然尝试清理缓存

        # 2. --- 关键：从缓存中移除 ---
        if user_id in self._cache:
            del self._cache[user_id]


# --- 交互界面 (Modals & Views) ---

class LicenseEditHubView(ui.View):
    """枢纽视图（V3版：完全融入主界面替换模型）"""

    def __init__(self, db: LicenseDB, config: LicenseConfig, callback: callable, on_cancel: callable):
        super().__init__(timeout=300)
        self.db = db
        self.config = config
        self.callback = callback  # 顶层回调，接收 (interaction, new_details)
        self.on_cancel = on_cancel  # 顶层“取消”回调，接收 (interaction)

    @ui.button(label="📝 使用自定义文本填写", style=discord.ButtonStyle.primary, row=0)
    async def set_with_custom(self, interaction: discord.Interaction, button: ui.Button):
        # 弹出不响应的 Modal，并将顶层回调传给它
        modal = CustomLicenseEditModal(self.db, self.config, callback=self.callback)
        await interaction.response.send_modal(modal)

    @ui.button(label="📜 从CC协议模板中选择", style=discord.ButtonStyle.secondary, row=0)
    async def set_with_cc(self, interaction: discord.Interaction, button: ui.Button):
        # 准备下一个不响应的视图
        cc_view = CCLicenseSelectView(self.db, self.config, callback=self.callback)
        # 关键：用 cc_view 替换当前枢纽视图，这是对按钮点击的响应
        cc_select_content = (
            "你正在选择一个标准的CC协议模板。\n\n"
            "- 你选择的协议将**覆盖**你当前的授权设置。\n"
            "- 你可以修改后续弹出的“署名要求”和“附加说明”，但这些不会改变CC协议的核心条款。\n"
            "- 如果你想在CC协议的基础上做更多修改，请返回并选择“创建或编辑自定义协议”，然后手动输入你的条款。"
        )
        await interaction.response.edit_message(
            content=cc_select_content,
            view=cc_view
        )

    @ui.button(label="取消", style=discord.ButtonStyle.danger, row=1)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        # 直接调用顶层的“取消”回调
        await self.on_cancel(interaction)


class AttributionNotesModal(ui.Modal, title="填写署名与备注"):
    """一个只询问署名和备注的简单Modal"""

    def __init__(self, default_attribution: str, default_notes: str, final_callback: callable):
        super().__init__()
        self.attribution = ui.TextInput(label="署名要求", default=default_attribution)
        self.notes = ui.TextInput(label="附加说明 (可选)", default=default_notes if default_notes != "无" else "", required=False,
                                  style=discord.TextStyle.paragraph)
        self.add_item(self.attribution)
        self.add_item(self.notes)
        self.submitted = False
        self.final_callback = final_callback  # 直接接收最终的回调

    async def on_submit(self, interaction: discord.Interaction):
        # 直接调用回调，把新鲜的 interaction 传出去
        await self.final_callback(interaction, self.attribution.value, self.notes.value or "无")


class CustomLicenseEditModal(ui.Modal, title="编辑自定义授权协议"):
    """一个只包含5个文本输入框的、合规的Modal"""

    def __init__(self, db: LicenseDB, current_config: LicenseConfig, callback: callable):
        super().__init__()
        self.db = db
        self.config = current_config
        self.callback = callback  # 存储回调

        details = current_config.license_details
        self.reproduce = ui.TextInput(label="是否允许转载？", default=details.get("reproduce"), max_length=100)
        self.derive = ui.TextInput(label="是否允许演绎？", default=details.get("derive"), max_length=100)
        self.commercial = ui.TextInput(label="是否允许商业性使用？", default=details.get("commercial"), max_length=100)
        self.attribution = ui.TextInput(label="署名要求", default=details.get("attribution", f"<@{self.config.user_id}>"), max_length=100)
        self.notes = ui.TextInput(label="附加说明 (可选)", default=details.get("notes", "无") if details.get("notes", "无") != "无" else "", required=False,
                                  style=discord.TextStyle.paragraph)

        self.add_item(self.reproduce)
        self.add_item(self.derive)
        self.add_item(self.commercial)
        self.add_item(self.attribution)
        self.add_item(self.notes)

    async def on_submit(self, interaction: discord.Interaction):
        # 1. 构建数据
        new_details = {
            "type": "custom",  # 明确这是自定义协议
            "reproduce": self.reproduce.value,
            "derive": self.derive.value,
            "commercial": self.commercial.value,
            "attribution": self.attribution.value,
            "notes": self.notes.value or "无"
        }

        # 2. 直接调用回调，把新鲜的 interaction 传出去
        await self.callback(interaction, new_details)


class CCLicenseSelectView(ui.View):
    """让用户选择CC协议的视图"""

    def __init__(self, db: LicenseDB, config: LicenseConfig, callback: callable):
        super().__init__(timeout=300)
        self.db = db
        self.config = config
        self.callback = callback  # 存储回调

        options = [discord.SelectOption(label=name, value=name) for name in CC_LICENSES.keys()]
        self.add_item(ui.Select(placeholder="选择一个CC协议...", options=options, custom_id="cc_select"))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # 当下拉菜单被选择时，interaction.data['custom_id'] 会是 'cc_select'
        if interaction.data.get("custom_id") == "cc_select":
            await self.handle_selection(interaction)
        return True  # 允许交互

    async def handle_selection(self, interaction: discord.Interaction):
        selected_cc = interaction.data["values"][0]
        cc_data = CC_LICENSES[selected_cc]

        # 定义一个“中介”回调函数，它负责组合数据
        async def modal_submit_callback(modal_interaction, attribution, notes):
            final_details = {
                "type": selected_cc,
                "reproduce": cc_data["reproduce"],
                "derive": cc_data["derive"],
                "commercial": cc_data["commercial"],
                "attribution": attribution,
                "notes": notes or "无"
            }
            # 调用最上层的回调
            await self.callback(modal_interaction, final_details)

        # 弹出 Modal，并把我们的“中介”回调传给它
        modal = AttributionNotesModal(
            default_attribution=self.config.license_details.get("attribution", f"<@{interaction.user.id}>"),
            default_notes=self.config.license_details.get("notes", "无"),
            final_callback=modal_submit_callback
        )
        await interaction.response.send_modal(modal)


class ConfirmPostView(ui.View):
    """一个简单的、只用于在主界面进行确认的视图"""

    def __init__(self, author_id: int, on_confirm: callable, on_cancel: callable):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 这不是你的确认按钮哦。", ephemeral=True)
            return False
        return True

    @ui.button(label="✅ 确认发布", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        await self.on_confirm(interaction)

    @ui.button(label="❌ 返回", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        await self.on_cancel(interaction)


class CustomConfirmView(ui.View):
    """一个为特定流程定制的，简单的确认视图。"""

    def __init__(self, author: discord.User, timeout: int = 120):
        super().__init__(timeout=timeout)
        self.author = author
        self.value: bool | None = None  # 用来存储用户的选择

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("❌ 这不是给你的按钮哦～", ephemeral=True)
            return False
        return True

    @ui.button(label="确认", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: ui.Button):
        self.value = True
        # 响应本次点击，但不做任何多余操作，只是为了让 Discord 知道我们收到了
        await interaction.response.defer()
        self.stop()  # 停止视图，让 await self.wait() 继续执行

    @ui.button(label="取消", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: ui.Button):
        self.value = False
        await interaction.response.defer()
        self.stop()


class InitialActionView(ui.View):
    def __init__(self, cog: 'LicenseCog', db: LicenseDB, config: LicenseConfig, thread: discord.Thread):
        super().__init__(timeout=3600)
        self.cog = cog
        self.db = db
        self.config = config
        self.thread = thread
        self.owner_id = thread.owner_id
        # 保存原始embed，以便随时可以“返回”
        self.original_embed = self.build_original_embed()

    def build_original_embed(self) -> discord.Embed:
        """构建主界面的Embed"""
        embed = discord.Embed(
            title=f"👋 你好, {self.cog.bot.get_user(self.owner_id).display_name}！",
            description="我注意到你发布了一个新作品。你希望如何处理内容的授权协议呢？",
            color=discord.Color.blue()
        )
        cmd_name = ACTIVE_COMMAND_CONFIG["group"]["name"]
        cmd_name_remind = ACTIVE_COMMAND_CONFIG["remind"]["name"]
        embed.set_footer(text=f"{HELPER_SIGNATURE} | 如果按钮失效，请使用 `/{cmd_name} {cmd_name_remind}`")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ 这不是你的帖子，不能进行操作哦。", ephemeral=True)
            return False
        return True

    async def _save_and_confirm_callback(self, interaction: discord.Interaction, new_details: dict):
        """回调：用于永久保存协议"""
        self.config.license_details = new_details
        self.db.save_config(self.config)
        # 使用 followup 发送确认消息，因为原始交互可能已经被 modal/view 使用了
        await interaction.followup.send("✅ 你的默认协议已永久更新！", ephemeral=True)
        # 如果这是在主助手消息上操作的，可以考虑刷新它，但对于私密消息来说，这就足够了。

    # --- 核心：统一的确认流程 ---
    async def show_confirmation_view(self, interaction: discord.Interaction, config_to_show: LicenseConfig):
        """
        在主界面上显示预览和确认按钮。
        :param interaction: 触发此流程的交互。
        :param config_to_show: 要预览和发布的配置。
        """
        final_embed = build_license_embed(config_to_show, interaction.user)
        preview_embed = final_embed.copy()
        preview_embed.title = f"🔍 预览：{preview_embed.title}"
        preview_embed.description = "**请预览你将要发布的协议。**\n确认后将发布到帖子中，并关闭此面板。"

        # 定义确认和取消的行为
        async def do_post(post_interaction: discord.Interaction):
            await self.thread.send(embed=final_embed)
            await post_interaction.response.edit_message(
                content="✅ 协议已发布。", embed=None, view=None
            )
            self.stop()

        async def do_cancel(cancel_interaction: discord.Interaction):
            # 取消就直接调用返回主菜单的方法
            await self.back_to_main_menu(cancel_interaction)

        # 创建并显示确认视图
        confirm_view = ConfirmPostView(
            author_id=interaction.user.id,
            on_confirm=do_post,
            on_cancel=do_cancel
        )

        # 因为我们保证了传入的 interaction 总是“新鲜的”，所以可以直接响应
        await interaction.response.edit_message(embed=preview_embed, view=confirm_view)

    # --- “返回主菜单”的逻辑 ---
    async def back_to_main_menu(self, interaction: discord.Interaction):
        """
        一个可复用的方法，用于将UI完全恢复到初始状态。
        """
        # 确保 self.original_embed 是最新的
        if not self.original_embed:
            self.original_embed = self.build_original_embed()

        # --- 核心修改：明确地将 content 设为 None ---
        await interaction.response.edit_message(
            content=None,  # <-- 关键！清除掉所有可能存在的上层文本。
            embed=self.original_embed,
            view=self
        )

    # --- 发布默认协议 ---
    @ui.button(label="发布默认协议", style=discord.ButtonStyle.success, row=0)
    async def post_default(self, interaction: discord.Interaction, button: ui.Button):
        await self.show_confirmation_view(interaction, self.config)

    # --- 编辑并发布(仅本次) ---
    # --- “一次性发布”按钮 ---
    @ui.button(label="编辑并发布(仅本次)", style=discord.ButtonStyle.primary, row=0)
    async def edit_and_post_once(self, interaction: discord.Interaction, button: ui.Button):
        # 定义编辑完成后的行为：进入确认流程
        async def on_edit_complete(edit_interaction: discord.Interaction, temp_details: dict):
            temp_config = LicenseConfig(self.owner_id)
            temp_config.license_details = temp_details
            await self.show_confirmation_view(edit_interaction, temp_config)

        # 定义取消编辑的行为：返回主菜单
        async def on_edit_cancel(cancel_interaction: discord.Interaction):
            await self.back_to_main_menu(cancel_interaction)

        # 创建枢纽视图，把行为传进去
        hub_view = LicenseEditHubView(
            db=self.db, config=self.config,
            callback=on_edit_complete,
            on_cancel=on_edit_cancel
        )

        # 用枢纽视图替换主菜单视图
        await interaction.response.edit_message(
            content=(
                "你正在为你**本次发布**编辑一个临时协议。\n"
                "这个操作**不会**更改你保存的默认协议。\n"
                f"{HUB_VIEW_CONTENT}"
            ),
            embed=None,  # 清理掉主菜单的embed
            view=hub_view
        )

    # --- “永久编辑”按钮 ---
    @ui.button(label="永久编辑默认协议", style=discord.ButtonStyle.secondary, row=1)
    async def edit_default_license(self, interaction: discord.Interaction, button: ui.Button):
        # 定义编辑完成后的行为：保存并返回主菜单
        async def on_edit_complete(edit_interaction: discord.Interaction, new_details: dict):
            # 1. 保存数据
            self.config.license_details = new_details
            self.db.save_config(self.config)

            # 2. 更新主菜单的Embed以反映变化 (可选但推荐)
            self.original_embed = self.build_original_embed()  # 也许这里可以加个“已保存”的提示

            # 3. 返回主菜单，并给一个私密确认消息
            await self.back_to_main_menu(edit_interaction)
            await edit_interaction.followup.send("✅ 你的默认协议已永久保存！", ephemeral=True)

        # 定义取消编辑的行为：返回主菜单
        async def on_edit_cancel(cancel_interaction: discord.Interaction):
            await self.back_to_main_menu(cancel_interaction)

        # 创建枢纽视图
        hub_view = LicenseEditHubView(
            db=self.db, config=self.config,
            callback=on_edit_complete,
            on_cancel=on_edit_cancel
        )

        # 用枢纽视图替换主菜单视图
        await interaction.response.edit_message(
            content=(
                "你正在**永久编辑**你的默认协议。\n"
                "保存后，这将成为你未来的默认设置。\n"
                f"{HUB_VIEW_CONTENT}"
            ),
            embed=None,
            view=hub_view
        )

    # --- 新增的按钮 ---
    @ui.button(label="预览协议", style=discord.ButtonStyle.primary, row=0)
    async def preview_license(self, interaction: discord.Interaction, button: ui.Button):
        """发送一条临时的私密消息来展示当前的默认协议，同时保持原按钮可用。"""

        # 1. 确认交互，以便我们稍后可以发送 "followup" 消息。
        #    这一步只是为了防止交互超时，并不决定消息的隐私性。
        await interaction.response.defer(thinking=False)

        # 2. 构建协议 Embed
        embed = build_license_embed(self.config, interaction.user)
        embed.title = "👀 你的当前默认协议预览"

        # 3. 使用 followup.send 发送真正的私密消息。
        #    在这里明确指定 ephemeral=True 是最关键、最可靠的一步。
        await interaction.followup.send(embed=embed, ephemeral=True)

    @ui.button(label="机器人设置", style=discord.ButtonStyle.secondary, row=1)
    async def settings(self, interaction: discord.Interaction, button: ui.Button):
        # 这里的逻辑和斜杠命令完全一样
        config = self.db.get_config(interaction.user.id)
        view = SettingsView(self.db, config, self.cog)  # 传入 self.cog

        embed = discord.Embed(
            title="⚙️ 机器人设置",
            description="在这里管理授权助手的所有行为。\n完成后，点击下方的“关闭面板”即可。",
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @ui.button(label="本次跳过", style=discord.ButtonStyle.secondary, row=1)
    async def skip_for_now(self, interaction: discord.Interaction, button: ui.Button):
        cmd_name = ACTIVE_COMMAND_CONFIG["group"]["name"]
        await interaction.response.edit_message(content=f"好的，你随时可以通过 `/{cmd_name}` 命令来设置你的授权协议。", view=None)
        self.stop()

    @ui.button(label="别再打扰我", style=discord.ButtonStyle.danger, row=1)
    async def disable_bot(self, interaction: discord.Interaction, button: ui.Button):
        """禁用机器人功能"""
        config = self.db.get_config(self.owner_id)
        config.bot_enabled = False
        self.db.save_config(config)
        cmd_name = ACTIVE_COMMAND_CONFIG["group"]["name"]
        cmd_name_setting = ACTIVE_COMMAND_CONFIG["settings"]["name"]
        await interaction.response.edit_message(
            content=f"好的，我以后不会再主动打扰你了。\n你可以随时使用 `/{cmd_name} {cmd_name_setting}` 命令重新启用我。",
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
    """机器人行为设置视图（V3版：优雅的独立面板）"""

    def __init__(self, db: 'LicenseDB', config: 'LicenseConfig', cog: 'LicenseCog'):
        super().__init__(timeout=600)  # 延长超时时间
        self.db = db
        self.config = config
        self.cog = cog  # 需要 cog 来调用保存回调
        self.update_button_labels()

    def update_button_labels(self):
        """根据当前配置更新按钮标签"""
        self.toggle_auto_post_button.label = f"自动发布: {'✅' if self.config.auto_post else '❌'}"
        self.toggle_bot_enabled_button.label = f"机器人总开关: {'✅' if self.config.bot_enabled else '❌'}"
        self.toggle_confirmation_button.label = f"发布前二次确认: {'✅' if self.config.require_confirmation else '❌'}"

    # --- 开关按钮的逻辑：原地刷新 ---
    @ui.button(label="切换自动发布", style=discord.ButtonStyle.primary, row=0)
    async def toggle_auto_post_button(self, interaction: discord.Interaction, button: ui.Button):
        self.config.auto_post = not self.config.auto_post
        self.db.save_config(self.config)
        self.update_button_labels()
        # 响应交互，并用更新后的自己重新渲染视图
        await interaction.response.edit_message(view=self)

    @ui.button(label="切换机器人总开关", style=discord.ButtonStyle.primary, row=0)
    async def toggle_bot_enabled_button(self, interaction: discord.Interaction, button: ui.Button):
        self.config.bot_enabled = not self.config.bot_enabled
        self.db.save_config(self.config)
        self.update_button_labels()
        await interaction.response.edit_message(view=self)

    @ui.button(label="切换发布前二次确认", style=discord.ButtonStyle.primary, row=1)
    async def toggle_confirmation_button(self, interaction: discord.Interaction, button: ui.Button):
        self.config.require_confirmation = not self.config.require_confirmation
        self.db.save_config(self.config)
        self.update_button_labels()
        await interaction.response.edit_message(view=self)

    # --- 危险操作的逻辑：发起独立的确认流程 ---
    @ui.button(label="重置我的协议", style=discord.ButtonStyle.danger, row=2)
    async def reset_license(self, interaction: discord.Interaction, button: ui.Button):
        async def on_confirm(confirm_interaction: discord.Interaction):
            # 确认后，执行重置操作
            self.config.license_details = get_default_license_details(self.config.user_id)
            self.db.save_config(self.config)
            await confirm_interaction.response.edit_message(content="✅ 你的授权协议已重置为默认值。", view=None)

        async def on_cancel(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(content="🚫 操作已取消。", view=None)

        # 发起一个独立的、临时的确认流程
        confirm_view = ConfirmPostView(interaction.user.id, on_confirm, on_cancel)
        await interaction.response.send_message(
            "**⚠️ 警告：** 此操作会将你的默认协议恢复为初始设置！\n请确认你的操作：",
            view=confirm_view,
            ephemeral=True
        )

    @ui.button(label="删除所有数据", style=discord.ButtonStyle.danger, row=2)
    async def delete_data(self, interaction: discord.Interaction, button: ui.Button):
        """发起一个独立的、用于确认删除所有用户数据的流程"""

        # 1. 定义确认后的操作
        async def on_confirm(confirm_interaction: discord.Interaction):
            # a. 执行真正的删除操作
            try:
                self.db.delete_config(self.config.user_id)
            except OSError as e:
                # 如果删除失败，给出错误提示
                if self.cog.logger:
                    self.cog.logger.error(f"删除用户数据文件失败: {self.config.user_id}, 错误: {e}")
                await confirm_interaction.response.edit_message(
                    content=f"❌ 删除数据时发生错误！请联系管理员。错误详情: `{e}`",
                    view=None
                )
                return

            # b. 成功后，更新确认消息
            await confirm_interaction.response.edit_message(
                content="🗑️ **你的所有数据已被永久删除。**\n下次你发布作品时，我将会像初次见面一样与你打招呼。",
                view=None
            )

            # c. 既然数据都没了，设置面板也应该关闭
            #    我们尝试删除原始的设置面板消息
            try:
                # interaction 是 SettingsView 的交互，不是 confirm_interaction
                await interaction.delete_original_response()
            except discord.NotFound:
                pass  # 如果找不到了就算了

            # d. 停止当前 SettingsView 的生命周期
            self.stop()

        # 2. 定义取消后的操作
        async def on_cancel(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(content="🚫 操作已取消，你的数据安然无恙。", view=None)

        # 3. 创建并发送独立的确认视图
        #    我们使用之前创建的 ConfirmPostView，因为它正好符合我们的需求
        confirm_view = ConfirmPostView(interaction.user.id, on_confirm, on_cancel)

        # 这里的警告信息必须非常强烈
        await interaction.response.send_message(
            "**🚨 终极警告：此操作不可逆！🚨**\n\n"
            "你确定要**永久删除**你保存在本机器人中的所有数据吗？这包括：\n"
            "- 你保存的默认授权协议\n"
            "- 所有的机器人行为设置\n\n"
            "**此操作无法撤销！请再次确认！**",
            view=confirm_view,
            ephemeral=True
        )

    # --- 新增的关闭按钮 ---
    @ui.button(label="关闭面板", style=discord.ButtonStyle.secondary, row=3)
    async def close_panel(self, interaction: discord.Interaction, button: ui.Button):
        """直接删除这个设置面板消息"""
        await interaction.response.defer()  # 先响应，防止超时
        await interaction.delete_original_response()
        self.stop()


# --- 辅助函数 ---
def build_license_embed(config: LicenseConfig, author: discord.User) -> discord.Embed:
    """根据配置构建授权协议的Embed（V4版：读取时强制覆盖CC协议）"""

    # 从数据库获取原始的、用户保存的详情
    saved_details = config.license_details
    license_type = saved_details.get("type", "custom")

    # 创建一个用于展示的字典副本，这是我们将要操作的对象
    display_details = saved_details.copy()

    # --- 核心安全阀逻辑 ---
    if license_type in CC_LICENSES:
        # 检测到是CC协议，强制用常量覆盖核心条款
        standard_terms = CC_LICENSES[license_type]
        display_details["reproduce"] = standard_terms["reproduce"]
        display_details["derive"] = standard_terms["derive"]
        display_details["commercial"] = standard_terms["commercial"]
        display_details["url"] = standard_terms["url"]  # 确保URL也是正确的
    else:
        # 如果不是CC协议，确保类型被正确标记为 'custom' 以免混淆
        license_type = "custom"
        display_details["type"] = "custom"

    # --- 后续的Embed构建代码与之前版本完全一致 ---

    embed = discord.Embed(
        title=f"📜 {author.display_name} 的内容授权协议",
        color=discord.Color.gold()
    )
    embed.set_thumbnail(url=author.display_avatar.url)

    if license_type != "custom":  # 这里使用净化后的 license_type
        embed.add_field(
            name="📄 协议类型 (License Type)",
            value=f"**[{license_type}]({display_details['url']})**",
            inline=False
        )
        embed.description = f"本内容采用 **{license_type}** 国际许可协议进行许可。点击上方链接查看完整协议。"
    else:
        # 为自定义协议也添加一个类型字段
        embed.add_field(
            name="📄 协议类型 (License Type)",
            value="**自定义协议 (Custom License)**",
            inline=False
        )

    embed.add_field(name="🔁 转载", value=display_details.get("reproduce", "未设置"), inline=True)
    embed.add_field(name="🎨 衍生创作", value=display_details.get("derive", "未设置"), inline=True)
    embed.add_field(name="💰 商业用途", value=display_details.get("commercial", "未设置"), inline=True)
    embed.add_field(name="✒️ 署名要求", value=display_details.get("attribution", "未设置"), inline=False)

    notes = display_details.get("notes")
    if notes and notes != "无":
        embed.add_field(name="📝 附加说明", value=notes, inline=False)
    cmd_name = ACTIVE_COMMAND_CONFIG["group"]["name"]
    embed.set_footer(text=f"该协议由作者设置 | 使用 `/{cmd_name}` 命令管理你的协议")

    return embed


class FirstTimeSetupView(ui.View):
    """引导新用户首次创建协议的视图（V3版：融入统一架构）"""

    def __init__(self, cog: 'LicenseCog', db: 'LicenseDB', owner_id: int, thread: discord.Thread):
        super().__init__(timeout=3600)
        self.cog = cog
        self.db = db
        self.owner_id = owner_id
        self.thread = thread

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ 这不是你的帖子，不能进行操作哦。", ephemeral=True)
            return False
        return True

    # --- “创建协议”按钮 ---
    @ui.button(label="✨ 创建我的授权协议", style=discord.ButtonStyle.success)
    async def create_license(self, interaction: discord.Interaction, button: ui.Button):
        config = self.db.get_config(self.owner_id)

        # 1. 定义创建完成后的行为：保存数据，然后用标准的 InitialActionView 替换当前界面
        async def on_create_complete(create_interaction: discord.Interaction, new_details: dict):
            # a. 保存数据
            config.license_details = new_details
            self.db.save_config(config)

            # b. 创建标准的 InitialActionView
            main_view = InitialActionView(self.cog, self.db, config, self.thread)

            # c. 用主界面替换当前的“欢迎”界面
            await create_interaction.response.edit_message(
                content=None,  # 清理掉之前的文字
                embed=main_view.original_embed,
                view=main_view
            )
            # 在这里，FirstTimeSetupView 的使命结束，main_view 接管

        # 2. 定义取消创建的行为：什么都不做，让用户留在“欢迎”界面
        async def on_create_cancel(cancel_interaction: discord.Interaction):
            # 用欢迎界面替换掉枢纽视图界面
            await cancel_interaction.response.edit_message(
                embed=interaction.message.embeds[0], view=self
            )

        # 3. 创建枢纽视图
        hub_view = LicenseEditHubView(
            db=self.db, config=config,
            callback=on_create_complete,
            on_cancel=on_create_cancel
        )

        # 4. 用枢纽视图替换当前的“欢迎”界面
        await interaction.response.edit_message(
            content=(
                "太棒了！请创建你的第一份默认协议。\n"
                "这将成为你未来发布作品时的默认设置。\n"
                f"{HUB_VIEW_CONTENT}"
            ),
            embed=None,
            view=hub_view
        )

    # --- 其他按钮的逻辑现在也变得清晰 ---
    @ui.button(label="本次跳过", style=discord.ButtonStyle.secondary)
    async def skip_for_now(self, interaction: discord.Interaction, button: ui.Button):
        cmd_name = ACTIVE_COMMAND_CONFIG["group"]["name"]
        await interaction.response.edit_message(
            content=f"好的，你随时可以通过 `/{cmd_name}` 命令来设置你的授权协议。",
            embed=None, view=None
        )
        self.stop()

    @ui.button(label="别再打扰我", style=discord.ButtonStyle.danger, row=1)
    async def disable_bot(self, interaction: discord.Interaction, button: ui.Button):
        config = self.db.get_config(self.owner_id)
        config.bot_enabled = False
        self.db.save_config(config)
        cmd_name = ACTIVE_COMMAND_CONFIG["group"]["name"]
        cmd_name_setting = ACTIVE_COMMAND_CONFIG["setting"]["name"]
        await interaction.response.edit_message(
            content=f"好的，我以后不会再主动打扰你了。\n你可以随时使用 `/{cmd_name} {cmd_name_setting}` 命令重新启用我。",
            embed=None, view=None
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

    async def _save_and_confirm_callback(self, interaction: discord.Interaction, user_id: int, new_details: dict):
        """
        标准回调 V4版：简单保存，将所有校验逻辑交给 build_license_embed。
        """
        config = self.db.get_config(user_id)

        # 直接保存前端构建好的完整协议详情
        config.license_details = new_details
        self.db.save_config(config)

        # 后续的确认消息逻辑保持不变
        try:
            # 使用 followup.send 以避免交互冲突
            await interaction.followup.send("✅ 你的默认协议已更新并保存！", ephemeral=True)
            # 尝试清理原始消息
            if not interaction.is_expired():
                # 这里编辑的是枢纽视图的消息
                await interaction.edit_original_response(content="✅ 操作完成！", view=None)
        except discord.NotFound:
            pass
        except Exception as e:
            if self.logger:
                self.logger.warning(f"在回调中发送确认消息时出错: {e}")

    # --- 新增：私有的清理辅助方法 ---
    async def _cleanup_previous_helpers(self, thread: discord.Thread):
        """清理指定帖子中所有由本助手发送的交互消息"""
        try:
            async for message in thread.history(limit=50):
                if message.author.id == self.bot.user.id and message.embeds:
                    embed = message.embeds[0]
                    # 通过Embed的标题或页脚中的签名来识别
                    if embed.footer.text and HELPER_SIGNATURE in embed.footer.text:
                        await message.delete()
        except discord.HTTPException as e:
            if self.logger:
                self.logger.warning(f"清理助手消息时出错: {e}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"清理助手消息时发生未知错误: {e}")

    # --- 新增：私有的发送辅助方法 ---
    async def _send_helper_message(self, thread: discord.Thread):
        """发送带有交互按钮的助手消息"""
        author_id = thread.owner_id
        author = thread.guild.get_member(author_id)
        if not author: return

        user_config_file = self.db._get_user_file(author_id)
        cmd_name = ACTIVE_COMMAND_CONFIG["group"]["name"]
        cmd_name_remind = ACTIVE_COMMAND_CONFIG["remind"]["name"]
        footer_text = f"{HELPER_SIGNATURE} | 如果按钮失效，请使用 `/{cmd_name} {cmd_name_remind}`"

        if not user_config_file.exists():
            # 新用户流程
            embed = discord.Embed(
                title=f"欢迎, {author.display_name}！我是内容授权助手",
                description=(
                    "我可以帮助你在每次发布作品后，轻松附上你的授权协议，保护你的创作权益。\n\n"
                    "点击下方按钮，开始创建你的第一份默认协议吧！"
                ),
                color=discord.Color.magenta()
            )
            embed.set_footer(text=footer_text)
            view = FirstTimeSetupView(db=self.db, cog=self, owner_id=author_id, thread=thread)
            await thread.send(content=author.mention, embed=embed, view=view)
        else:
            # 老用户流程
            config = self.db.get_config(author_id)
            if not config.bot_enabled: return

            embed = discord.Embed(
                title=f"👋 你好, {author.display_name}！",
                description="我注意到你发布了一个新作品。你希望如何处理内容的授权协议呢？",
                color=discord.Color.blue()
            )
            embed.set_footer(text=footer_text)
            view = InitialActionView(self, self.db, config, thread)
            await thread.send(content=f"{author.mention}", embed=embed, view=view)

        # --- 重构：事件和命令现在只调用辅助方法 ---

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        """当有新帖子创建时触发"""
        if thread.parent_id not in self.monitored_channel_ids or thread.owner_id == self.bot.user.id:
            return
        await asyncio.sleep(2)
        # 直接调用发送逻辑
        await self._send_helper_message(thread)

    # --- 斜杠命令 ---
    license_group = app_commands.Group(
        name=ACTIVE_COMMAND_CONFIG["group"]["name"],
        description=ACTIVE_COMMAND_CONFIG["group"]["description"]
    )

    @license_group.command(
        name=ACTIVE_COMMAND_CONFIG["remind"]["name"],
        description=ACTIVE_COMMAND_CONFIG["remind"]["description"]
    )
    async def remind_me(self, interaction: discord.Interaction):
        """重新召唤协议助手。"""
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ 此命令只能在帖子（子区）中使用。", ephemeral=True)
            return

        thread = interaction.channel
        is_owner = (interaction.user.id == thread.owner_id)
        can_manage = interaction.user.guild_permissions.manage_threads
        if not is_owner and not can_manage:
            await interaction.response.send_message("❌ 你不是该帖子的所有者，也没有管理权限。", ephemeral=True)
            return

        await interaction.response.send_message("✅ 好的，正在清理旧提醒并重新发送...", ephemeral=True)

        # 1. 清理
        await self._cleanup_previous_helpers(thread)

        # 2. 重新发送
        await self._send_helper_message(thread)

    @license_group.command(
        name=ACTIVE_COMMAND_CONFIG["edit"]["name"],
        description=ACTIVE_COMMAND_CONFIG["edit"]["description"]
    )
    async def edit_license(self, interaction: discord.Interaction):
        """打开授权协议编辑中心。"""
        config = self.db.get_config(interaction.user.id)

        # 1. 定义编辑完成后的行为：只保存并发送一个确认消息
        async def on_edit_complete(edit_interaction: discord.Interaction, new_details: dict):
            # 调用标准的保存回调
            await self._save_and_confirm_callback(edit_interaction, interaction.user.id, new_details)

        # 2. 定义取消编辑的行为：只清理UI
        async def on_edit_cancel(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(content="操作已取消。", view=None)

        # 创建枢纽视图...
        hub_view = LicenseEditHubView(
            db=self.db, config=config,
            callback=on_edit_complete,
            on_cancel=on_edit_cancel
        )

        # --- 核心修改：明确使用 send_message ---
        await interaction.response.send_message(
            content=(
                "你正在**永久编辑**你的默认协议。\n"
                "保存后，这将成为你未来的默认设置。\n"
                f"{HUB_VIEW_CONTENT}"
            ),
            view=hub_view,
            ephemeral=True  # 确保是私密消息
        )

    @license_group.command(
        name=ACTIVE_COMMAND_CONFIG["settings"]["name"],
        description=ACTIVE_COMMAND_CONFIG["settings"]["description"]
    )
    async def settings(self, interaction: discord.Interaction):
        """发送一个独立的设置面板"""
        config = self.db.get_config(interaction.user.id)
        view = SettingsView(self.db, config, self)  # 把 cog 实例传进去

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
        """显示你当前的默认协议"""
        config = self.db.get_config(interaction.user.id)
        embed = build_license_embed(config, interaction.user)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(LicenseCog(bot))
