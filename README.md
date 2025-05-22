# Discord机器人模板

这是一个基本的Discord机器人模板，采用Python和discord.py库。

## 功能特点

- 基本命令框架
- 模块化Cog系统
- 环境变量配置
- 异步支持

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

1. 在[Discord开发者门户](https://discord.com/developers/applications)创建一个新的应用
2. 在Bot选项卡中创建一个机器人
3. 复制机器人的令牌
4. 创建一个`.env`文件，并添加以下内容：
```
DISCORD_TOKEN=你的机器人令牌
```

## 运行机器人

```bash
python main.py
```

## 添加新命令

可以通过两种方式添加新命令：

1. 直接在`main.py`中添加
2. 在`src/cogs`文件夹中创建新的Cog模块（推荐）

### 创建新的Cog

1. 在`src/cogs`文件夹中创建一个新的Python文件
2. 使用以下模板：

```python
import discord
from discord.ext import commands

class YourCogName(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="your_command")
    async def your_command(self, ctx):
        """命令说明"""
        # 您的代码
        await ctx.send("回复内容")

async def setup(bot):
    await bot.add_cog(YourCogName(bot))
```

## 邀请机器人到服务器

1. 在Discord开发者门户中，导航到OAuth2 > URL Generator
2. 在"Scopes"部分选择"bot"
3. 在"Bot Permissions"部分选择所需的权限
4. 复制生成的URL并在浏览器中打开它
5. 选择要添加机器人的服务器并授权

## 主要命令

- `!ping` - 检查机器人延迟
- `!hello` - 问候消息
- `!example` - 示例Cog命令
- `!random` - 生成1-100之间的随机数