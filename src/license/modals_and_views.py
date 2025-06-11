# --- 交互界面层 (Modals & Views) ---
import asyncio
from typing import TYPE_CHECKING

from discord import ui

from .tool_view import ConfirmPostView
from .ui_factory import prepare_edit_hub, prepare_confirmation_flow

if TYPE_CHECKING:
    from .cog import LicenseCog
from src.license.utils import *


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

    def __init__(self, db: LicenseDB, config: LicenseConfig, callback: callable, on_cancel: callable, commercial_use_allowed: bool):
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
        self.commercial_use_allowed = commercial_use_allowed

    @ui.button(label="📝 使用自定义文本填写", style=discord.ButtonStyle.primary, row=0)
    async def set_with_custom(self, interaction: discord.Interaction, button: ui.Button):
        """点击此按钮，会弹出一个用于填写所有自定义协议条款的 Modal。"""
        # 创建 Modal，并将顶层回调函数 `self.callback` 传递给它。
        modal = CustomLicenseEditModal(self.db, self.config, callback=self.callback, commercial_use_allowed=self.commercial_use_allowed)
        await interaction.response.send_modal(modal)

    @ui.button(label="📜 从CC协议模板中选择", style=discord.ButtonStyle.secondary, row=0)
    async def set_with_cc(self, interaction: discord.Interaction, button: ui.Button):
        """点击此按钮，会将当前视图替换为 CC 协议选择视图。"""
        # 创建下一个视图，并将顶层回调 `self.callback` 传递给它。
        cc_view = CCLicenseSelectView(self.db, self.config, callback=self.callback, commercial_use_allowed=self.commercial_use_allowed)
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

    def __init__(self, db: LicenseDB, current_config: LicenseConfig, callback: callable, commercial_use_allowed: bool):
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
        # 根据开关状态决定“商业用途”输入框的行为
        if commercial_use_allowed:
            self.commercial = ui.TextInput(label="是否允许商业性使用？", default=details.get("commercial"), max_length=100)
        else:
            self.commercial = ui.TextInput(
                label="商业性使用 (已禁用)",
                default="禁止 (服务器全局设置)",  # 提供清晰的默认值
            )

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

    def __init__(self, db: LicenseDB, config: LicenseConfig, callback: callable, commercial_use_allowed: bool):
        super().__init__(timeout=300)
        self.db = db
        self.config = config
        self.callback = callback  # 存储顶层回调

        # 【核心逻辑】使用新的 Getter 来获取过滤后的协议列表
        available_licenses = get_available_cc_licenses(commercial_use_allowed)
        options = [discord.SelectOption(label=name, value=name) for name in available_licenses.keys()]

        # 如果过滤后没有选项，可以提供一个提示
        if not options:
            options.append(discord.SelectOption(label="无可用非商业CC协议", value="disabled", emoji="❌"))
            self.add_item(ui.Select(placeholder="服务器已禁用商业协议", options=options, disabled=True))
        else:
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


class InitialActionView(ui.View):
    """
    这是用户发帖后看到的主要交互面板（针对已注册用户）。
    提供了所有核心操作的入口：直接发布、临时编辑后发布、永久编辑、预览、设置等。
    """

    def __init__(self, cog: 'LicenseCog', db: LicenseDB, config: LicenseConfig, thread: discord.Thread, commercial_use_allowed: bool):
        super().__init__(timeout=3600)  # 较长的超时时间，给用户充分的反应时间
        self.cog = cog
        self.db = db
        self.config = config
        self.thread = thread
        self.owner_id = thread.owner_id
        # 缓存原始的Embed，以便在各种操作后可以方便地“返回主菜单”。
        self._original_embed = None
        self.commercial_use_allowed = commercial_use_allowed

    async def build_original_embed(self) -> discord.Embed:
        """构建主交互面板的Embed。"""
        member = await get_member_async_thread(self.thread, self.owner_id)
        display_name = member.display_name if member else "创作者"

        embed = discord.Embed(
            title=f"👋 你好, {display_name}！",
            description="我注意到你发布了一个新作品。你希望如何处理内容的授权协议呢？",
            color=discord.Color.blue()
        )
        embed.set_footer(text=build_footer_text(SIGNATURE_HELPER))
        return embed

    async def get_original_embed(self):
        if self._original_embed is None:
            self._original_embed = await self.build_original_embed()
        return self._original_embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """确保只有帖子作者可以操作。"""
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ 这不是你的帖子，不能进行操作哦。", ephemeral=True)
            return False
        return True

    # --- 核心UI流程方法 ---

    async def post_license_directly(self, interaction: discord.Interaction, config_to_post: LicenseConfig):
        """
        【最终简化版】一个直接发布协议的辅助函数。
        它现在相信 build_license_embed 总能成功。
        """
        # 【核心修复】直接构建并获取 Embed，不再检查错误
        final_embed = build_license_embed(
            config_to_post,
            interaction.user,
            self.commercial_use_allowed
        )

        # 直接发布
        await self.thread.send(embed=final_embed)
        await interaction.response.edit_message(
            content="✅ 协议已直接发布。", embed=None, view=None
        )
        if isinstance(interaction, discord.Interaction):
            self.stop()

    async def show_confirmation_view(self, interaction: discord.Interaction, config_to_show: LicenseConfig):
        """
        显示预览和确认发布的界面。这是一个可复用的流程。
        Args:
            interaction: 触发此流程的交互。
            config_to_show: 需要被预览和发布的 `LicenseConfig` 对象。
        """

        # 定义确认和取消按钮的具体行为
        async def do_post(post_interaction: discord.Interaction, final_embed: discord.Embed):
            """确认=发帖并关闭面板"""
            await self.thread.send(embed=final_embed)
            await post_interaction.response.edit_message(content="✅ 协议已发布。", embed=None, view=None)
            self.stop()

        async def do_cancel(cancel_interaction: discord.Interaction):
            """取消=返回主菜单"""
            await self.back_to_main_menu(cancel_interaction)

        # 创建并显示确认视图
        preview_embed, confirm_view = await prepare_confirmation_flow(
            cog=self.cog,  # 传递 self.cog！
            thread=self.thread,
            config=config_to_show,
            author=interaction.user,
            on_confirm_action=do_post,
            on_cancel_action=do_cancel
        )

        await interaction.response.edit_message(embed=preview_embed, view=confirm_view)

    async def back_to_main_menu(self, interaction: discord.Interaction):
        """
        一个可复用的方法，用于将UI完全恢复到初始的主菜单状态。
        """

        # 核心：用原始的Embed和自身(self, 即InitialActionView)来编辑消息，实现“返回”效果。
        await interaction.response.edit_message(
            content=None,  # 清除可能存在的上层文本，如“你正在编辑...”
            embed=await self.get_original_embed(),
            view=self
        )

    # --- 按钮定义 ---

    @ui.button(label="发布默认协议", style=discord.ButtonStyle.success, row=0)
    async def post_default(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：直接使用用户保存的默认配置进行发布流程。"""
        if self.config.require_confirmation:
            await self.show_confirmation_view(interaction, self.config)
        else:
            await self.post_license_directly(interaction, self.config)

    @ui.button(label="编辑并发布(仅本次)", style=discord.ButtonStyle.primary, row=0)
    async def edit_and_post_once(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：临时编辑协议并发布。"""

        # 1. 定义此场景下的回调
        async def on_edit_complete(edit_interaction: discord.Interaction, temp_details: dict):
            temp_config = LicenseConfig(edit_interaction.user)
            temp_config.license_details = temp_details
            await self.show_confirmation_view(edit_interaction, temp_config)

        async def on_edit_cancel(cancel_interaction: discord.Interaction):
            await self.back_to_main_menu(cancel_interaction)

        # 2. 调用工厂
        content, hub_view = prepare_edit_hub(
            db=self.db,
            config=self.config,
            on_success_callback=on_edit_complete,
            on_cancel_callback=on_edit_cancel,
            commercial_use_allowed=self.commercial_use_allowed,
            is_temporary=True  # 标记为临时编辑
        )

        # 3. 呈现UI
        await interaction.response.edit_message(
            content=content,
            embed=None,
            view=hub_view
        )

    @ui.button(label="编辑默认协议", style=discord.ButtonStyle.secondary, row=0)
    async def edit_default_license(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：编辑并永久保存用户的默认协议。"""

        # 1. 定义此场景下的“成功”和“取消”回调
        async def on_edit_complete(edit_interaction: discord.Interaction, new_details: dict):
            # 成功：保存配置，然后返回主菜单
            self.config.license_details = new_details
            self.db.save_config(self.config)
            await self.back_to_main_menu(edit_interaction)
            await edit_interaction.followup.send("✅ 你的默认协议已永久保存！", ephemeral=True)

        async def on_edit_cancel(cancel_interaction: discord.Interaction):
            # 取消：直接返回主菜单
            await self.back_to_main_menu(cancel_interaction)

        # 2. 调用同一个工厂函数来构建UI组件
        content, hub_view = prepare_edit_hub(
            db=self.db,
            config=self.config,
            on_success_callback=on_edit_complete,
            on_cancel_callback=on_edit_cancel,
            commercial_use_allowed=self.commercial_use_allowed,
            is_temporary=False
        )

        # 3. 在自己的上下文中呈现UI (编辑当前消息)
        await interaction.response.edit_message(
            content=content,
            embed=None,
            view=hub_view
        )

    @ui.button(label="预览协议", style=discord.ButtonStyle.primary, row=0)
    async def preview_license(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：以一条临时的、只有自己能看到的消息来预览当前默认协议。"""
        # defer() 只是为了确认交互，防止超时。
        await interaction.response.defer(thinking=False, ephemeral=True)

        embed = build_license_embed(self.config, interaction.user, commercial_use_allowed=self.commercial_use_allowed)
        embed.title = "👀 你的当前默认协议预览"
        embed.set_footer(text=build_footer_text(SIGNATURE_HELPER))  # 覆盖掉带有官方签名的页脚

        # 使用 followup.send 发送私密消息。这是最可靠的发送 ephemeral 消息的方式。
        await interaction.followup.send(embed=embed, ephemeral=True)

    @ui.button(label="机器人设置", style=discord.ButtonStyle.secondary, row=1)
    async def settings(self, interaction: discord.Interaction, button: ui.Button):
        """按钮：打开独立的机器人行为设置面板。"""
        # 这个逻辑和斜杠命令 `/内容授权 设置` 完全一样
        config = self.db.get_config(interaction.user)
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
        config = self.db.get_config(interaction.user)
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
            await asyncio.sleep(1)
            await confirm_interaction.delete_original_response()

        async def on_cancel(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(content="🚫 操作已取消。", embed=None, view=None)
            await asyncio.sleep(1)
            await cancel_interaction.delete_original_response()

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

            # 成功后，更新确认消息
            await confirm_interaction.response.edit_message(content="🗑️ **你的所有数据已被永久删除。**", view=None)
            await asyncio.sleep(1)
            await confirm_interaction.delete_original_response()

        async def on_cancel(cancel_interaction: discord.Interaction):
            await cancel_interaction.response.edit_message(content="🚫 操作已取消，你的数据安然无恙。", view=None)
            await asyncio.sleep(1)
            await cancel_interaction.delete_original_response()

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


class FirstTimeSetupView(ui.View):
    """
    新用户第一次与机器人交互时看到的欢迎和引导视图。
    主要目的是引导用户完成首次协议创建。
    """

    def __init__(self, cog: 'LicenseCog', db: 'LicenseDB', owner_id: int, thread: discord.Thread, commercial_use_allowed: bool):
        super().__init__(timeout=3600)
        self.cog = cog
        self.db = db
        self.owner_id = owner_id
        self.thread = thread
        self.commercial_use_allowed = commercial_use_allowed

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
        config = self.db.get_config(interaction.user)  # 获取一个默认配置

        # 定义创建完成后的行为：保存数据，然后用标准的主交互面板替换当前欢迎界面
        async def on_create_complete(create_interaction: discord.Interaction, new_details: dict):
            # a. 保存数据
            config.license_details = new_details
            self.db.save_config(config)

            # b. 创建标准的主交互面板视图
            main_view = InitialActionView(self.cog, self.db, config, self.thread, commercial_use_allowed=self.commercial_use_allowed)

            # c. 用主交互面板替换当前的欢迎界面
            await create_interaction.response.edit_message(
                content=None,  # 清理掉之前的欢迎文字
                embed= await main_view.get_original_embed(),
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
            on_cancel=on_create_cancel,
            commercial_use_allowed=self.commercial_use_allowed
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
        config = self.db.get_config(interaction.user)
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
