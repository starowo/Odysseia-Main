import asyncio
import functools
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


def get_default_license_details(user_id: int) -> dict:
    """返回一份标准的、全新的默认授权协议详情字典"""
    return {
        "type": "custom",  # 新增字段，用于区分是 "custom" 还是 "cc"
        "reproduce": "询问作者",
        "derive": "询问作者",
        "commercial": "禁止",
        "attribution": f"<@{user_id}>",
        "notes": "无"
    }


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

class LicenseEditHubView(ui.View):
    """一个让用户选择如何编辑协议的“枢纽”视图"""

    def __init__(self, db: LicenseDB, config: LicenseConfig, ephemeral: bool, callback: callable):
        super().__init__(timeout=300)
        self.db = db
        self.config = config
        self.ephemeral = ephemeral  # 决定消息是否为私密
        self.callback = callback  # 操作完成后的回调函数

    async def send(self, interaction: discord.Interaction):
        """一个辅助方法，用于发送或编辑消息以显示此视图"""
        content = "请选择你希望如何设置你的授权协议："
        if self.ephemeral:
            await interaction.response.send_message(content, view=self, ephemeral=True)
        else:
            await interaction.response.edit_message(content=content, view=self)

    @ui.button(label="📝 使用自定义文本填写", style=discord.ButtonStyle.primary, row=0)
    async def set_with_custom(self, interaction: discord.Interaction, button: ui.Button):
        # 传递回调函数
        modal = CustomLicenseEditModal(self.db, self.config, callback=self.callback)
        await interaction.response.send_modal(modal)
        self.stop()

    @ui.button(label="📜 从CC协议模板中选择", style=discord.ButtonStyle.secondary, row=0)
    async def set_with_cc(self, interaction: discord.Interaction, button: ui.Button):
        # 传递回调函数
        cc_view = CCLicenseSelectView(self.db, self.config, callback=self.callback)
        await interaction.response.edit_message(content="请从下面的模板中选择一个CC协议：", view=cc_view)
        self.stop()


class AttributionNotesModal(ui.Modal, title="填写署名与备注"):
    """一个只询问署名和备注的简单Modal"""

    def __init__(self, default_attribution: str, default_notes: str):
        super().__init__()
        self.attribution = ui.TextInput(label="署名要求", default=default_attribution)
        self.notes = ui.TextInput(label="附加说明 (可选)", default=default_notes if default_notes != "无" else "", required=False,
                                  style=discord.TextStyle.paragraph)
        self.add_item(self.attribution)
        self.add_item(self.notes)

    async def on_submit(self, interaction: discord.Interaction):
        # 这个 modal 不直接保存，它只把结果返回给调用它的 View
        await interaction.response.defer()  # 响应交互，但不发送任何消息
        self.stop()


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
        self.config.license_details = {
            "type": "custom",
            "reproduce": self.reproduce.value,
            "derive": self.derive.value,
            "commercial": self.commercial.value,
            "attribution": self.attribution.value,
            "notes": self.notes.value or "无"
        }
        self.db.save_config(self.config)
        await self.callback(interaction, self.config.license_details)


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

        # 弹出简单的 Modal 来获取署名和备注
        modal = AttributionNotesModal(
            default_attribution=self.config.license_details.get("attribution", f"<@{self.config.user_id}>"),
            default_notes=self.config.license_details.get("notes", "无")
        )
        await interaction.response.send_modal(modal)
        await modal.wait()

        # Modal 提交后，组合所有数据并保存
        self.config.license_details = {
            "type": selected_cc,
            "reproduce": cc_data["reproduce"],
            "derive": cc_data["derive"],
            "commercial": cc_data["commercial"],
            "attribution": modal.attribution.value,
            "notes": modal.notes.value or "无"
        }
        self.db.save_config(self.config)

        # 调用回调而不是直接保存
        await self.callback(interaction, self.config.license_details)
        # 清理选择界面
        await interaction.edit_original_response(content=f"已选择协议：**{selected_cc}**，正在处理...", view=None)
        self.stop()


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
    """在新帖下询问作者操作的视图（功能增强版）"""

    def __init__(self, db: LicenseDB, config: LicenseConfig, thread: discord.Thread):
        super().__init__(timeout=3600)
        self.db = db
        self.config = config
        self.thread = thread
        self.owner_id = thread.owner_id

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

    async def _post_once_callback(self, interaction: discord.Interaction, new_details: dict):
        """回调：用于一次性发布协议"""
        # 创建一个临时的 config 对象来构建 embed
        temp_config = LicenseConfig(self.config.user_id)
        temp_config.license_details = new_details

        temp_license_embed = build_license_embed(temp_config, interaction.user)
        await self.thread.send(embed=temp_license_embed)

        # 更新原始助手消息，告知操作完成
        # 注意：这里我们不能用 interaction.edit_original_response，因为它编辑的是枢纽视图的消息。
        # 我们需要找到原始的助手消息并编辑它。但这会让逻辑变得复杂。
        # 一个更简单的做法是，直接在频道里发送一个确认消息。
        await self.thread.send(f"✅ {interaction.user.mention}，你的一次性协议已发布。你的默认协议未被更改。")

        # 同时，在私密消息流中给用户一个最终确认
        if not interaction.response.is_done():
            await interaction.response.edit_message(content="✅ 操作完成！", view=None)
        else:
            await interaction.followup.send("✅ 操作完成！", ephemeral=True)

    @ui.button(label="发布默认协议", style=discord.ButtonStyle.success, row=0)
    async def post_default(self, interaction: discord.Interaction, button: ui.Button):
        # ... (此部分代码与你上一版“造轮子”的方案完全相同，此处省略以节省空间) ...
        # ... (核心逻辑是：显示预览 -> 使用CustomConfirmView -> 根据结果发布或返回) ...
        original_embed = interaction.message.embeds[0]
        license_embed = build_license_embed(self.config, interaction.user)
        preview_embed = license_embed.copy()
        preview_embed.title = "🔍 协议预览与确认"
        preview_embed.description = "**你确定要以以下协议发布吗？**\n\n(此为预览，确认后将公开发布)"
        confirmation_view = CustomConfirmView(author=interaction.user, timeout=120)
        await interaction.response.edit_message(embed=preview_embed, view=confirmation_view)
        await confirmation_view.wait()
        if confirmation_view.value is True:
            await interaction.edit_original_response(content="✅ 已确认，协议已发布。", embed=None, view=None)
            await self.thread.send(embed=license_embed)
            self.stop()
        else:
            await interaction.edit_original_response(embed=original_embed, view=self)

    @ui.button(label="编辑并发布(仅本次)", style=discord.ButtonStyle.primary, row=0)
    async def edit_and_post_once(self, interaction: discord.Interaction, button: ui.Button):
        """核心新功能：一次性编辑并发布"""
        hub_view = LicenseEditHubView(
            db=self.db,
            config=self.config,
            ephemeral=True,
            callback=self._post_once_callback  # 传入“一次性发布”的回调
        )
        await hub_view.send(interaction)
        # 这里的交互是临时的，不影响主视图
        # 主视图 (InitialActionView) 会继续等待操作

    @ui.button(label="永久编辑默认协议", style=discord.ButtonStyle.secondary, row=1)
    async def edit_default_license(self, interaction: discord.Interaction, button: ui.Button):
        hub_view = LicenseEditHubView(
            db=self.db,
            config=self.config,
            ephemeral=True,
            callback=self._save_and_confirm_callback  # 传入“永久保存”的回调
        )
        await hub_view.send(interaction)
        # 同样是临时交互

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
        # 跳转到增强版的设置视图
        view = SettingsView(db=self.db, config=self.config)
        await view.send(interaction)

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
    """机器人行为设置视图（功能增强版）"""

    def __init__(self, db: LicenseDB, config: LicenseConfig):
        super().__init__(timeout=300)
        self.db = db
        self.config = config
        self.update_buttons()

    def update_buttons(self):
        """根据当前配置更新按钮标签"""
        self.toggle_auto_post_button.label = f"自动发布: {'✅' if self.config.auto_post else '❌'}"
        self.toggle_bot_enabled_button.label = f"机器人总开关: {'✅' if self.config.bot_enabled else '❌'}"
        self.toggle_confirmation_button.label = f"发布前二次确认: {'✅' if self.config.require_confirmation else '❌'}"

    async def send(self, interaction: discord.Interaction):
        """一个辅助方法，用于发送或编辑消息以显示此视图"""
        embed = discord.Embed(
            title="⚙️ 机器人设置",
            description="在这里管理授权助手的所有行为。",
            color=discord.Color.blurple()
        )
        # 如果是首次发送，就用 send_message，否则用 edit_message
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.send_message(embed=embed, view=self, ephemeral=True)

    @ui.button(label="切换自动发布", style=discord.ButtonStyle.primary, row=0)
    async def toggle_auto_post_button(self, interaction: discord.Interaction, button: ui.Button):
        self.config.auto_post = not self.config.auto_post
        self.db.save_config(self.config)
        self.update_buttons()
        await self.send(interaction)

    @ui.button(label="切换机器人启用状态", style=discord.ButtonStyle.primary, row=0)
    async def toggle_bot_enabled_button(self, interaction: discord.Interaction, button: ui.Button):
        self.config.bot_enabled = not self.config.bot_enabled
        self.db.save_config(self.config)
        self.update_buttons()
        await self.send(interaction)

    @ui.button(label="切换二次确认", style=discord.ButtonStyle.primary, row=1)
    async def toggle_confirmation_button(self, interaction: discord.Interaction, button: ui.Button):
        self.config.require_confirmation = not self.config.require_confirmation
        self.db.save_config(self.config)
        self.update_buttons()
        await self.send(interaction)

    # --- 危险操作区域 ---
    @ui.button(label="重置我的协议", style=discord.ButtonStyle.danger, row=2)
    async def reset_license(self, interaction: discord.Interaction, button: ui.Button):
        confirm_view = CustomConfirmView(author=interaction.user)
        await interaction.response.edit_message(
            content="**⚠️ 警告：** 此操作会将你的默认协议恢复为社区初始设置，此前的自定义内容将丢失！\n请确认你的操作：",
            embed=None,
            view=confirm_view
        )
        await confirm_view.wait()
        if confirm_view.value:
            self.config.license_details = get_default_license_details(self.config.user_id)
            self.db.save_config(self.config)
            await interaction.edit_original_response(content="✅ 你的授权协议已重置为默认值。", view=None)
        else:
            await self.send(interaction)  # 取消则返回设置主界面

    @ui.button(label="删除所有数据", style=discord.ButtonStyle.danger, row=2)
    async def delete_data(self, interaction: discord.Interaction, button: ui.Button):
        confirm_view = CustomConfirmView(author=interaction.user)
        await interaction.response.edit_message(
            content="**🚨 终极警告：** 此操作将**永久删除**你保存在本机器人中的所有数据（包括协议和所有设置）！\n此操作无法撤销！**请再次确认！**",
            embed=None,
            view=confirm_view
        )
        await confirm_view.wait()
        if confirm_view.value:
            user_file = self.db._get_user_file(self.config.user_id)
            if user_file.exists():
                user_file.unlink()  # 使用 pathlib 删除文件
            await interaction.edit_original_response(content="🗑️ 你的所有数据已被永久删除。", view=None)
        else:
            await self.send(interaction)  # 取消则返回设置主界面


# --- 辅助函数 ---
def build_license_embed(config: LicenseConfig, author: discord.User) -> discord.Embed:
    """根据配置构建授权协议的Embed（V3版：实时获取CC协议条款）"""

    # 这是一个临时的、完整的协议详情字典，用于本次展示
    display_details = config.license_details.copy()
    license_type = display_details.get("type", "custom")

    # --- 核心修改在这里 ---
    if license_type != "custom" and license_type in CC_LICENSES:
        # 如果是CC协议，从常量中实时加载标准条款
        cc_standard_terms = CC_LICENSES[license_type]

        # 使用 update() 方法，将标准条款合并到我们的展示用字典中
        # 这会添加 "reproduce", "derive", "commercial", "url" 等字段
        # 同时会保留数据库中已有的 "attribution" 和 "notes"
        display_details.update(cc_standard_terms)

    # 从这里开始，后续代码完全不需要改变，因为 display_details 已经是完整的了

    embed = discord.Embed(
        title=f"📜 {author.display_name} 的内容授权协议",
        color=discord.Color.gold()
    )
    embed.set_thumbnail(url=author.display_avatar.url)

    if license_type != "custom" and license_type in CC_LICENSES:
        embed.add_field(
            name="📄 协议类型 (License Type)",
            value=f"**[{license_type}]({display_details['url']})**",
            inline=False
        )
        embed.description = f"本内容采用 **{license_type}** 国际许可协议进行许可。点击上方链接查看完整协议。"

    embed.add_field(name="🔁 转载", value=display_details.get("reproduce", "未设置"), inline=True)
    embed.add_field(name="🎨 衍生创作", value=display_details.get("derive", "未设置"), inline=True)
    embed.add_field(name="💰 商业用途", value=display_details.get("commercial", "未设置"), inline=True)
    embed.add_field(name="✒️ 署名要求", value=display_details.get("attribution", "未设置"), inline=False)

    notes = display_details.get("notes")
    if notes and notes != "无":
        embed.add_field(name="📝 附加说明", value=notes, inline=False)

    embed.set_footer(text=f"该协议由作者设置 | 使用 /license 命令管理你的协议")

    return embed


class FirstTimeSetupView(ui.View):
    """引导新用户首次创建协议的视图"""

    def __init__(self, cog: 'LicenseCog', db: LicenseDB, owner_id: int, thread: discord.Thread):
        super().__init__(timeout=3600)  # 1小时后失效
        self.db = db
        self.owner_id = owner_id
        self.thread = thread
        self.cog = cog  # 存储对主 Cog 的引用

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ 这不是你的帖子，不能进行操作哦。", ephemeral=True)
            return False
        return True

    async def _first_time_save_callback(self, interaction: discord.Interaction, new_details: dict):
        """专门为新用户设计的、保存并过渡到主界面的回调。"""
        # 1. 调用 Cog 的标准保存方法来处理数据存储
        await self.cog._save_and_confirm_callback(interaction, self.owner_id, new_details)

        # 2. 刷新主界面，进入标准模式
        #    这里的 interaction 是从编辑流程中传回来的，我们需要用它来编辑最开始的那个“欢迎”消息
        #    幸运的是，interaction.message 指向的就是那个消息！
        new_config = self.db.get_config(self.owner_id)
        main_view = InitialActionView(self.db, new_config, self.thread)

        embed = discord.Embed(
            title=f"✅ 协议已创建！你好, {interaction.user.display_name}！",
            description="你的默认协议已保存。现在，你希望如何处理这个帖子的授权呢？",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"{self.cog.HELPER_SIGNATURE} | 你已进入标准操作模式")

        await interaction.message.edit(content=None, embed=embed, view=main_view)

    @ui.button(label="✨ 创建我的授权协议", style=discord.ButtonStyle.success)
    async def create_license(self, interaction: discord.Interaction, button: ui.Button):
        config = self.db.get_config(self.owner_id)

        # 将我们专为新用户设计的的回调传递下去
        hub_view = LicenseEditHubView(
            db=self.db,
            config=config,
            callback=self._first_time_save_callback,
            ephemeral=False
        )

        # 使用 hub_view 的 send 方法来编辑当前消息
        await hub_view.send(interaction)

        # hub_view 和它的回调会处理后续所有事情，所以这里不用再 wait 了
        self.stop()

    @ui.button(label="本次跳过", style=discord.ButtonStyle.secondary, row=0)
    async def skip_for_now(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(content="好的，你随时可以通过 `/license` 命令来设置你的授权协议。", view=None)
        self.stop()

    @ui.button(label="别再打扰我", style=discord.ButtonStyle.danger, row=0)
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

    async def _save_and_confirm_callback(self, interaction: discord.Interaction, user_id: int, new_details: dict):
        """
        一个标准的回调函数，用于保存用户的协议配置并发送确认。（V2版：优化CC协议存储）
        """
        config = self.db.get_config(user_id)
        license_type = new_details.get("type", "custom")

        # --- 核心修改在这里 ---
        if license_type != "custom" and license_type in CC_LICENSES:
            # 对于CC协议，我们只存储类型、署名和备注。
            # 其他所有条款（reproduce, derive, commercial）都将被舍弃，以保证纯洁性。
            final_details_to_save = {
                "type": license_type,
                "attribution": new_details.get("attribution", f"<@{user_id}>"),
                "notes": new_details.get("notes", "无")
            }
        else:
            # 对于自定义协议，我们保存所有内容。
            final_details_to_save = new_details
            # 确保自定义协议的 type 字段是正确的
            final_details_to_save["type"] = "custom"

        # 更新配置并保存
        config.license_details = final_details_to_save
        self.db.save_config(config)

        # 后续的确认消息逻辑保持不变
        try:
            await interaction.followup.send("✅ 你的默认协议已更新并保存！", ephemeral=True)
            if not interaction.is_expired():
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
        footer_text = f"{HELPER_SIGNATURE} | 如果按钮失效，请使用 /license remind"

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
            view = InitialActionView(self.db, config, thread)
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
    license_group = app_commands.Group(name="license", description="管理你的内容授权协议")

    @license_group.command(name="remind", description="在当前帖子中重新发送授权协议助手提醒")
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

    @license_group.command(name="edit", description="创建或修改你的默认授权协议")
    async def edit_license(self, interaction: discord.Interaction):
        """打开授权协议编辑中心。"""
        config = self.db.get_config(interaction.user.id)

        # 使用 functools.partial 来创建一个已经包含了 user_id 的新函数
        save_callback = functools.partial(self._save_and_confirm_callback, user_id=interaction.user.id)

        # 关键：将创建好的回调函数传递下去
        hub_view = LicenseEditHubView(
            db=self.db,
            config=config,
            # 这个是独立的私密消息，所以 ephemeral=True
            # callback 使用我们刚刚创建的偏函数
            callback=save_callback,
            ephemeral=True
        )

        await hub_view.send(interaction)  # send 方法现在需要自己处理 ephemeral

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
