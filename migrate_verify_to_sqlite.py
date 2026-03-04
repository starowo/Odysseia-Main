"""
将 data/verify/{guild_id}/{user_id}.json 批量迁移到 SQLite 数据库。

针对数十万 JSON 文件做了如下性能优化：
  - 多进程并行解析 JSON（CPU 密集型）
  - 每 BATCH_SIZE 条用户做一次 commit，减少 I/O 阻塞
  - WAL 模式 + NORMAL 同步级别
  - 进度条实时显示

用法：
    python migrate_verify_to_sqlite.py                          # 默认路径
    python migrate_verify_to_sqlite.py --data-dir ./data/verify # 自定义数据目录
    python migrate_verify_to_sqlite.py --db ./data/verify/verify.db
    python migrate_verify_to_sqlite.py --workers 8              # 指定解析进程数
"""

import argparse
import asyncio
import json
import pathlib
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Dict, List, Optional, Tuple

# ── 批量大小 ────────────────────────────────────────────────
BATCH_SIZE = 2000


# ── 单个 JSON 解析（在子进程中执行） ───────────────────────
def _parse_json_file(path_str: str) -> Optional[Tuple[int, int, Dict]]:
    """解析一个 JSON 文件，返回 (guild_id, user_id, data) 或 None。"""
    try:
        p = pathlib.Path(path_str)
        user_id = int(p.stem)
        guild_id = int(p.parent.name)
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return guild_id, user_id, data
    except Exception:
        return None


async def migrate(data_dir: pathlib.Path, db_path: pathlib.Path, workers: int):
    # 延迟导入，这样可以在未安装 aiosqlite 时先看到帮助
    from src.verify.database import VerifyDatabase

    # ── 1. 收集所有 JSON 文件路径 ───────────────────────────
    print(f"[1/4] 扫描 {data_dir} ...")
    json_files: List[str] = []
    for guild_dir in data_dir.iterdir():
        if not guild_dir.is_dir():
            continue
        # 跳过 verify.db 所在目录的其它非文件夹内容
        for f in guild_dir.iterdir():
            if f.suffix == ".json":
                json_files.append(str(f))

    total = len(json_files)
    if total == 0:
        print("未找到任何 JSON 文件，退出。")
        return
    print(f"    找到 {total} 个用户 JSON 文件。")

    # ── 2. 多进程解析 JSON ──────────────────────────────────
    print(f"[2/4] 解析 JSON（{workers} 进程）...")
    t0 = time.perf_counter()
    parsed: List[Tuple[int, int, Dict]] = []
    errors = 0

    with ProcessPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(_parse_json_file, json_files, chunksize=256):
            if result is None:
                errors += 1
            else:
                parsed.append(result)

    t1 = time.perf_counter()
    print(f"    解析完成：{len(parsed)} 成功，{errors} 失败，耗时 {t1 - t0:.2f}s")

    # ── 3. 初始化数据库 ────────────────────────────────────
    print(f"[3/4] 初始化数据库 {db_path} ...")
    db = VerifyDatabase(db_path)
    await db.init()

    # ── 4. 批量写入 ────────────────────────────────────────
    print(f"[4/4] 写入 SQLite（每 {BATCH_SIZE} 条 commit 一次）...")
    t2 = time.perf_counter()
    done = 0
    attempt_count = 0

    for guild_id, user_id, data in parsed:
        attempts = data.get("attempts", [])
        await db.bulk_import_user(
            guild_id=guild_id,
            user_id=user_id,
            last_success=data.get("last_success"),
            timeout_until=data.get("timeout_until"),
            quiz_cooldown_until=data.get("quiz_cooldown_until"),
            attempts=attempts,
        )
        done += 1
        attempt_count += len(attempts)

        if done % BATCH_SIZE == 0:
            await db.bulk_commit()
            pct = done * 100 // len(parsed)
            print(f"    {done}/{len(parsed)} ({pct}%) ...", flush=True)

    # 最后一批
    await db.bulk_commit()
    t3 = time.perf_counter()

    await db.close()

    # ── 汇总 ───────────────────────────────────────────────
    print()
    print("=" * 50)
    print(f"迁移完成！")
    print(f"  用户数　　　　: {len(parsed)}")
    print(f"  答题记录总数　: {attempt_count}")
    print(f"  解析失败　　　: {errors}")
    print(f"  解析耗时　　　: {t1 - t0:.2f}s")
    print(f"  写入耗时　　　: {t3 - t2:.2f}s")
    print(f"  总耗时　　　　: {t3 - t0:.2f}s")
    print(f"  数据库文件　　: {db_path}")
    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="将 verify JSON 数据迁移到 SQLite")
    parser.add_argument(
        "--data-dir",
        type=pathlib.Path,
        default=pathlib.Path("data/verify"),
        help="JSON 数据根目录（默认 data/verify）",
    )
    parser.add_argument(
        "--db",
        type=pathlib.Path,
        default=pathlib.Path("data/verify/verify.db"),
        help="目标 SQLite 数据库路径（默认 data/verify/verify.db）",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="JSON 解析并行进程数（默认 8）",
    )
    args = parser.parse_args()

    if not args.data_dir.exists():
        print(f"错误：数据目录 {args.data_dir} 不存在")
        sys.exit(1)

    if args.db.exists():
        print(f"警告：数据库 {args.db} 已存在，新数据将追加/覆盖。")
        resp = input("是否继续？[y/N] ").strip().lower()
        if resp != "y":
            print("已取消。")
            sys.exit(0)

    asyncio.run(migrate(args.data_dir, args.db, args.workers))


if __name__ == "__main__":
    main()
