import discord

dm_bot: discord.Client = None

async def init_dm_bot(token: str):
    # init dm bot
    global dm_bot
    intents = discord.Intents.none()
    intents.dm_messages = True
    intents.members = True
    intents.guilds = True
    intents.integrations = True
    dm_bot = discord.Client(intents=intents)
    await dm_bot.login(token)
    await dm_bot.connect()
    return dm_bot

async def send_dm(guild: discord.Guild, user: discord.User, message: str = None, embed: discord.Embed = None):
    # send dm using another bot
    if dm_bot is None:
        raise Exception("DM bot 未初始化")
    
    # 获取dm_bot在公会中的实例
    dm_guild = dm_bot.get_guild(guild.id)
    if dm_guild is None:
        raise Exception(f"DM bot 不在服务器 {guild.name} 中")
    
    # 获取用户在公会中的成员对象
    member = dm_guild.get_member(user.id)
    if member is None:
        raise Exception(f"无法在服务器 {guild.name} 中找到用户 {user.display_name}")
    
    # 发送私信
    await member.send(content=message, embed=embed)