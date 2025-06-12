# --- 辅助函数 ---
import asyncio
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


def build_settings_embed(config: LicenseConfig) -> discord.Embed:
    """
    【新增】工厂函数：创建一个包含所有配置项及其详细解释的设置面板Embed。
    """
    description_parts = []

    # 1. 机器人总开关
    enabled_emoji = "✅ 启用" if config.bot_enabled else "❌ 禁用"
    description_parts.append(f"**机器人总开关**: {enabled_emoji}")
    description_parts.append(
        "> 控制机器人在你发新帖时是否会自动出现。关闭后，你需要使用 `/内容授权 打开面板` 手动召唤我。"
    )
    description_parts.append("---")

    # 2. 自动发布默认协议
    auto_post_emoji = "✅ 启用" if config.auto_post else "❌ 禁用"
    description_parts.append(f"**自动发布默认协议**: {auto_post_emoji}")
    description_parts.append(
        "> 启用后，当机器人出现时，将直接尝试发布你的默认协议，而不会显示一系列交互按钮让你选择。"
    )
    description_parts.append("---")

    # 3. 发布前二次确认
    confirm_emoji = "✅ 启用" if config.require_confirmation else "❌ 禁用"
    description_parts.append(f"**发布前二次确认**: {confirm_emoji}")
    description_parts.append(
        "> 启用后，在发布任何协议前（包括自动发布），都会先让你预览并点击确认。"
    )

    description_parts.append("\n完成后，点击下方的“关闭面板”即可。（不关也行，保存是实时的，就是不够优雅，懂吧？）")

    # 使用我们现有的标准助手Embed框架来创建
    return create_helper_embed(
        title="⚙️ 机器人设置详解",
        description="\n".join(description_parts),
        color=discord.Color.blurple()
    )


def create_helper_embed(title: str, description: str, color: discord.Color = discord.Color.blue()) -> discord.Embed:
    """
    【新增】工厂函数：创建一个标准的、带有助手签名的交互面板Embed。
    这确保了所有中间状态的交互消息都能被正确识别和清理。
    """
    embed = discord.Embed(
        title=title,
        description=description,
        color=color
    )
    embed.set_footer(text=build_footer_text(SIGNATURE_HELPER))
    return embed

async def safe_delete_original_response(interaction: discord.Interaction, sleep_time: int = 0) -> None:
    if sleep_time > 0:
        await asyncio.sleep(sleep_time)
    try:
        await interaction.delete_original_response()
    except discord.NotFound:
        pass  # 如果用户在此期间关闭了，也无妨


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
    is_cc_license = license_type in CC_LICENSES

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
    if is_cc_license:
        display_details.update(CC_LICENSES[license_type])

    description_parts = []
    description_parts.append(f"**发布者: ** {author.mention}")

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
            f"\n\n**📝 附加说明**  (如无另外声明，其效力范围同本协议)\n"
            f"-------------------\n"
            f"{formatted_notes}"
        )
        description_parts.append(notes_section)

    # 3. 创建 Embed 并组合描述
    embed = discord.Embed(
        title=f"📜 内容授权协议",
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

    embed.add_field(name="✒️ 作者署名", value=_format_links_in_text(display_details.get("attribution", "未设置")), inline=False)
    embed.add_field(name="🔁 二次传播", value=_format_links_in_text(display_details.get("reproduce", "未设置")), inline=True)
    embed.add_field(name="🎨 二次创作", value=_format_links_in_text(display_details.get("derive", "未设置")), inline=True)
    embed.add_field(name="💰 商业用途", value=_format_links_in_text(display_details.get("commercial", "未设置")), inline=True)

    # 5. 添加“协议生效规则”字段
    effectiveness_rules = (
        f"1. **定义**：这是由「{SIGNATURE_HELPER}」生成的通用内容授权协议，下文简称为**“本协议”**。\n"
        "2. **效力范围（“时间段”）**：\n"
        "> **截断与起始**：本协议的发布，将**截断**并取代任何更早发布的“本协议”对**未来内容**的效力。本协议的效力从其**发布时**开始。\n"
        "> **向前追溯**：**如果**在本协议之前**不存在**其他“本协议”，则本协议的效力将**向前追溯**，覆盖从帖子建立（1楼）开始、所有未被单独授权的内容。\n"
        "3. **效力层级（谁说了算）**：\n"
        "> **最高层级**：创作者（即本帖所有者）在本帖内发表的任何**亲口声明**（例如在任意楼的全局规定、附加条款、“本协议”附加说明中的内容），其法律效力**永远高于**“本协议”。\n"
        "> **冲突解决**：若“本协议”条款与创作者的亲口声明冲突，以**创作者的声明**为准。"
    )
    embed.add_field(
        name="⚖️ 协议生效规则",
        value=effectiveness_rules,
        inline=False
    )

    # 6. 如果选择了CC协议，则添加CC免责声明字段
    if is_cc_license:
        cc_disclaimer = (
            "若创作者通过“附加说明”或亲口声明，为本协议附加了额外条款，则本授权**可能不再被视为一份标准的CC协议**。\n"
            "届时，本协议将被理解为一份包含所有上述条款（署名、二创、转载、商用等）的**自定义协议**，CC协议链接仅供参考。"
        )
        embed.add_field(
            name="⚠️ 关于CC协议的特别说明",
            value=cc_disclaimer,
            inline=False
        )

    # 5. 设置页脚
    embed.set_footer(text=build_footer_text(SIGNATURE_LICENSE))

    return embed
