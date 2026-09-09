import asyncio
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from src.admin.cog import AdminCommands
from src.post_filter.cog import PostFilterCog


class MetadataOnlyMessage(SimpleNamespace):
    @property
    def content(self):
        raise AssertionError("message body must not be read")

    @property
    def embeds(self):
        raise AssertionError("message embeds must not be read")

    @property
    def attachments(self):
        raise AssertionError("message attachments must not be read")


def audit_line(message):
    return f"message_id={message.id} author_id={message.author.id} created_at={message.created_at.isoformat()}\n"


def moderation_context(count=205, author_id=123, fail_upload=False):
    channel = MagicMock()
    channel.id = 10
    channel.mention = "#review"
    base = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    messages = [
        MetadataOnlyMessage(
            id=i + 1,
            created_at=base + datetime.timedelta(milliseconds=i // 3),
            channel=channel,
            author=SimpleNamespace(name="ReviewUser", id=author_id),
        )
        for i in range(count)
    ]
    remaining = list(messages)
    captures = []
    streams = []

    async def fetch_message(message_id):
        return messages[message_id - 1]

    async def history(*, limit, after, before, oldest_first=False):
        selected = [
            m for m in remaining
            if (m.id > after.id if hasattr(after, "id") else m.created_at > after)
            and (m.id < before.id if hasattr(before, "id") else m.created_at < before)
        ]
        selected.sort(key=lambda m: m.id, reverse=not oldest_first)
        for message in selected[:limit]:
            yield message

    async def delete_messages(batch):
        for message in batch:
            remaining.remove(message)

    async def capture_send(*, embed, files):
        streams.append(files[0].fp)
        captures.append(files[0].fp.read().decode("utf-8"))
        await asyncio.sleep(0)
        if fail_upload:
            raise RuntimeError("upload failed")

    channel.fetch_message = AsyncMock(side_effect=fetch_message)
    channel.history = history
    channel.delete_messages = AsyncMock(side_effect=delete_messages)
    log_channel = SimpleNamespace(send=AsyncMock(side_effect=capture_send))
    guild = MagicMock(spec=discord.Guild)
    guild.id = 1
    guild.get_channel_or_thread.return_value = log_channel
    interaction = SimpleNamespace(
        channel=channel,
        guild=guild,
        user=SimpleNamespace(mention="@reviewer"),
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )
    cog = object.__new__(AdminCommands)
    cog.get_guild_config = MagicMock(return_value=99)
    return cog, interaction, messages, remaining, captures, streams


async def invoke(context, start_index=0, end_index=-1):
    cog, interaction, messages, *_ = context
    await AdminCommands.bulk_delete_messages.callback(
        cog, interaction,
        f"https://discord.com/channels/1/10/{messages[start_index].id}",
        f"https://discord.com/channels/1/10/{messages[end_index].id}",
    )


@pytest.mark.asyncio
async def test_audit_keeps_all_batches_and_same_timestamp_messages_without_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    context = moderation_context()
    with patch("src.admin.cog.confirm_view", AsyncMock(return_value=True)), \
            patch("builtins.open", side_effect=AssertionError("message content written to disk")):
        await invoke(context)
    _, _, messages, remaining, captures, streams = context
    assert not remaining
    assert captures == ["".join(audit_line(m) for m in messages)]
    assert all(stream.closed for stream in streams)
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_selection_excludes_adjacent_messages_with_the_same_timestamp():
    context = moderation_context(6)
    with patch("src.admin.cog.confirm_view", AsyncMock(return_value=True)):
        await invoke(context, start_index=1, end_index=3)
    assert [m.id for m in context[3]] == [1, 5, 6]
    assert context[4] == ["".join(audit_line(m) for m in context[2][1:4])]


@pytest.mark.asyncio
async def test_concurrent_moderation_audits_do_not_share_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = moderation_context(4, author_id=123)
    second = moderation_context(4, author_id=456)
    with patch("src.admin.cog.confirm_view", AsyncMock(return_value=True)), \
            patch("builtins.open", side_effect=AssertionError("shared temporary file used")):
        await asyncio.gather(invoke(first), invoke(second))
    assert "author_id=123" in first[4][0] and "author_id=456" not in first[4][0]
    assert "author_id=456" in second[4][0] and "author_id=123" not in second[4][0]
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_legacy_import_skips_all_history_reads_without_content_intent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cog = object.__new__(AdminCommands)
    cog.bot = MagicMock()
    cog.bot.intents = SimpleNamespace(message_content=False)
    cog.logger = MagicMock()
    await cog._quiz_punish_init()
    cog.bot.get_channel.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_filter_cannot_be_enabled_without_content_intent():
    cog = object.__new__(PostFilterCog)
    cog.bot = SimpleNamespace(intents=SimpleNamespace(message_content=False))
    with pytest.raises(RuntimeError, match="消息正文读取已关闭"):
        await cog.cog_load()


@pytest.mark.asyncio
async def test_upload_failure_closes_audit_buffer_without_leaving_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    context = moderation_context(4, fail_upload=True)
    with patch("src.admin.cog.confirm_view", AsyncMock(return_value=True)), \
            patch("builtins.open", side_effect=AssertionError("message content written to disk")):
        with pytest.raises(RuntimeError, match="upload failed"):
            await invoke(context)
    assert all(stream.closed for stream in context[5])
    assert not list(tmp_path.iterdir())
