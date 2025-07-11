import discord

dm_bot: discord.Client = None

async def init_dm_bot(token: str):
    # init dm bot
    global dm_bot
    dm_bot = discord.Client(intents=discord.Intents.default())
    await dm_bot.login(token)
    await dm_bot.connect()
    return dm_bot

async def send_dm(user: discord.User, message: str = None, embed: discord.Embed = None):
    # send dm using another bot
    await dm_bot.get_user(user.id).send(content=message, embed=embed)