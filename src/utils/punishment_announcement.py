"""Deliver standard punishment announcements and optional original-only copies."""
import io
import logging

import discord

from src.utils.config_helper import get_config_value


async def archive_punishment(bot, guild_id, announce_channel, *, embed,
                             file=None, get_config=get_config_value):
    """Archive failures must never affect moderation or ordinary announcements."""
    try:
        archive_id = get_config("punish_archive_channel_id", guild_id, 0)
        if not archive_id:
            return
        archive_id = int(archive_id)
        if archive_id == announce_channel.id:
            return
        channel = bot.get_channel(archive_id)
        if channel is None:
            channel = await bot.fetch_channel(archive_id)
        await channel.send(embed=embed.copy(), file=file)
    except Exception:
        logging.getLogger(__name__).exception(
            "处罚留痕发送失败 (guild_id=%s)", guild_id
        )
    finally:
        if file is not None:
            file.close()


async def send_punishment_announcement(bot, guild_id, channel, *, from_sync,
                                       embed, get_config=get_config_value, **kwargs):
    """Keep the original send intact; sync deliveries never enter the archive path."""
    archive_file = None
    if not from_sync and kwargs.get("file") is not None:
        original = kwargs["file"]
        try:
            position = original.fp.tell()
            try:
                contents = original.fp.read()
            finally:
                original.fp.seek(position)
            archive_file = discord.File(
                io.BytesIO(contents), filename=original.filename,
                spoiler=original.spoiler, description=original.description,
            )
        except Exception:
            logging.getLogger(__name__).exception("处罚留痕附件复制失败 (guild_id=%s)", guild_id)
            await channel.send(embed=embed, **kwargs)
            return
    try:
        await channel.send(embed=embed, **kwargs)
        if not from_sync:
            await archive_punishment(
                bot, guild_id, channel, embed=embed, file=archive_file,
                get_config=get_config,
            )
    finally:
        if archive_file is not None:
            archive_file.close()
