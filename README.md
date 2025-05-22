# Odysseia Discord 机器人

一个功能强大的 Discord 机器人，专为类脑服务器管理而设计。

## 主要功能

### 子区管理功能
- **子区清理**：自动清理不活跃成员，优先移除未发言成员
- **消息管理**：删除指定消息
- **子区操作**：
  - 删除整个子区
  - 锁定/解锁子区
  - 设置慢速模式（5秒到1分钟）
  - 标注/取消标注重要消息

### 管理员工具
- **成员管理**：
  - 禁言成员（支持设置天数）
  - 永久封禁成员
  - 撤销处罚
  - 答题处罚（移除身份组送往答题区）
- **身份组管理**：批量转移身份组
- **频道管理**：修改频道名称、慢速模式、NSFW设置等
- **子区管理**：管理员可直接锁定、解锁、归档、取消归档、置顶、取消置顶和删除子区

### 机器人管理
- **模块管理**：列出、启用、禁用和重载功能模块
- **状态检查**：测试机器人响应时间

### 其他特性
- **实时日志**：支持通过Discord嵌入式消息(Embed)查看机器人日志
- **完善的错误处理**：详细的错误日志记录
- **处罚记录**：自动保存处罚记录并支持公示

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
        }
    },
    "admins": [
        "123456789"  // 管理员用户ID
    ],
    "prefix": "!",  // 命令前缀
    "status": "watching",  // 机器人状态：playing, watching, listening
    "status_text": "子区里的一切",  // 状态文本
    "quiz_role_id": 123456789,  // 答题身份组ID
    "punish_announce_channel_id": 123456789  // 处罚公告频道ID
}
```

## 启动机器人
```bash
python main.py
```

## 指令列表

### 子区自助管理指令
- `/自助管理 清理子区 [阈值]` - 清理子区不活跃成员
- `/自助管理 删除消息 [消息链接]` - 删除指定消息
- `/自助管理 删帖` - 删除整个子区
- `/自助管理 锁定子区 [原因]` - 锁定子区，禁止发言
- `/自助管理 解锁子区` - 解锁子区，允许发言
- `/自助管理 慢速模式 [时间]` - 设置发言间隔时间
- `/自助管理 标注 [操作] [消息链接]` - 标注/取消标注消息

### 管理员指令
- `/管理 禁言 [成员] [天数] [原因] [图片]` - 禁言成员
- `/管理 永封 [成员] [原因] [图片]` - 永久封禁成员
- `/管理 撤销处罚 [处罚ID] [原因]` - 撤销处罚
- `/管理 批量转移身份组 [原身份组] [新身份组] [移除原身份组]` - 批量转移身份组
- `/管理 频道管理 [频道] [新名称] [慢速模式] [nsfw] [auto_archive]` - 编辑频道属性
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