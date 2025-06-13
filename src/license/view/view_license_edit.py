# --- 交互界面层 (Modals & Views) ---
from datetime import datetime
from typing import Dict, Any

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

    def __init__(self, db: LicenseDB, config: LicenseConfig, callback: callable, on_cancel: callable, commercial_use_allowed: bool, content: str,
                 is_temporary: bool, owner_id: int):
        """
        Args:
            db: LicenseDB 实例，用于传递给子组件。
            config: 当前用户的配置，用于提供默认值。
            callback: 编辑成功后的回调函数，签名应为 `async def callback(interaction, new_details: dict)`。
            on_cancel: 用户点击取消按钮时的回调函数，签名应为 `async def on_cancel(interaction)`。
        """
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.is_temporary = is_temporary
        self.db = db
        self.config = config
        self.callback = callback
        self.on_cancel = on_cancel
        self.commercial_use_allowed = commercial_use_allowed
        self.content = content  # 保存引导文本

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """权限检查"""
        return await do_simple_owner_id_interaction_check(self.owner_id, interaction)

    @ui.button(label="📝 使用自定义文本填写", style=discord.ButtonStyle.primary, row=0)
    async def set_with_custom(self, interaction: discord.Interaction, button: ui.Button):
        """点击此按钮，会弹出一个用于填写所有自定义协议条款的 Modal。"""
        # 创建 Modal，并将顶层回调函数 `self.callback` 传递给它。
        modal = CustomLicenseEditModal(self.db, self.config, callback=self.callback, commercial_use_allowed=self.commercial_use_allowed,
                                       is_temporary=self.is_temporary)
        await interaction.response.send_modal(modal)

    @ui.button(label="📜 从CC协议模板中选择", style=discord.ButtonStyle.secondary, row=0)
    async def set_with_cc(self, interaction: discord.Interaction, button: ui.Button):
        """点击此按钮，会将当前视图替换为 CC 协议选择视图。"""
        await safe_defer(interaction)

        async def back_to_hub_callback(back_interaction: discord.Interaction):
            hub_embed = create_helper_embed(title="📝 编辑授权协议", description=self.content)
            await back_interaction.edit_original_response(embed=hub_embed, view=self)

        cc_view = CCLicenseSelectView(
            db=self.db, config=self.config, callback=self.callback, on_cancel=back_to_hub_callback,
            commercial_use_allowed=self.commercial_use_allowed, is_temporary=self.is_temporary, owner_id=self.owner_id
        )
        # 直接从子视图获取完整的初始消息载荷
        payload = cc_view.get_initial_payload()
        await interaction.edit_original_response(**payload)

    @ui.button(label="💻 从软件协议模板中选择", style=discord.ButtonStyle.secondary, row=0)
    async def set_with_software(self, interaction: discord.Interaction, button: ui.Button):
        """点击此按钮，会进入软件协议选择视图。"""
        await safe_defer(interaction)

        async def back_to_hub_callback(back_interaction: discord.Interaction):
            hub_embed = create_helper_embed(title="📝 编辑授权协议", description=self.content)
            await back_interaction.edit_original_response(embed=hub_embed, view=self)

        software_view = SoftwareLicenseSelectView(
            db=self.db, config=self.config, callback=self.callback, on_cancel=back_to_hub_callback,
            is_temporary=self.is_temporary, owner_id=self.owner_id
        )
        # 【优化】直接从子视图获取完整的初始消息载荷
        payload = software_view.get_initial_payload()
        await interaction.edit_original_response(**payload)

    @ui.button(label="取消", style=discord.ButtonStyle.danger, row=1)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        """点击取消，直接调用顶层的 `on_cancel` 回调。"""
        await safe_defer(interaction)
        await self.on_cancel(interaction)


class AttributionNotesModal(ui.Modal, title="填写署名与备注"):
    """
    一个简单的 Modal，仅用于让用户填写“署名要求”和“附加说明”。
    在选择CC协议后弹出，用于补充非核心条款。
    """

    def __init__(self, default_attribution: str, default_notes: str, final_callback: callable, is_temporary: bool):
        """
        Args:
            default_attribution: 默认显示的署名要求。
            default_notes: 默认显示的附加说明。
            final_callback: 用户提交 Modal 后的回调，签名应为 `async def callback(interaction, attribution: str, notes: str)`。
        """
        super().__init__()
        self.is_temporary = is_temporary

        # 根据 is_temporary 动态设置标签
        if is_temporary:
            attribution_label = "内容原作者署名"
        else:
            # Discord 的 Modal 标签支持换行符，是理想的提示位置
            attribution_label = "内容原作者署名\n (若为搬运作品，建议使用“仅本次”功能发布)"

        self.attribution = ui.TextInput(label=attribution_label, default=default_attribution)
        self.notes = ui.TextInput(label="附加说明 (可选)", default=default_notes if default_notes != "无" else "", required=False,
                                  style=discord.TextStyle.paragraph)
        self.add_item(self.attribution)
        self.add_item(self.notes)
        self.final_callback = final_callback

    async def on_submit(self, interaction: discord.Interaction):
        """当用户提交时，调用最终回调并传入填写的数据。"""
        await safe_defer(interaction)
        await self.final_callback(interaction, self.attribution.value, self.notes.value or "无")


class CustomLicenseEditModal(ui.Modal, title="编辑自定义授权协议"):
    """
    一个用于完整编辑自定义授权协议的 Modal。
    包含所有协议条款的文本输入框。
    """

    def __init__(self, db: LicenseDB, current_config: LicenseConfig, callback: callable, commercial_use_allowed: bool, is_temporary: bool):
        """
        Args:
            db: LicenseDB 实例。
            current_config: 当前用户配置，用于填充默认值。
            callback: 提交后的回调，签名应为 `async def callback(interaction, new_details: dict)`。
        """
        super().__init__()
        self.is_temporary = is_temporary
        self.db = db
        self.config = current_config
        self.callback = callback  # 存储顶层回调

        details = current_config.license_details
        self.reproduce = ui.TextInput(label="二次传播条款", default=details.get("reproduce"), max_length=100)
        self.derive = ui.TextInput(label="二次创作条款", default=details.get("derive"), max_length=100)
        # 根据开关状态决定“商业用途”输入框的行为
        if commercial_use_allowed:
            self.commercial = ui.TextInput(label="商业用途条款", default=details.get("commercial"), max_length=100)
        else:
            self.commercial = ui.TextInput(
                label="商业用途条款 (已禁用)",
                default="禁止 (服务器全局设置)",  # 提供清晰的默认值
            )

        # 根据 is_temporary 动态设置标签
        if is_temporary:
            attribution_label = "内容原作者署名"
        else:
            # Discord 的 Modal 标签支持换行符，是理想的提示位置
            attribution_label = "内容原作者署名\n (若为搬运作品，建议使用“仅本次”功能发布)"

        self.attribution = ui.TextInput(label=attribution_label, default=details.get("attribution", f"需保留创作者 <@{self.config.user_id}> 的署名"),
                                        max_length=100)
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
        await safe_defer(interaction)
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
    - 修复了在未选择协议时点击“查看知识”按钮会报错的问题。
    - 将视图渲染逻辑拆分为多个独立的Embed构建方法，使代码更清晰。
    """

    def __init__(self, db: LicenseDB, config: LicenseConfig, callback: callable, on_cancel: callable, commercial_use_allowed: bool, is_temporary: bool,
                 owner_id: int):  # <-- 修复了之前owner_id=bool的笔误
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.db = db
        self.config = config
        self.callback = callback
        self.on_cancel = on_cancel
        self.commercial_use_allowed = commercial_use_allowed
        self.is_temporary = is_temporary
        self.selected_license: Optional[str] = None
        self.show_knowledge = False  # 控制是否显示重要知识的内部状态

        self._initial_embed = self._build_initial_prompt_embed()

    def get_initial_payload(self) -> Dict[str, Any]:
        """提供一个清晰的公共接口，用于获取初始消息载荷。"""
        return {"embed": self._initial_embed, "view": self}

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """权限检查"""
        return await do_simple_owner_id_interaction_check(self.owner_id, interaction)

    # --- 专注的Embed构建辅助方法 ---

    def _build_initial_prompt_embed(self) -> discord.Embed:
        """只构建初始的、提示用户选择协议的Embed。"""
        initial_cc_content = (
            "请从下方选择一个标准的CC协议模板。\n\n"
            "- 你选择的协议将**覆盖**你当前的授权设置。\n"
            "- 选择后，你将看到协议的简介并可以确认。\n"
            "- ** 注意 ** ：这些协议不推荐用于软件工程领域内容。"
        )
        return create_helper_embed(
            title="📜 选择一个CC协议模板",
            description=initial_cc_content,
            color=discord.Color.green()
        )

    def _build_selected_license_details_embed(self) -> discord.Embed:
        """只构建包含特定协议详情的Embed。"""
        license_data = CC_LICENSES[self.selected_license]
        description_text = f"你选择了 **{self.selected_license}**。\n> {license_data['description']}\n\n"
        element_explanations = [CC_ELEMENT_EXPLANATIONS[elem] for elem in license_data["elements"]]
        core_content = (
            f"{description_text}"
            f"**核心条款解读：**\n"
            f"-------------------\n"
            f"{'\n\n'.join(element_explanations)}"
        )
        return create_helper_embed(
            title="📜 查看CC协议详情",
            description=core_content,
            color=discord.Color.green()
        )

    def _build_knowledge_embed(self) -> discord.Embed:
        """构建“重要知识”附录Embed，并按需定制URL。"""
        knowledge_text = (
            "**💡 关于授权协议的重要知识**\n\n"  # 增加换行

            "🖥️ **不推荐用于软件代码**\n"
            "> **请注意：** CC系列协议**主要为文章、图片、音乐、视频等**创作内容**设计。\n"
            "> 对于软件，我们强烈建议采用由 [开源促进会 (OSI)](https://opensource.org/) 审核和推荐，或受到广泛的认可的软件专用许可证，你可以在 “软件协议模板” 下找到一部分常用协议。\n\n"

            "⚖️ **协议的效力**\n"
            "> 作者一旦为某次发布选择了CC协议，该选择便具有法律约束力。\n\n"  # 增加换行

            "📝 **基于单次发布**\n"
            "> CC协议是附加在**作品的某一次发布**上的。作者可以为**未来的新作品**（即使是基于旧作品的修改）选择一个完全不同的协议。但是这不会影响对旧作品**已经做出**的授权（即，授权不可收回）。\n\n"  # 增加换行

            "🔄 **重新授权可能**\n"
            "> 作者甚至可以为**同一个旧作品**在未来提供一个**新的、并行的**授权选项（例如，从严格协议变为宽松协议，甚至从宽松协议变为严格协议）。届时，**他人**可以选择遵守旧的或新的任一协议。\n\n"  # 增加换行

            "👑 **作者本人许可优先**\n"
            "> 无论协议如何规定，只要**他人**能联系上原作者并获得其**单独、明确的许可**，就可以不受本协议限制。\n\n"  # 增加换行

            "📚 **解释仅供参考**\n"
            f"> 为便于理解，我们对协议条款进行了通俗化解释。这些解释（包括本**重要知识**）不应替代具有法律效力的官方协议原文。若有疑问，请以Creative Commons官方网站的说明为准。"
        )

        # 如果已选择协议，我们可以做得更好，把通用提示语替换为带链接的！
        if self.selected_license:
            url = CC_LICENSES[self.selected_license]['url']
            knowledge_text += f"\n> 若有疑问，请以后者为准：[官方协议原文]({url})"
        else:
            knowledge_text += "\n> 若有疑问，请以Creative Commons官方网站的说明为准。"

        return discord.Embed(
            description=knowledge_text,
            color=discord.Color.light_grey()
        )

    # --- 主渲染方法，负责组装Embeds列表 ---
    async def _render_view(self, interaction: discord.Interaction):
        await safe_defer(interaction)
        embeds_to_show = []

        # 1. 决定主Embed是什么
        if not self.selected_license:
            # 直接使用缓存的初始Embed
            primary_embed = self._initial_embed
        else:
            primary_embed = self._build_selected_license_details_embed()
        embeds_to_show.append(primary_embed)

        knowledge_button = get_item_by_id(self, "license_cog:cc_licenses:knowledge_button")

        # 2. 根据状态决定是否添加附录Embed，并更新按钮
        if self.show_knowledge:
            knowledge_button.label = "收起重要知识"
            knowledge_button.style = discord.ButtonStyle.primary
            appendix_embed = self._build_knowledge_embed()
            embeds_to_show.append(appendix_embed)
        else:
            knowledge_button.label = "💡 查看重要知识"
            knowledge_button.style = discord.ButtonStyle.secondary

        # 3. 发送组装好的Embeds列表
        await interaction.edit_original_response(embeds=embeds_to_show, view=self)

    # --- 组件 ---
    @ui.select(
        placeholder="请从这里选择一个CC协议...",
        options=[
            discord.SelectOption(label=data['label'], value=name, description=data['description'][:100])
            for name, data in get_available_cc_licenses().items()
        ],
        custom_id="license_cog:cc_licenses:select",
        row=0
    )
    async def select_callback(self, interaction: discord.Interaction, select: ui.Select):
        await safe_defer(interaction)
        self.selected_license = interaction.data["values"][0]

        confirm_button = get_item_by_id(self, "license_cog:cc_licenses:confirm_button")

        # --- 根据服务器设置和协议类型，决定确认按钮的状态 ---
        license_is_commercial = "NC" not in self.selected_license
        if isinstance(confirm_button, ui.Button):
            if not self.commercial_use_allowed and license_is_commercial:
                confirm_button.disabled = True
                confirm_button.label = "❌ 服务器已禁用商业协议"
            else:
                confirm_button.disabled = False
                confirm_button.label = "✅ 确认使用此协议"

        self.show_knowledge = False
        await self._render_view(interaction)

    @ui.button(label="✅ 请先选择一个协议", style=discord.ButtonStyle.success, disabled=True, custom_id="license_cog:cc_licenses:confirm_button", row=1)
    async def confirm_selection(self, interaction: discord.Interaction, button: ui.Button):
        if not self.selected_license: return
        cc_data = CC_LICENSES[self.selected_license]

        async def modal_submit_callback(modal_interaction, attribution, notes):
            final_details = {
                "type": self.selected_license, "reproduce": cc_data["reproduce"], "derive": cc_data["derive"],
                "commercial": cc_data["commercial"], "attribution": attribution, "notes": notes or "无"
            }
            await self.callback(modal_interaction, final_details)

        modal = AttributionNotesModal(
            default_attribution=self.config.license_details.get("attribution", ""),
            default_notes=self.config.license_details.get("notes", "无"),
            final_callback=modal_submit_callback,
            is_temporary=self.is_temporary
        )
        await interaction.response.send_modal(modal)

    @ui.button(label="💡 查看重要知识", style=discord.ButtonStyle.secondary, custom_id="license_cog:cc_licenses:knowledge_button", row=1)
    async def toggle_knowledge(self, interaction: discord.Interaction, button: ui.Button):
        await safe_defer(interaction)
        self.show_knowledge = not self.show_knowledge
        await self._render_view(interaction)

    @ui.button(label="返回", style=discord.ButtonStyle.danger, row=2)
    async def cancel_callback(self, interaction: discord.Interaction, button: ui.Button):
        await safe_defer(interaction)
        await self.on_cancel(interaction)


class SoftwareLicenseSelectView(ui.View):
    """
    让用户通过下拉菜单选择一个标准软件协议的视图。
    这是 CCLicenseSelectView 的一个变体，专为软件许可证设计。
    """

    def __init__(self, db: LicenseDB, config: LicenseConfig, callback: callable, on_cancel: callable, is_temporary: bool, owner_id: int):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.db = db
        self.config = config
        self.callback = callback
        self.on_cancel = on_cancel
        self.is_temporary = is_temporary
        self.selected_license: Optional[str] = None

        # 在初始化时就创建好初始Embed
        self._initial_embed = self._build_initial_prompt_embed()

    def get_initial_payload(self) -> Dict[str, Any]:
        """提供一个清晰的公共接口，用于获取初始消息载荷。"""
        return {"embed": self._initial_embed, "view": self}

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """权限检查"""
        return await do_simple_owner_id_interaction_check(self.owner_id, interaction)

    def _build_initial_prompt_embed(self) -> discord.Embed:
        """构建初始的、提示用户选择协议的Embed。"""
        initial_content = (
            "请从下方为你的**软件或代码项目**选择一个合适的开源许可证。\n\n"
            "- 你选择的协议**不会覆盖**你当前的授权设置，只会替换其类型。\n"
            "- 选择后，你将看到协议的简介并可以确认。\n"
            "- **注意**：这些协议不推荐用于文章、图片等创作内容。"
        )
        return create_helper_embed(
            title="💻 选择一个软件协议模板",
            description=initial_content,
            color=discord.Color.dark_blue()
        )

    def _build_selected_license_details_embed(self) -> discord.Embed:
        """构建包含特定软件协议详情的Embed。"""
        license_data = SOFTWARE_LICENSES[self.selected_license]
        core_content = (
            f"你选择了 **{self.selected_license}**。\n"
            f"> {license_data['description']}\n\n"
            f"**官方链接**\n"
            f"更多详情，请阅读 [官方协议原文]({license_data['url']})。"
        )
        return create_helper_embed(
            title=f"💻 查看 {self.selected_license} 协议详情",
            description=core_content,
            color=discord.Color.dark_blue()
        )

    async def _render_view(self, interaction: discord.Interaction):
        """主渲染方法，负责组装Embeds列表。"""
        await safe_defer(interaction)
        if not self.selected_license:
            # 直接使用缓存的初始Embed
            embed_to_show = self._initial_embed
        else:
            embed_to_show = self._build_selected_license_details_embed()
        await interaction.edit_original_response(embed=embed_to_show, view=self)

    @ui.select(
        placeholder="请从这里选择一个软件协议...",
        options=[
            discord.SelectOption(label=name, value=name, description=data['description'][:100])
            for name, data in get_available_software_licenses().items()
        ],
        custom_id="license_cog:software_licenses:select",
        row=0
    )
    async def select_callback(self, interaction: discord.Interaction, select: ui.Select):
        """用户从下拉菜单中选择一个项目后触发。"""
        await safe_defer(interaction)
        self.selected_license = interaction.data["values"][0]
        confirm_button = get_item_by_id(self,"license_cog:software_licenses:confirm_button")
        if isinstance(confirm_button, ui.Button):
            confirm_button.disabled = False
            confirm_button.label = "✅ 确认使用此协议"
        await self._render_view(interaction)

    @ui.button(label="✅ 请先选择一个协议", style=discord.ButtonStyle.success, disabled=True, custom_id="license_cog:software_licenses:confirm_button", row=1)
    async def confirm_selection(self, interaction: discord.Interaction, button: ui.Button):
        """确认选择，并弹出Modal填写署名和备注。"""
        if not self.selected_license: return

        async def modal_submit_callback(modal_interaction, attribution, notes):
            # 不覆盖核心条款，只更新类型、署名和备注。
            # 这样可以在切换回自定义协议时保留用户之前的设置。
            # 1. 从当前配置开始，保留所有未修改的字段。
            final_details = self.config.license_details.copy()

            # 2. 只更新用户本次操作明确设置的字段。
            final_details["type"] = self.selected_license
            final_details["attribution"] = attribution
            final_details["notes"] = notes or "无"

            # 3. 将更新后的数据传回上层回调。
            await self.callback(modal_interaction, final_details)

        modal = AttributionNotesModal(
            default_attribution=self.config.license_details.get("attribution", f"Copyright (c) {datetime.now().year} <@{self.config.user_id}>"),
            default_notes=self.config.license_details.get("notes", "无"),
            final_callback=modal_submit_callback,
            is_temporary=self.is_temporary
        )
        await interaction.response.send_modal(modal)

    @ui.button(label="返回", style=discord.ButtonStyle.danger, row=2)
    async def cancel_callback(self, interaction: discord.Interaction, button: ui.Button):
        """调用“返回”回调。"""
        await safe_defer(interaction)
        await self.on_cancel(interaction)
