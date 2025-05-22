# Discord

一个功能丰富的 Discord 机器人,主要用于管理服务器线程和提供管理员工具。

### 主要功能

#### 线程管理
- 自动追踪和记录线程内的消息数量
- 提供线程统计信息和分析
- 支持线程归档和清理

#### 管理员工具
- 日志记录和监控
- 通过嵌入式消息(Embed)集中展示机器人状态
- 支持管理员命令和权限控制

#### 其他特性
- 模块化设计,易于扩展
- 完善的错误处理和日志记录
- 支持环境变量配置

## 安装

1. 克隆这个仓库
2. 创建并激活虚拟环境（推荐）：
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

## 配置

1. 创建 `config.json` 文件
2. 在 `config.json` 中填写以下配置:

```json
{
    "token": "discord bot令牌",
    "logging": {
        "enabled": true,
        "guild_id": 123456789, // 日志服务器
        "channel_id": 123456789, // 日志频道
        "level": "INFO"
    },
    "cogs": {
        "thread_manage": {
            "enabled": true,
            "description": "子区自助管理功能"
        }
    },
    "admins": [
        "123456789" // 管理员身份组
    ],
    "prefix": "!",
    "status": "watching",
    "status_text": "子区里的一切"
}
```


## 运行机器人

```bash
python main.py
```