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
        if announce_channel is not None and archive_id == announce_channel.id:
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
                                       embed, get_config=get_config_value,
                                       archive_embed=None,
                                       archive_file_override=None,
                                       **kwargs):
    """Keep the original send intact; sync deliveries never enter the archive path."""
    archive_file = None
    original_file = kwargs.get("file")
    if not from_sync:
        if archive_file_override is not None:
            archive_file = archive_file_override
        elif original_file is not None:
            try:
                position = original_file.fp.tell()
                try:
                    contents = original_file.fp.read()
                finally:
                    original_file.fp.seek(position)
                archive_file = discord.File(
                    io.BytesIO(contents), filename=original_file.filename,
                    spoiler=original_file.spoiler, description=original_file.description,
                )
            except Exception:
                logging.getLogger(__name__).exception("处罚留痕附件复制失败 (guild_id=%s)", guild_id)
    try:
        send_err = None
        try:
            await channel.send(embed=embed, **kwargs)
        except Exception as exc:
            send_err = exc
            logging.getLogger(__name__).exception("处罚公示发送失败 (guild_id=%s)", guild_id)

        if not from_sync:
            await archive_punishment(
                bot, guild_id, channel,
                embed=archive_embed if archive_embed is not None else embed,
                file=archive_file,
                get_config=get_config,
            )

        if send_err is not None:
            raise send_err
    finally:
        if archive_file is not None:
            archive_file.close()
        if original_file is not None:
            try:
                original_file.close()
            except Exception:
                pass
