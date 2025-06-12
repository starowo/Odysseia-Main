# --- START OF FILE src/license/ui_factory.py ---

"""
UI 工厂 (UI Factory)

本模块提供一系列“工厂函数”，用于构建常用的、复杂的UI组件（如View和Embed）。
这些函数只负责“构建”而不负责“发送”，将构建逻辑与交互响应逻辑解耦。
调用方可以根据自身上下文（如新消息、编辑现有消息）来决定如何使用这些返回的组件。
"""
from typing import Tuple, Callable, Coroutine, Any, TYPE_CHECKING, List

import discord
from discord import Embed

from src.license.view_tool import ConfirmPostView
from .view_tool import ConfirmPostView

if TYPE_CHECKING:
    from .cog import LicenseCog
from .constants import HUB_VIEW_CONTENT, SIGNATURE_HELPER
from .database import LicenseConfig
from .database import LicenseDB


def prepare_edit_hub(
        db: LicenseDB,
        config: LicenseConfig,
        on_success_callback: Callable,
        on_cancel_callback: Callable,
        commercial_use_allowed: bool,
        owner_id: int,
        is_temporary: bool = False,
) -> Tuple[str, discord.ui.View]:
    """
    工厂函数：准备“协议编辑枢纽”所需的 View 和 content。

    Args:
        db: LicenseDB 实例。
        config: 当前用户的配置对象。
        on_success_callback: 编辑成功后应调用的最终回调函数。
        on_cancel_callback: 用户取消编辑后应调用的回调函数。
        commercial_use_allowed: 是否允许商业化许可证。
        is_temporary: 是否是为“临时编辑”场景构建。
        owner_id: 用于权限校验

    Returns:
        一个元组 (content, view)，调用方可以决定如何发送它们。
    """
    # 在这里，在函数内部进行导入
    from .modals_and_views import LicenseEditHubView
    if is_temporary:
        content = (
            "你正在为你**本次发布**编辑一个临时协议。\n"
            "这个操作**不会**更改你保存的默认协议。\n"
            f"{HUB_VIEW_CONTENT}"
        )
    else:
        content = (
            "你正在**永久编辑**你的默认协议。\n"
            "保存后，这将成为你未来的默认设置。\n"
            f"{HUB_VIEW_CONTENT}"
        )

    # 将最终的回调函数“透传”给 LicenseEditHubView
    hub_view = LicenseEditHubView(
        db=db,
        config=config,
        callback=on_success_callback,
        on_cancel=on_cancel_callback,
        commercial_use_allowed=commercial_use_allowed,
        content=content,
        is_temporary=is_temporary,
        owner_id = owner_id
    )

    return content, hub_view


from .utils import build_license_embeds, build_footer_text


async def prepare_confirmation_flow(
        cog: 'LicenseCog',
        thread: discord.Thread,
        config: LicenseConfig,
        author: discord.User,
        on_confirm_action: Callable[..., Coroutine[Any, Any, None]],
        on_cancel_action: Callable[..., Coroutine[Any, Any, None]],
) -> tuple[str, list[Embed], ConfirmPostView]:
    """
    【最终版重构】
    - 返回一个Embeds列表用于预览。
    - 预览内容包含主面板和附录。
    - 不再需要侦察历史消息。
    """
    commercial_use_allowed = cog.commercial_use_allowed

    # 1. 构建最终会发布的 Embeds 列表 (包含附录)
    #    这个 final_embeds 变量将直接传递给最终的 on_confirm_action
    final_embeds = build_license_embeds(
        config=config,
        author=author,
        commercial_use_allowed=commercial_use_allowed,
        include_appendix=True
    )

    # 2. 基于 final_embeds 创建一个专门用于预览的列表
    #    我们不直接修改 final_embeds，而是创建副本进行操作
    preview_embeds = [embed.copy() for embed in final_embeds]

    # 3. 创建独立的 content 字符串，而不是修改 description
    preview_content = (
        f"{author.mention}\n"  # Mention 用户以提醒
        "**请预览你将要发布的协议。**\n"
        "它将包含以下的主面板和一个规则附录。\n"
        "-------------------"
    )

    # 3. 对预览的主 Embed 进行“特化”处理
    if preview_embeds:  # 安全检查，确保列表不为空
        main_preview_embed = preview_embeds[0]
        # 修改标题
        main_preview_embed.title = f"🔍 预览：{main_preview_embed.title}"
        # 修改页脚，用助手的签名覆盖掉最终的协议签名
        main_preview_embed.set_footer(text=build_footer_text(SIGNATURE_HELPER))

    # 4. 创建视图和回调
    #    on_confirm_wrapper 现在直接捕获并使用上面创建的 final_embeds
    async def on_confirm_wrapper(interaction: discord.Interaction):
        await on_confirm_action(interaction, final_embeds)

    confirm_view = ConfirmPostView(
        author_id=author.id,
        on_confirm=on_confirm_wrapper,
        on_cancel=on_cancel_action
    )

    # 返回特化后的预览Embeds列表和视图
    return preview_content,preview_embeds, confirm_view