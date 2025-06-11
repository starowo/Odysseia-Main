# --- 辅助函数 ---
import re

from discord import Thread, Guild

from src.license.constants import *
from src.license.database import *


def _format_links_in_text(text: str) -> str:
    """
    【新增】一个辅助函数，用于查找文本中的裸露URL并将其转换为Markdown链接。
    例如：将 "https://example.com" 转换为 "[https://example.com](https://example.com)"
    """
    if not text:
        return text
    # 一个简单的正则表达式来匹配 http/https 链接
    url_pattern = re.compile(r'(https?://[^\s]+)')
    # 使用 re.sub 进行替换
    return url_pattern.sub(r'[\g<0>](\g<0>)', text)


async def get_member_async_thread(thread: Thread, user_id: int) -> Member | None:
    return thread.guild.get_member(user_id) or await thread.guild.fetch_member(user_id)


async def get_member_async_guild(guild: Guild, user_id: int) -> Member | None:
    return guild.get_member(user_id) or await guild.fetch_member(user_id)


def get_member(thread: Thread, user_id: int) -> discord.Member:
    return thread.guild.get_member(user_id)


def build_footer_text(signature: str) -> str:
    """
    【新增】统一的页脚文本构建器。
    它会自动附加统一的“宣传语”。

    Args:
        signature: 标识此 Embed 类型的签名，
                   如 HELPER_SIGNATURE 或 LICENSE_SIGNATURE。

    Returns:
        一个格式化好的、符合全新标准的页脚字符串。
    """
    cmd_name = ACTIVE_COMMAND_CONFIG["group"]["name"]
    cmd_name_panel = ACTIVE_COMMAND_CONFIG["panel"]["name"]
    return f"{signature} | 如果按钮失效，请使用 `/{cmd_name} {cmd_name_panel}`"


def get_available_cc_licenses(commercial_allowed: bool) -> dict:
    """
    【新增】根据服务器配置，获取可用的CC协议列表。
    这是一个“Getter”或“过滤器”。
    """
    if commercial_allowed:
        return CC_LICENSES  # 如果允许，返回全部

    # 如果禁止，则只返回名字中包含 "NC" (Non-Commercial) 的协议
    return {
        name: data for name, data in CC_LICENSES.items() if "NC" in name
    }


def build_license_embed(config: LicenseConfig, author: discord.Member, commercial_use_allowed: bool) -> discord.Embed:
    """
    根据给定的配置对象和作者信息，构建一个支持完整Markdown附加说明的美观Embed。
    """
    saved_details = config.license_details.copy()  # 使用副本以防修改原始配置对象
    license_type = saved_details.get("type", "custom")

    warning_message = None  # 用于存储将要显示的警告信息

    # --- 【核心】策略校验与自动降级逻辑 ---
    if not commercial_use_allowed:
        # 1. 对自定义协议，强制覆盖商业条款
        if license_type == "custom":
            saved_details["commercial"] = "禁止"

        # 2. 对CC协议，检查冲突并执行降级
        elif license_type in CC_LICENSES and "NC" not in license_type:
            original_license = license_type
            # 尝试找到对应的NC版本
            # 例如: "CC BY 4.0" -> "CC BY-NC 4.0"
            #       "CC BY-SA 4.0" -> "CC BY-NC-SA 4.0"
            potential_nc_version = license_type.replace("CC BY", "CC BY-NC")

            if potential_nc_version in CC_LICENSES:
                # 成功找到可降级的版本
                license_type = potential_nc_version
                saved_details["type"] = license_type
            else:
                # 如果找不到（例如对于 CC0 这种未来可能添加的），则降级为自定义
                license_type = "custom"
                saved_details["type"] = "custom"
                saved_details["commercial"] = "禁止"

            # 准备警告信息
            warning_message = (
                f"**⚠️ 协议已自动调整**\n"
                f"由于本服务器禁止商业用途，您误选择的协议 **{original_license}** "
                f"已被自动调整为 **{license_type}**。"
            )

    # --- Embed 构建流程 ---
    display_details = saved_details
    # 如果降级了，就强制使用新协议的数据
    if license_type in CC_LICENSES:
        display_details.update(CC_LICENSES[license_type])

    description_parts = []
    description_parts.append(f"**内容作者:** {author.mention}")

    if license_type != "custom":
        description_parts.append(f"本内容采用 **[{license_type}]({display_details['url']})** 国际许可协议进行许可。")

    # 【核心】如果存在警告信息，将其添加到描述中
    if warning_message:
        description_parts.append(f"\n> {warning_message}")  # 使用引用块使其更醒目

    # 3. 添加附加说明
    notes: str = display_details.get("notes")
    if notes and notes.strip() and notes != "无":
        formatted_notes = _format_links_in_text(notes)
        notes_section = (
            f"\n\n**📝 附加说明**\n"
            f"-------------------\n"
            f"{formatted_notes}"
        )
        description_parts.append(notes_section)

    # 3. 创建 Embed 并组合描述
    embed = discord.Embed(
        title=f"📜 {author.display_name} 的内容授权协议",
        description="\n".join(description_parts) if description_parts else None,
        color=discord.Color.gold() if not warning_message else discord.Color.orange()  # 警告时使用不同颜色
    )

    # 【核心变更】使用 set_author 来展示作者信息
    # 这会在 Embed 的最顶部显示作者的头像和名字
    embed.set_author(name=f"由 {author.display_name} ({author.name}) 发布", icon_url=author.display_avatar.url)

    # 4. 添加结构化的核心条款字段
    if license_type != "custom":
        embed.add_field(name="📄 协议类型", value=f"**{license_type}**", inline=False)
    else:
        embed.add_field(name="📄 协议类型", value="**自定义协议**", inline=False)

    embed.add_field(name="🔁 转载", value=_format_links_in_text(display_details.get("reproduce", "未设置")), inline=True)
    embed.add_field(name="🎨 衍生创作", value=_format_links_in_text(display_details.get("derive", "未设置")), inline=True)
    embed.add_field(name="💰 商业用途", value=_format_links_in_text(display_details.get("commercial", "未设置")), inline=True)
    embed.add_field(name="✒️ 署名要求", value=_format_links_in_text(display_details.get("attribution", "未设置")), inline=False)

    # 注意：我们不再在这里添加 '附加说明' 的 field

    # 5. 设置页脚
    embed.set_footer(text=build_footer_text(SIGNATURE_LICENSE))

    return embed
