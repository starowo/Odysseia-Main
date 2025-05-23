```markdown
# Odysseia Discord 机器人

一个功能强大的 Discord 机器人，专为类脑服务器管理而设计。

## 主要功能

### 子区管理功能
- **子区清理**：自动清理不活跃成员，优先移除未发言成员
- **消息管理**：删除指定消息
- **子区操作**：
  - 删除整个子区
  - 锁定/解锁子区
  - 设置慢速模式（支持多种时间间隔，从无到6小时）
  - 标注/取消标注重要消息

### 管理员工具
- **成员管理**：
  - 禁言成员（支持设置时长：分钟、小时、天，最长28天；可附加警告天数）
  - 永久封禁成员
  - 撤销处罚
  - 答题处罚（移除身份组送往答题区）
- **身份组管理**：批量转移身份组
- **频道管理**：修改频道名称、慢速模式、NSFW设置、自动归档等
- **子区管理**：管理员可直接锁定、解锁、归档、取消归档、置顶、取消置顶和删除子区
- **批量删除消息**: 在指定范围内删除消息

### 机器人管理
- **模块管理**：列出、启用、禁用和重载功能模块
- **状态检查**：测试机器人响应时间

### 其他特性
- **实时日志**：支持通过Discord嵌入式消息(Embed)查看机器人日志
- **完善的错误处理**：详细的错误日志记录
- **处罚记录**：自动保存处罚记录并支持公示

## 0. 准备工作：创建并配置Discord机器人

你不需要新建一个账号。
在运行本项目之前，你需要前往Discord开发者平台创建一个机器人应用程序，并获取其令牌（Token）和客户端ID，以及配置必要的权限。

### 1. 创建Discord应用程序并获取令牌

1.  **前往开发者平台**: 打开 [Discord开发者门户](https://discord.com/developers/applications/me) 并登录你的Discord账号。
2.  **创建新应用程序**: 点击页面右上角的 **“New Application”**按钮（你可能需要通过一个人机验证，如果持续加载，检查网络环境或许有效）。
3.  **命名应用程序**: 为你的应用程序输入一个名称（例如 `Odysseia`），然后点击 **“Create”** 或 **“确定”**。
4.  **添加机器人**: 在左侧菜单中，选择 **“Bot”** 选项卡。然后点击 **“Add Bot”** ，并在弹出的确认窗口中确认。
5.  **获取机器人令牌 (Token)**: 在“Bot”页面，找到 **“TOKEN”** 部分。点击 **“Copy”** 按钮复制你的机器人令牌。
    *   **重要提示**: **请务必妥善保管此令牌！** 它相当于你机器人的密码，泄露可能导致他人登录并控制你的机器人。如果不慎泄露，你可以在此页面重新生成令牌。

### 2. 配置机器人权限 (Intents)

在“Bot”页面，向下滚动到 **“Privileged Gateway Intents”**（特权网关意图）部分。为了确保机器人能正常工作并接收到所有必要的事件，请开启以下选项：
*   **PRESENCE INTENT**: 允许机器人接收用户在线状态和活动信息。
*   **SERVER MEMBERS INTENT**: 允许机器人接收服务器成员信息（例如成员加入或离开）。
*   **MESSAGE CONTENT INTENT**: 允许机器人接收消息内容。对于处理用户命令至关重要。
    *   **注意**: 对于在少于100个服务器中运行的未验证机器人，可以直接在此处启用特权意图。如果机器人已验证或即将需要验证，则可能需要申请这些特权意图。

### 3. 将机器人邀请到你的服务器

机器人不能像普通用户一样自行加入服务器，需要通过邀请链接 。

1.  **获取客户端ID**: 在左侧菜单中，选择 **“OAuth2”** 选项卡。然后点击 **“General”** 子选项卡，找到 **“CLIENT ID”** 并复制它。客户端ID是公开的，无需保密。
2.  **生成邀请链接**:
    *   在 **“OAuth2”** 选项卡下，选择 **“URL Generator”** 。
    *   在 **“SCOPES”** 部分，勾选 **`bot`**  和 **`applications.commands`** （用于斜杠命令）。
    *   在 **“BOT PERMISSIONS”** 部分，根据你的需求选择机器人所需的权限 。对于本项目的机器人，推荐选择以下权限以确保所有功能正常运行：
        *   `Manage Channels` (管理频道)
        *   `Manage Roles` (管理身份组)
        *   `Kick Members` (踢出成员)
        *   `Ban Members` (封禁成员)
        *   `Timeout Members` (禁言成员)
        *   `Manage Messages` (管理消息)
        *   `Read Message History` (读取消息历史)
        *   `Send Messages` (发送消息)
        *   `Embed Links` (嵌入链接)
        *   `Attach Files` (附加文件)
        *   `Use External Emojis` (使用外部表情)
        *   `Add Reactions` (添加反应)
        *   `Manage Threads` (管理子区)
        *   `Read Message History` (读取消息历史)
        *   `Send Messages in Threads` (在子区中发送消息)
        *   `Create Public Threads` (创建公共子区)
        *   `Create Private Threads` (创建私人子区)
    *   **注意**: 避免直接勾选 `Administrator` 权限，除非你完全了解其风险 。
    *   完成权限选择后，在页面底部 **“GENERATED URL”** 部分，点击右侧的 **“Copy”** 按钮复制生成的邀请链接 。
3.  **邀请机器人**: 将复制的邀请链接粘贴到你的浏览器中打开 。选择你想要添加机器人的服务器，然后点击 **“Authorize”** 或 **“授权”** 。
    *   **注意**: 只有拥有 `Manage Server` 权限的服务器所有者或管理员才能邀请机器人 。如果列表中没有显示你的服务器，你可能需要先创建一个服务器 。

完成以上步骤后，你的Discord机器人就已创建并成功加入你的服务器，并拥有了运行本项目所需的令牌和权限。

## 安装与配置

### 系统要求
- Python 3.10 或更高版本

### 安装步骤
1. 克隆仓库：
```bash
git clone https://github.com/yourusername/Odysseia-Main.git
cd Odysseia-Main
```

2. 创建并激活虚拟环境：
```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux/macOS
source .venv/bin/activate
```

3. 安装依赖：
```bash
pip install -r requirements.txt
```

### 配置机器人
1. 创建或编辑 `config.json` 文件：
```json
{
    "token": "你的Discord机器人令牌",
    "logging": {
        "enabled": true,
        "guild_id": 123456789,  // 日志服务器ID
        "channel_id": 123456789,  // 日志频道ID
        "level": "INFO"  // 日志级别：DEBUG, INFO, WARNING, ERROR, CRITICAL
    },
    "cogs": {
        "thread_manage": {
            "enabled": true,
            "description": "子区自助管理功能"
        },
        "admin": {
            "enabled": true,
            "description": "管理员功能"
        },
        "bot_manage": {
            "enabled": true,
            "description": "机器人自身管理功能"
        }
    },
    "admins": [
        "123456789"  // 管理员用户ID
    ],
    "prefix": "!",  // 命令前缀
    "status": "watching",  // 机器人状态：playing, watching, listening
    "status_text": "子区里的一切",  // 状态文本
    "quiz_role_id": 123456789,  // 答题身份组ID
    "punish_announce_channel_id": 123456789,  // 处罚公告频道ID
    "warned_role_id": 123456789 // 警告身份组ID，用于标记被警告成员
}
```

## 启动机器人
```bash
python main.py
```

## 指令列表

### 子区自助管理指令
- `/自助管理 清理子区 [阈值 (默认900，范围800-1000)]` - 清理子区不活跃成员
- `/自助管理 删除消息 [消息链接]` - 删除指定消息
- `/自助管理 删帖` - 删除整个子区
- `/自助管理 锁定子区 [原因]` - 锁定子区，禁止发言
- `/自助管理 解锁子区` - 解锁子区，允许发言
- `/自助管理 慢速模式 [时间 (选项: 无, 5秒, 10秒, 15秒, 30秒, 1分钟, 5分钟, 10分钟, 15分钟, 30分钟, 1小时, 2小时, 6小时)]` - 设置发言间隔时间
- `/自助管理 标注 [操作] [消息链接]` - 标注/取消标注消息

### 管理员指令
- `/管理 身份组 [成员] [操作] [身份组] [原因]` - 添加/移除身份组
- `/管理 批量删除消息 [开始消息链接] [结束消息链接]` - 在当前频道，从指定消息开始到指定消息结束，删除全部消息
- `/管理 禁言 [成员] [时长 (例如: 5m, 12h, 3d, 最大28天)] [原因] [图片] [警告天数 (可选)]` - 禁言成员
- `/管理 永封 [成员] [原因] [图片] [删除消息天数 (0-7天)]` - 永久封禁成员
- `/管理 撤销处罚 [处罚ID] [原因]` - 撤销处罚
- `/管理 批量转移身份组 [原身份组] [新身份组] [移除原身份组]` - 批量转移身份组
- `/管理 频道管理 [频道] [新名称] [慢速模式] [nsfw] [自动归档 (仅限论坛/子区)]` - 编辑频道属性
- `/管理 子区管理 锁定 [thread]` - 锁定线程
- `/管理 子区管理 解锁 [thread]` - 解锁线程
- `/管理 子区管理 archive [thread]` - 归档线程
- `/管理 子区管理 unarchive [thread]` - 取消归档线程
- `/管理 子区管理 pin [thread]` - 置顶线程
- `/管理 子区管理 unpin [thread]` - 取消置顶
- `/管理 子区管理 删帖 [thread]` - 删除线程
- `/答题处罚 [成员]` - 移除身份组送往答题区

### 机器人管理指令
- `/bot管理 模块列表` - 列出所有可用模块及其状态
- `/bot管理 启用模块 [module_name]` - 启用指定模块
- `/bot管理 禁用模块 [module_name]` - 禁用指定模块
- `/bot管理 重载模块 [module_name]` - 重载指定模块
- `/bot管理 ping` - 测试机器人响应时间

## 目录结构
```
Odysseia-Main/
├── main.py          # 主程序入口
├── config.json      # 配置文件
├── requirements.txt # 依赖列表
└── src/             # 源代码目录
    ├── admin/       # 管理员功能
    ├── bot_manage/  # 机器人管理功能
    ├── thread_manage/ # 子区管理功能
    └── utils/       # 工具函数
```

## 许可证
本项目使用MIT与Commons Clause混合许可证 - 详情请查看 LICENSE 文件
```
