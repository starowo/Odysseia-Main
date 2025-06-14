# --- 交互界面层 (Modals & Views) ---
from datetime import datetime
from typing import Dict, Any

from src.license.utils import *


# --- 第二步的视图，在原地编辑后显示 ---
class CustomEditStep2View(ui.View):
    def __init__(self, owner_id: int, core_terms: dict, prefill_data: dict, final_callback: callable, on_cancel: callable, is_temporary: bool,
                 on_save_action: callable):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.core_terms = core_terms
        self.prefill_data = prefill_data
        self.final_callback = final_callback
        self.on_cancel = on_cancel
        self.is_temporary = is_temporary
        self.on_save_action = on_save_action

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await do_simple_owner_id_interaction_check(self.owner_id, interaction)

    @ui.button(label="📝 编辑附加信息 (第 2/2 步)", style=discord.ButtonStyle.primary)
    async def edit_notes(self, interaction: discord.Interaction, button: ui.Button):
        # 这是最终提交时的回调
        async def final_submit_callback(modal_interaction: discord.Interaction, attribution: str, notes: str, personal_statement: str):
            new_details = {
                **self.core_terms,
                "attribution": attribution,
                "notes": notes,
                "personal_statement": personal_statement
            }
            processed_details = self.on_save_action(new_details)
            await self.final_callback(modal_interaction, processed_details)

        # 弹出第二个Modal
        second_modal = AttributionNotesModal(
            default_attribution=self.prefill_data.get("attribution", ""),
            default_notes=self.prefill_data.get("notes", "无"),
            default_personal_statement=self.prefill_data.get("personal_statement", "无"),
            final_callback=final_submit_callback,
            is_temporary=self.is_temporary
        )
        await interaction.response.send_modal(second_modal)

    @ui.button(label="取消编辑", style=discord.ButtonStyle.danger)
    async def cancel_edit(self, interaction: discord.Interaction, button: ui.Button):
        await safe_defer(interaction)
        await self.on_cancel(interaction)


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

    # +++ 所有编辑流程都通过 start_full_edit_flow 启动 +++
    async def start_flow_for(self, interaction: discord.Interaction, prefill_data: dict, on_save_action: callable, title_hint: Optional[str] = None):
        """一个统一的启动器，负责启动两步式编辑流程。"""

        # 这是第一步Modal提交后的回调
        async def core_modal_submit_callback(modal_interaction: discord.Interaction, core_terms: dict):
            # 创建第二步的视图和Embed
            step2_view = CustomEditStep2View(
                owner_id=modal_interaction.user.id,
                core_terms=core_terms,
                prefill_data=prefill_data,
                final_callback=self.callback,  # 顶层回调
                on_cancel=self.on_cancel,  # 顶层取消回调
                is_temporary=self.is_temporary,
                on_save_action=on_save_action
            )
            step2_embed = create_helper_embed(
                title="📝 编辑协议 (2/2)",
                description=(
                    "很遗憾，由于Discord的API限制，您不得不分两步对内容进行填写，我们对此深感抱歉。\n"
                    "✅ 核心条款已暂存。请点击下方按钮，继续填写附加信息。"
                )
            )
            # 原地编辑消息，进入第二步
            await modal_interaction.edit_original_response(embed=step2_embed, view=step2_view)

        # 启动流程：弹出第一个Modal
        core_modal = CustomLicenseCoreModal(
            prefill_data=prefill_data,
            callback=core_modal_submit_callback,
            commercial_use_allowed=self.commercial_use_allowed,
            title_hint=title_hint
        )
        await interaction.response.send_modal(core_modal)

    @ui.button(label="📝 使用自定义文本填写", style=discord.ButtonStyle.primary, row=0)
    async def set_with_custom(self, interaction: discord.Interaction, button: ui.Button):
        # 启动通用编辑流程
        await self.start_flow_for(
            interaction=interaction,
            prefill_data=self.config.license_details,
            on_save_action=lambda details: details,  # 自定义流程直接返回数据
            title_hint="自定义"
        )

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


class CustomLicenseCoreModal(ui.Modal):
    """
    第一步的Modal
    """

    def __init__(self, prefill_data: dict, callback: callable, commercial_use_allowed: bool, title_hint: Optional[str] = None):
        base_title = "编辑协议 - 核心条款"
        modal_title = f"{base_title} ({title_hint})" if title_hint else base_title
        if len(modal_title) > 45:
            modal_title = modal_title[:42] + "..."  # 留3个点
        super().__init__(title=modal_title)
        self.callback = callback

        # --- 提供更智能的默认值和 placeholder ---
        default_reproduce = prefill_data.get("reproduce") or "允许在本社区内转载，需注明出处"
        default_derive = prefill_data.get("derive") or "允许在本社区内进行二次创作，需注明出处"
        default_commercial = prefill_data.get("commercial") or "禁止"

        self.reproduce = ui.TextInput(
            label="二次传播 (转载/搬运) 条款",
            default=default_reproduce,
            placeholder="例如：需联系作者获得授权 / 仅允许在本站转载",
            max_length=100
        )
        self.derive = ui.TextInput(
            label="二次创作 (同人/改图) 条款",
            default=default_derive,
            placeholder="例如：需联系作者获得授权 / 允许，但禁止用于头像",
            max_length=100
        )

        if commercial_use_allowed:
            self.commercial = ui.TextInput(
                label="商业用途条款",
                default=default_commercial,
                placeholder="例如：禁止 / 允许，但需联系作者",
                max_length=100
            )
        else:
            self.commercial = ui.TextInput(label="商业用途条款 (已禁用)", default="禁止 (服务器全局设置)")

        self.add_item(self.reproduce)
        self.add_item(self.derive)
        self.add_item(self.commercial)

    async def on_submit(self, interaction: discord.Interaction):
        await safe_defer(interaction)
        core_terms = {
            "reproduce": self.reproduce.value,
            "derive": self.derive.value,
            "commercial": self.commercial.value
        }
        await self.callback(interaction, core_terms)


class AttributionNotesModal(ui.Modal, title="编辑协议 - 附加信息"):
    """
    第二步的Modal
    """

    def __init__(self, default_attribution: str, default_notes: str, default_personal_statement: str, final_callback: callable, is_temporary: bool):
        super().__init__()
        self.final_callback = final_callback

        attribution_label = "内容原作者署名" + ("" if is_temporary else "\n (若为搬运作品，建议使用“仅本次”功能发布)")
        self.attribution = ui.TextInput(label=attribution_label, default=default_attribution, style=discord.TextStyle.paragraph)
        self.notes = ui.TextInput(label="附加条款 (可选，严肃内容，被视作协议的一部分)", default=default_notes if default_notes != "无" else "", required=False,
                                  style=discord.TextStyle.paragraph)
        self.personal_statement = ui.TextInput(label="附言 (可选，个性化内容，通常不具备法律效力)",
                                               default=default_personal_statement if default_personal_statement != "无" else "", required=False,
                                               style=discord.TextStyle.paragraph)

        self.add_item(self.attribution)
        self.add_item(self.notes)
        self.add_item(self.personal_statement)

    async def on_submit(self, interaction: discord.Interaction):
        await safe_defer(interaction)
        await self.final_callback(interaction, self.attribution.value, self.notes.value or "无", self.personal_statement.value or "无")


class CCLicenseSelectView(ui.View):
    """
    让用户通过下拉菜单选择一个标准CC协议的视图。
    - 修复了在未选择协议时点击“查看知识”按钮会报错的问题。
    - 将视图渲染逻辑拆分为多个独立的Embed构建方法，使代码更清晰。
    """

    def __init__(self, db: LicenseDB, config: LicenseConfig, callback: callable, on_cancel: callable, commercial_use_allowed: bool, is_temporary: bool,
                 owner_id: int):
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
            "# ⚠️ **再次提醒**：\n"
            "## 选择CC协议意味着您的作品可能被广泛传播到您无法控制的地方。\n\n"
            "如何快速选择？CC协议是一个“组合式的协议”，其中，共享(CC)和署名(BY)是必选项，其他选项包含：\n\n"
            "想让您的作品和二创**永远保持开放共享**？\n"
            "➡️ **选 `相同方式共享 (SA)`**\n\n"
            "想禁止商业使用？\n"
            "➡️ **选 `非商业化 (NC)`**\n\n"
            "想让别人**只能看不能改**，完全禁止二创？\n"
            "➡️ **选 `禁止修改 (ND)`**\n\n"
            "下方选择一个协议，可查看更详细的场景化说明。"
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
        joined_explanations = '\n\n'.join(element_explanations)
        core_content = (
            f"{description_text}"
            f"**核心条款解读：**\n"
            f"-------------------\n"
            f"{joined_explanations}"
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
        """启动预填充的编辑流程 """
        CC_LICENSES_NOTES = "CC协议不应具备额外条款，如果对此处进行修改，会使得最终保存的协议变为自定义协议。"
        if not self.selected_license: return

        # 同样使用 LicenseEditHubView 的启动器
        hub_view = LicenseEditHubView(self.db, self.config, self.callback, self.on_cancel, self.commercial_use_allowed, "", self.is_temporary, self.owner_id)

        # --- 分离数据流 ---

        # 1. 获取原始、纯净的模板数据。这是用于“逻辑比较”的唯一真实来源。
        original_template = CC_LICENSES[self.selected_license]

        # 2. 创建一个独立的副本，专门用于“界面展示”。
        modal_prefill_data = original_template.copy()
        modal_prefill_data.update({
            "attribution": self.config.license_details.get("attribution", f"需保留创作者 <@{self.config.user_id}> 的署名"),
            "notes": CC_LICENSES_NOTES,
            "personal_statement": self.config.license_details.get("personal_statement", "无"),
        })

        # 3. 对“界面展示”用的数据进行预处理，替换占位符，使其对用户友好。
        license_name_to_display = self.selected_license
        for key in ["reproduce", "derive", "commercial"]:
            if key in modal_prefill_data and isinstance(modal_prefill_data[key], str):
                modal_prefill_data[key] = modal_prefill_data[key].format(license_type=license_name_to_display)

        expected_data_if_unmodified = modal_prefill_data.copy()

        # 4. 定义保存时的特殊逻辑。注意：它捕获并使用了原始的、纯净的`original_template`。
        def on_save_action(new_details: dict) -> dict:
            # 这里的比较，必须是拿用户提交的数据和“原始模板”进行比较，这样才绝对准确。
            is_modified = (
                    new_details["reproduce"] !=  expected_data_if_unmodified.get("reproduce") or
                    new_details["derive"] !=  expected_data_if_unmodified.get("derive") or
                    (self.commercial_use_allowed and new_details["commercial"] !=  expected_data_if_unmodified.get("commercial")) or
                    new_details["notes"] != CC_LICENSES_NOTES
            )

            final_details = self.config.license_details.copy()
            if is_modified:
                # 核心条款被修改，转为自定义协议并完全覆盖
                final_details = new_details
                final_details["type"] = "custom"
            else:
                # 核心条款未变，只更新非核心部分，保留类型
                final_details["type"] = self.selected_license
                final_details["attribution"] = new_details["attribution"]
                final_details["personal_statement"] = new_details["personal_statement"]

            return final_details

        # 调用hub_view上的通用启动器
        await hub_view.start_flow_for(
            interaction=interaction,
            prefill_data=modal_prefill_data,
            on_save_action=on_save_action,
            title_hint=f"改动即转为自定义"
        )

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
            "- 我们更建议您直接在自己的代码仓库中提供许可证信息，不过，您可以浏览这些常见的软件协议作为科普。\n"
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
        confirm_button = get_item_by_id(self, "license_cog:software_licenses:confirm_button")
        if isinstance(confirm_button, ui.Button):
            confirm_button.disabled = False
            confirm_button.label = "✅ 确认使用此协议"
        await self._render_view(interaction)

    @ui.button(label="✅ 请先选择一个协议", style=discord.ButtonStyle.success, disabled=True, custom_id="license_cog:software_licenses:confirm_button", row=1)
    async def confirm_selection(self, interaction: discord.Interaction, button: ui.Button):
        if not self.selected_license: return

        async def modal_submit_callback(modal_interaction, attribution, notes, personal_statement):
            # 不覆盖核心条款，只更新类型、署名和备注。
            # 这样可以在切换回自定义协议时保留用户之前的设置。
            # 1. 从当前配置开始，保留所有未修改的字段。
            final_details = self.config.license_details.copy()
            # 2. 只更新用户本次操作明确设置的字段。
            final_details["type"] = self.selected_license
            final_details["attribution"] = attribution
            final_details["notes"] = notes or "无"
            final_details["personal_statement"] = personal_statement or "无"
            await self.callback(modal_interaction, final_details)

        modal = AttributionNotesModal(
            default_attribution=self.config.license_details.get("attribution", f"Copyright (c) {datetime.now().year} <@{self.config.user_id}>"),
            default_notes=self.config.license_details.get("notes", "无"),
            default_personal_statement=self.config.license_details.get("personal_statement", "无"),
            final_callback=modal_submit_callback,
            is_temporary=self.is_temporary
        )
        await interaction.response.send_modal(modal)

    @ui.button(label="返回", style=discord.ButtonStyle.danger, row=2)
    async def cancel_callback(self, interaction: discord.Interaction, button: ui.Button):
        """调用“返回”回调。"""
        await safe_defer(interaction)
        await self.on_cancel(interaction)
