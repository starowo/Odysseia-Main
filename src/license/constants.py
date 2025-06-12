# --- 全局常量与配置 ---

# 用于在清理历史消息时识别本机器人发出的交互面板
SIGNATURE_HELPER = "授权协议助手"

# 用于在已发布的最终协议中留下一个机器可读的“指纹”
SIGNATURE_LICENSE = "协议由授权助手生成"

# CC协议核心元素的通俗化、可复用解释
# 这是所有解释的“唯一真实来源”。
CC_ELEMENT_EXPLANATIONS = {
    "BY": "**✒️ 保留署名 (Attribution)**\n> **他人**在转载、二创等任何场景下使用**你的作品**时，都必须明确标注**你（原作者）**的名字或ID。",
    "NC": "**💰 非商业性使用 (Non-Commercial)**\n> **他人**不能将**你的作品**用于以商业盈利为主要目的的场合。例如，不能直接售卖，或用在付费才能观看的文章/视频中。",
    "SA": "**🔄 相同方式共享 (ShareAlike)**\n> 如果**他人**对**你的作品**进行了修改或二次创作，那么**他们的新作品**也必须使用与**你的作品**完全相同的CC协议进行分享。这常被称作“传染性”条款。",
    "ND": "**🚫 禁止二次创作 (NoDerivatives)**\n> **他人**不能对**你的作品**进行任何形式的修改，包括但不限于调色、裁剪、混剪、翻译等。只能原封不动地分享它。"
}

# Creative Commons 协议的标准化数据。
# 增加了 `elements` 字段，用于逐条解释。
CC_LICENSES = {
    "CC BY-NC 4.0": {
        "label": "共享 保留署名-非商业化 4.0",
        "description": "常见于同人创作圈。允许自由传播和二创，但杜绝商业行为。",
        "elements": ["BY", "NC"],
        "reproduce": "允许，但需保留署名且不得用于商业目的",
        "derive": "允许，但需保留署名且不得用于商业目的",
        "commercial": "禁止",
        "url": "https://creativecommons.org/licenses/by-nc/4.0/deed.zh-hans"
    },
    "CC BY-NC-SA 4.0": {
        "label": "共享 保留署名-非商业化-传染 4.0",
        "description": "开放共享精神的非商业版。确保**他人**的所有二创作品都保持免费和开放。",
        "elements": ["BY", "NC", "SA"],
        "reproduce": "允许，但需保留署名、非商业性使用、并以相同方式共享",
        "derive": "允许，但需保留署名、非商业性使用、并以相同方式共享",
        "commercial": "禁止",
        "url": "https://creativecommons.org/licenses/by-nc-sa/4.0/deed.zh-hans"
    },
    "CC BY-NC-ND 4.0": {
        "label": "共享 保留署名-非商业化-禁止修改 4.0",
        "description": "限制最多的协议，常被称为“仅供免费分享”。只允许你原封不动地下载和分享。",
        "elements": ["BY", "NC", "ND"],
        "reproduce": "允许，但需保留署名、非商业性使用、且不得修改",
        "derive": "禁止 (如需二创请联系作者)",
        "commercial": "禁止",
        "url": "https://creativecommons.org/licenses/by-nc-nd/4.0/deed.zh-hans"
    },
    "CC BY 4.0": {
        "label": "共享 保留署名 4.0",
        "description": "最宽松的协议之一。只要保留作者署名，你可以自由地做任何事。",
        "elements": ["BY"],
        "reproduce": "允许，但需保留作者署名",
        "derive": "允许，但需保留作者署名",
        "commercial": "允许，但需保留作者署名",
        "url": "https://creativecommons.org/licenses/by/4.0/deed.zh-hans"
    },
    "CC BY-SA 4.0": {
        "label": "共享 保留署名-传染 4.0",
        "description": "强调开放共享。**他人**的二创作品也必须以同样的开放姿态分享出去。",
        "elements": ["BY", "SA"],
        "reproduce": "允许，但需保留署名并以相同方式共享",
        "derive": "允许，但需保留署名并以相同方式共享",
        "commercial": "允许，但需保留署名并以相同方式共享",
        "url": "https://creativecommons.org/licenses/by-sa/4.0/deed.zh-hans"
    },
    "CC BY-ND 4.0": {
        "label": "共享 保留署名-禁止修改 4.0",
        "description": "保护作品的完整性。允许广泛传播，甚至商用，但不允许任何修改。",
        "elements": ["BY", "ND"],
        "reproduce": "允许，但需保留署名且不得修改",
        "derive": "禁止 (如需二创请联系作者)",
        "commercial": "允许，但需保留署名且不得修改",
        "url": "https://creativecommons.org/licenses/by-nd/4.0/deed.zh-hans"
    },
}

# 协议编辑中心的通用说明文本，方便在多处复用。
HUB_VIEW_CONTENT = (
    "请选择你希望如何设置你的授权协议：\n\n"
    "📝 **创建或编辑自定义协议**\n"
    "> 在这里，你可以完全手动控制每一项条款。最终生成的将是你独有的“自定义协议”。\n"
    "> ⚠️ **重要提醒：** \n"
    "> CC协议的核心是**鼓励分享**，因此所有标准模板都**允许他人进行二次传播（转载）**。如果你希望**【完全禁止二次传播】**，请使用本“创建或编辑自定义协议”选项。\n\n"

    "📜 **应用一个标准的CC协议**\n"
    "> 查看并从官方的 Creative Commons 协议中选择一个来应用。（选择后，可以看到不同CC协议的详细解释）\n"
    "> **注意：** 应用后，你当前的设置将被一个标准的CC协议模板所**覆盖**。\n"
    "> CC协议的核心条款是标准化的，任何附加的限制性条款都可能被视为无效。\n"
    "> 了解更多关于CC协议： https://creativecommons.org \n\n"

    "💻 **应用一个标准的软件协议 (新!)**\n"
    "> 为你的代码项目选择一个合适的开源许可证。\n\n"

    "💡 **关于“附加说明”的重要规则**\n"
    "> **如无另外说明**，你在“附加说明”中填写的条款，其**生效范围将和这份协议主体完全一致**（即，随协议的更新而“过时”）。\n"
    "> 如果你想让某条附加说明**永久有效**或拥有**特殊范围**，你**需要在其中明确写出**，例如：“**本帖所有内容**禁止商业用途。”\n\n"
)

# ============================================
#            开源软件教育平台
# ============================================

SOFTWARE_LICENSES = {
    "WTFPL": {
        "description": "“你™想干啥就干啥公共许可证”。终极的自由，已被FSF认证为兼容GPL，但未被OSI批准。",
        "url": "http://www.wtfpl.net/",
        "full_text": (
            ">>> DO WHAT THE FUCK YOU WANT TO PUBLIC LICENSE\n"
            "> Version 2, December 2004\n\n"
            "> Copyright (C) 2004 Sam Hocevar <sam@hocevar.net>\n\n"
            "> Everyone is permitted to copy and distribute verbatim or modified "
            "copies of this license document, and changing it is allowed as long "
            "as the name is changed.\n\n"
            "> DO WHAT THE FUCK YOU WANT TO PUBLIC LICENSE\n"
            "> TERMS AND CONDITIONS FOR COPYING, DISTRIBUTION AND MODIFICATION\n\n"
            "> 0. You just DO WHAT THE FUCK YOU WANT TO."
        )
    },
    "MIT": {
        "description": "最流行的宽松型许可证之一。代码可以被任意使用、修改、合并、出版、分发、再授权和/或贩卖，只需保留版权和许可声明。",
        "url": "https://opensource.org/licenses/MIT",
        "full_text": "条款很简单但还是超出了Discord的上限，所以请参考 [官方协议原文](https://opensource.org/licenses/MIT)"
    },
    "Apache-2.0": {
        "description": "一个在宽松和专利保护间取得良好平衡的许可证。除了MIT有的权限，它还明确授予了专利许可。",
        "url": "https://www.apache.org/licenses/LICENSE-2.0",
        "full_text": "条款复杂，请参考 [官方协议原文](https://www.apache.org/licenses/LICENSE-2.0)"
    },
    "GPL-3.0": {
        "description": "强大的“Copyleft”许可证。要求任何修改和分发的版本都必须以相同的GPL-3.0协议开源，保证了软件的永久自由。",
        "url": "https://www.gnu.org/licenses/gpl-3.0.html",
        "full_text": "条款极其复杂，请参考 [官方协议原文](https://www.gnu.org/licenses/gpl-3.0.html)"
    },
    "AGPL-3.0": {
        "description": "GPL的超集，专为网络服务设计。即使软件仅通过网络提供服务而未分发，也必须提供修改后的源代码。反商业闭源的终极利器。",
        "url": "https://www.gnu.org/licenses/agpl-3.0.html",
        "full_text": "条款极其复杂，请参考 [官方协议原文](https://www.gnu.org/licenses/agpl-3.0.html)"
    }
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
    "panel": {
        "name": "panel",
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
    "panel": {
        "name": "打开面板",
        "description": "在当前帖子中重新打开授权助手交互面板"
    },
    "edit": {
        "name": "编辑授权",
        "description": "创建或修改你的默认授权协议"
    },
    "settings": {
        "name": "设置助手",
        "description": "配置授权助手机器人的行为"
    },
    "show": {
        "name": "我的协议",
        "description": "查看你当前的默认授权协议"
    }
}

# 在代码中激活一套配置。当前选择中文版。
ACTIVE_COMMAND_CONFIG = COMMAND_CONFIG_ZH

MESSAGE_IGNORE = (f"{SIGNATURE_HELPER}: \n"
                  f"好的，我以后不会再主动打扰你了。\n"
                  f"你可以随时使用 `/{ACTIVE_COMMAND_CONFIG["group"]["name"]} {ACTIVE_COMMAND_CONFIG["settings"]["name"]}` 命令，在配置中重新启用我。\n"
                  f"也可以随时使用 `/{ACTIVE_COMMAND_CONFIG["group"]["name"]} {ACTIVE_COMMAND_CONFIG["panel"]["name"]}` 命令，直接调出我的主面板。\n")

MESSAGE_IGNORE_ONCE = (f"{SIGNATURE_HELPER}: \n"
                       f"好的，那我就先溜了。\n"
                       f"你可以随时使用 `/{ACTIVE_COMMAND_CONFIG["group"]["name"]}` 命令来设置你的授权协议。")
