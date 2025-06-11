# --- 全局常量与配置 ---

# 用于在清理历史消息时识别本机器人发出的交互面板
SIGNATURE_HELPER = "授权协议助手"

# 【新增】用于在已发布的最终协议中留下一个机器可读的“指纹”
SIGNATURE_LICENSE = "协议由授权助手生成"

# Creative Commons 协议的标准化数据。这是所有CC协议信息的“唯一真实来源”。
# 程序在生成CC协议Embed时会强制使用这里的数据，以保证协议的准确性。
# 格式：{ "显示名称": {"reproduce": "转载条款", "derive": "演绎条款", "commercial": "商业用途条款", "url": "官方协议链接"} }
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

# 协议编辑中心的通用说明文本，方便在多处复用。
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

# 在代码中激活一套配置。当前选择中文版。
ACTIVE_COMMAND_CONFIG = COMMAND_CONFIG_ZH