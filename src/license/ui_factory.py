# --- START OF FILE src/license/ui_factory.py ---

"""
UI 工厂 (UI Factory)

本模块提供一系列“工厂函数”，用于构建常用的、复杂的UI组件（如View和Embed）。
这些函数只负责“构建”而不负责“发送”，将构建逻辑与交互响应逻辑解耦。
调用方可以根据自身上下文（如新消息、编辑现有消息）来决定如何使用这些返回的组件。
"""
from typing import Tuple, Callable, Coroutine, Any, TYPE_CHECKING

import discord

from .tool_view import ConfirmPostView

if TYPE_CHECKING:
    from .cog import LicenseCog
from .constants import HUB_VIEW_CONTENT, SIGNATURE_HELPER
from .database import LicenseConfig
from .database import LicenseDB
from .utils import build_license_embed, build_footer_text


def prepare_edit_hub(
        db: LicenseDB,
        config: LicenseConfig,
        on_success_callback: Callable,
        on_cancel_callback: Callable,
        commercial_use_allowed: bool,
        is_temporary: bool = False
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
        commercial_use_allowed=commercial_use_allowed
    )

    return content, hub_view


async def prepare_confirmation_flow(
        cog: 'LicenseCog',  # 【核心】接收 Cog 实例
        thread: discord.Thread,
        config: LicenseConfig,
        author: discord.User,
        on_confirm_action: Callable[..., Coroutine[Any, Any, None]],
        on_cancel_action: Callable[..., Coroutine[Any, Any, None]],
) -> Tuple[discord.Embed, discord.ui.View]:
    """
    【最终完美版】工厂函数：通过接收 Cog 实例来获取所有必要的上下文。
    """
    # 【核心】现在直接从 Cog 实例中获取商业化状态和调用侦察方法
    commercial_use_allowed = cog.commercial_use_allowed
    is_reauthorization = await cog._find_existing_license_message(thread) is not None

    final_embed = build_license_embed(config, author, commercial_use_allowed)
    if is_reauthorization:
        preview_header = (
            "**请预览你将要发布的【新】协议。**\n"
            "确认后，此协议将适用于你**接下来**在本帖中发布的内容。旧有内容的授权保持不变。\n"
        )
    else:
        preview_header = (
            "**请预览你将要发布的【首次】协议。**\n"
            "确认后，此协议将适用于本帖中**已发布和未来发布的所有内容**，除非后续有新的协议替代或你另有说明。\n"
        )

    # 准备预览 Embed
    preview_embed = final_embed.copy()
    preview_embed.title = f"🔍 预览：{preview_embed.title}"
    preview_embed.set_footer(text=build_footer_text(SIGNATURE_HELPER))

    # 组合引导语和实际内容
    full_header = f"{preview_header}-------------------\n\n"
    preview_embed.description = full_header + (final_embed.description or "")

    # 【解耦】将最终的发布逻辑包装在 on_confirm 回调中
    async def on_confirm_wrapper(interaction: discord.Interaction):
        # 这个 wrapper 接收真实的 interaction，然后调用我们传入的最终动作
        await on_confirm_action(interaction, final_embed)

    # 创建确认视图，并把包装好的回调传进去
    confirm_view = ConfirmPostView(
        author_id=author.id,
        on_confirm=on_confirm_wrapper,
        on_cancel=on_cancel_action
    )

    return preview_embed, confirm_view
