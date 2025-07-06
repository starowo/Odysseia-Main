import logging
from datetime import datetime

import discord
from discord.ext import commands
import asyncio


# 日志设置
class SingleEmbedLogHandler(logging.Handler):
    """将日志集中写入指定频道中的同一个 Embed 消息 (最多 100 行)"""

    def __init__(self, bot: commands.Bot, guild_id: int, channel_id: int, max_lines: int = 100):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.max_lines = max_lines

        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task = None
        self._message: discord.Message = None
        self._lines: list[str] = []
        self._initialized = False
        self._last_update = 0
        self._update_interval = 10

    async def setup(self):
        self._task = asyncio.create_task(self._worker())

    def emit(self, record: logging.LogRecord):
        line = self.format(record)
        asyncio.create_task(self._queue.put(line))

    async def _worker(self):
        await self.bot.wait_until_ready()
        await self._ensure_message()
        self._initialized = True

        while True:
            try:
                lines_batch = []
                line = await self._queue.get()
                lines_batch.append(line)
                
                try:
                    while len(lines_batch) < 10:
                        extra_line = self._queue.get_nowait()
                        lines_batch.append(extra_line)
                except asyncio.QueueEmpty:
                    pass
                
                for log_line in lines_batch:
                    ts = datetime.now().strftime('%H:%M:%S')
                    self._lines.append(f"[{ts}] {log_line}")
                    if len(self._lines) > self.max_lines:
                        self._lines = self._lines[-self.max_lines:]
                
                current_time = asyncio.get_event_loop().time()
                if (self._initialized and self._message and 
                    current_time - self._last_update >= self._update_interval):
                    await self._edit_message()
                    self._last_update = current_time
                    
                for _ in lines_batch:
                    self._queue.task_done()
                    
                await asyncio.sleep(2)
                
            except Exception as e:
                print(f"日志处理器错误: {e}")
                try:
                    for _ in lines_batch:
                        self._queue.task_done()
                except:
                    pass
                await asyncio.sleep(5)

    async def _ensure_message(self):
        guild = self.bot.get_guild(self.guild_id)
        channel = guild.get_channel(self.channel_id) if guild else None
        if channel is None:
            raise RuntimeError("无法找到日志频道，请检查配置 guild_id / channel_id")

        pinned = await channel.pins()
        for msg in pinned:
            if msg.author == self.bot.user and msg.embeds and msg.embeds[0].title == 'Bot Logs':
                self._message = msg
                break

        if self._message is None:
            embed = discord.Embed(title='Bot Logs', description='(暂无日志)', color=discord.Color.green())
            self._message = await channel.send(embed=embed)
            try:
                await self._message.pin()
            except discord.HTTPException:
                pass

    async def _edit_message(self):
        if self._message is None:
            return

        try:
            desc = "```\n" + "\n".join(self._lines[-50:]) + "\n```"
            if len(desc) > 4000:
                desc = desc[-4000:]

            embed = self._message.embeds[0].copy()
            embed.description = desc
            embed.timestamp = datetime.now()

            await self._message.edit(embed=embed)
        except discord.HTTPException as e:
            if e.code == 30046:
                print("日志消息编辑次数超限，创建新消息")
                try:
                    await self._message.unpin()
                except:
                    pass
                
                channel = self._message.channel
                embed = discord.Embed(title='Bot Logs', description='```\n' + '\n'.join(self._lines[-50:]) + '\n```', color=discord.Color.green())
                embed.timestamp = datetime.now()
                
                self._message = await channel.send(embed=embed)
                try:
                    await self._message.pin()
                except:
                    pass
            else:
                print(f"更新日志消息失败: {e}")
        except Exception as e:
            print(f"更新日志消息时出现未知错误: {e}")