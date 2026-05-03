from __future__ import annotations

from typing import Dict, List, Callable, Awaitable, Optional, Tuple

import discord
from discord.ext import commands

from . import db

# 本文件实现：
#   1. 统计子区内聊天记录（分批每次 100 条），同时记录成员最后活跃时间
#   2. 将统计结果缓存到 SQLite 数据库，下次运行时仅增量统计
#   3. 根据阈值移除未发言成员，若仍超阈值则按活跃度移除：
#      - 有 last_active 时间戳的成员：按最后活跃时间升序（最久未活跃优先移除）
#      - 无 last_active 的旧数据成员：按发言数量升序（最少优先移除），优先于有时间戳的成员
#   4. 不移除贴主和机器人账号
#
# 公开函数：
#   clear_thread_members(channel, threshold, bot, logger=None) -> dict

# 工具函数
async def _update_message_cache(
    channel: discord.Thread,
    logger=None,
    progress_cb: Optional[Callable[[int, int, Optional[discord.Member], str], Awaitable[None]]] = None,
) -> Tuple[Dict[int, int], Dict[int, str]]:
    """增量统计子区消息并返回 (message_counts, last_active) 元组。

    message_counts : {user_id: msg_count}
    last_active    : {user_id: ISO 时间戳字符串}  — 成员最后发言时间
                     旧缓存中不存在此字段时为空 dict，后续增量拉取逐步填充。
    """

    last_id, message_counts, last_active = await db.load_thread_cache(channel.id)

    after_obj = discord.Object(id=last_id) if last_id else None

    processed = 0

    if progress_cb:
        await progress_cb(0, 0, None, "stat_start")

    while True:
        try:
            fetched: List[discord.Message] = [
                m async for m in channel.history(limit=100, after=after_obj, oldest_first=True)
            ]
        except discord.HTTPException as e:
            if logger:
                logger.warning(f"拉取子区 {channel.id} 历史消息失败：{e}")
            break

        if not fetched:
            break

        for msg in fetched:
            author = msg.author
            if author.bot:
                continue
            uid = author.id
            message_counts[uid] = message_counts.get(uid, 0) + 1
            last_active[uid] = msg.created_at.isoformat()

        after_obj = discord.Object(id=fetched[-1].id)
        last_id = fetched[-1].id

        if progress_cb:
            processed += len(fetched)
            await progress_cb(processed, 0, None, "stat_progress")

    try:
        await db.save_thread_cache(channel.id, last_id, message_counts, last_active)
    except Exception as e:
        if logger:
            logger.error(f"写入数据库缓存 thread_id={channel.id} 失败：{e}")

    if progress_cb:
        await progress_cb(processed, 0, None, "stat_done")

    return message_counts, last_active

# 主功能函数
async def clear_thread_members(
    channel: discord.Thread,
    threshold: int,
    bot: commands.Bot,
    logger=None,
    progress_cb: Optional[Callable[[int, int, Optional[discord.Member], str], Awaitable[None]]] = None,
) -> Dict[str, int]:
    """根据阈值清理子区成员。

    Parameters
    ----------
    channel : discord.Thread
        目标子区。
    threshold : int
        期望保留的最大成员数。
    bot : commands.Bot
        机器人实例，用于排除自身。
    logger : Logger
        可选日志记录器。
    progress_cb : Callable[[int, int, Optional[discord.Member], str], Awaitable[None]]
        进度回调函数，接受四个参数：已移除人数、总移除人数、当前移除成员、状态。

    Returns
    -------
    dict
        {
            "removed_inactive": 未发言移除人数,
            "removed_active": 低活跃移除人数,
            "final_count": 清理后成员数
        }
    """

    # 先确保有最新活跃度统计
    message_counts, last_active = await _update_message_cache(channel, logger, progress_cb=progress_cb)

    # 当前成员列表
    members: List[discord.Member] = await channel.fetch_members()
    current_count = len(members)

    # 若已低于阈值，直接返回
    if current_count <= threshold:
        if progress_cb:
            await progress_cb(0, 0, None, "done")
        return {
            "removed_inactive": 0,
            "removed_active": 0,
            "final_count": current_count,
        }

    # 贴主 & bot 不可移除
    protected_ids = {channel.owner_id, bot.user.id}

    # 统计未发言成员
    inactive_members = [
        m for m in members if message_counts.get(m.id, 0) == 0 and m.id not in protected_ids
    ]

    to_remove: List[discord.Member] = []
    # 先移除未发言成员
    for m in inactive_members:
        if current_count <= threshold:
            break
        to_remove.append(m)
        current_count -= 1

    # 如仍超过阈值，按活跃度移除成员
    # 渐进策略：有 last_active 时间戳的按时间排序，没有的按发言数量排序
    if current_count > threshold:
        active_candidates = [
            m for m in members if m.id not in protected_ids and m not in to_remove
        ]

        no_timestamp = [m for m in active_candidates if m.id not in last_active]
        has_timestamp = [m for m in active_candidates if m.id in last_active]

        no_timestamp.sort(key=lambda m: message_counts.get(m.id, 0))
        has_timestamp.sort(key=lambda m: last_active[m.id])

        for m in no_timestamp + has_timestamp:
            if current_count <= threshold:
                break
            to_remove.append(m)
            current_count -= 1

    # 在开始移除前触发开始回调
    if progress_cb:
        await progress_cb(0, len(to_remove), None, "start")

    # 开始批量移除
    removed_inactive = 0
    removed_active = 0

    for idx, member in enumerate(to_remove, start=1):
        try:
            await channel.remove_user(member)
            if message_counts.get(member.id, 0) == 0:
                removed_inactive += 1
            else:
                removed_active += 1
        except discord.HTTPException as e:
            # 移除失败时写日志但继续
            if logger:
                logger.warning(f"无法移除成员 {member}：{e}")
        finally:
            if progress_cb:
                await progress_cb(idx, len(to_remove), member, "progress")

    # 尝试获取最新成员数，若失败就使用 current_count
    try:
        # Thread.member_count 仅在调用 .fetch() 后刷新；这里做个保险
        members = await channel.fetch_members()  # type: ignore[attr-defined]
        final_count = len(members)
    except Exception:
        final_count = current_count

    # 结束回调
    if progress_cb:
        await progress_cb(len(to_remove), len(to_remove), None, "done")

    return {
        "removed_inactive": removed_inactive,
        "removed_active": removed_active,
        "final_count": final_count,
    }
