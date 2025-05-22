from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Callable, Awaitable, Optional

import discord
from discord.ext import commands

# ────────────────────────────────────────────────────────────────
# 本文件实现：
#   1. 统计子区内聊天记录（分批每次 100 条）。
#   2. 将统计结果缓存到 data/thread_cache/{thread_id}.json，
#      下次运行时仅增量统计。
#   3. 根据阈值移除未发言成员，若仍超阈值则移除发言最少成员。
#   4. 不移除贴主和机器人账号。
#
# 公开函数：
#   clear_thread_members(channel, threshold, bot, logger=None) -> dict
# ────────────────────────────────────────────────────────────────

# 缓存目录
_CACHE_DIR = Path("data/thread_cache")
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ------------------------- 工具函数 ----------------------------
async def _update_message_cache(
    channel: discord.Thread,
    logger=None,
    progress_cb: Optional[Callable[[int, int, Optional[discord.Member], str], Awaitable[None]]] = None,
) -> Dict[int, int]:
    """增量统计子区消息数并返回 {user_id: msg_count} 字典。"""

    cache_path = _CACHE_DIR / f"{channel.id}.json"

    # 读取历史缓存
    last_id: int = None
    message_counts: Dict[int, int] = {}

    if cache_path.exists():
        try:
            with cache_path.open("r", encoding="utf-8") as fp:
                cache = json.load(fp)
            last_id = cache.get("last_id")
            # 把 key 转回 int
            message_counts = {int(k): v for k, v in cache.get("message_counts", {}).items()}
        except Exception as e:
            if logger:
                logger.warning(f"读取缓存 {cache_path} 失败，将重新统计：{e}")
            # 若损坏则重建
            last_id = None
            message_counts = {}

    # 开始增量拉取，分批每次 100 条
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
            # 网络或权限异常，直接返回已有统计
            if logger:
                logger.warning(f"拉取子区 {channel.id} 历史消息失败：{e}")
            break

        if not fetched:
            break  # 没有更多消息

        # 处理批次
        for msg in fetched:
            author = msg.author
            # 机器人消息不计入活跃度
            if author.bot:
                continue
            uid = author.id
            message_counts[uid] = message_counts.get(uid, 0) + 1

        # 为下一次迭代设置 after，对应本批次最后一条（时间最晚）
        after_obj = discord.Object(id=fetched[-1].id)
        last_id = fetched[-1].id  # 记录最新消息 ID

        # 进度回调
        if progress_cb:
            processed += len(fetched)
            await progress_cb(processed, 0, None, "stat_progress")

    # 保存缓存（key 转 str 以便 JSON）
    cache_data = {
        "last_id": last_id,
        "message_counts": {str(k): v for k, v in message_counts.items()},
    }
    try:
        with cache_path.open("w", encoding="utf-8") as fp:
            json.dump(cache_data, fp, ensure_ascii=False, indent=2)
    except Exception as e:
        if logger:
            logger.error(f"写入缓存 {cache_path} 失败：{e}")

    # 完成回调
    if progress_cb:
        await progress_cb(processed, 0, None, "stat_done")

    return message_counts

# ------------------------- 主功能函数 --------------------------
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
    message_counts = await _update_message_cache(channel, logger, progress_cb=progress_cb)

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

    # 如仍超过阈值，移除发言最少的成员
    if current_count > threshold:
        active_candidates = [
            m for m in members if m.id not in protected_ids and m not in to_remove
        ]
        # 按发言数升序排序（少 -> 多）
        active_candidates.sort(key=lambda m: message_counts.get(m.id, 0))
        for m in active_candidates:
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
