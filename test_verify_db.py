"""
verify 模块 SQLite 数据库层测试脚本。

测试覆盖：
  - 表创建与初始化
  - 用户数据 CRUD（attempt / timeout / cooldown）
  - 近期失败次数统计逻辑
  - 题目缓存读写清除
  - 批量导入（迁移脚本使用的接口）
  - 与旧 JSON 格式的数据一致性

用法：
    python test_verify_db.py            # 运行全部测试
    python test_verify_db.py -v         # 详细输出
"""

import asyncio
import datetime
import json
import os
import pathlib
import tempfile
import unittest

# 保证从项目根目录导入
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from src.verify.database import VerifyDatabase


def _run(coro):
    """在同步测试中运行异步协程的辅助函数。"""
    return asyncio.get_event_loop().run_until_complete(coro)


class TestVerifyDatabase(unittest.TestCase):
    """VerifyDatabase 单元测试"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self._tmp.name) / "test_verify.db"
        self.db = VerifyDatabase(self.db_path)
        _run(self.db.init())

    def tearDown(self):
        _run(self.db.close())
        self._tmp.cleanup()

    # ── 基础：空用户查询 ─────────────────────────────────────

    def test_get_nonexistent_user(self):
        data = _run(self.db.get_user_data(111, 222))
        self.assertEqual(data["attempts"], [])
        self.assertIsNone(data["last_success"])
        self.assertIsNone(data["timeout_until"])
        self.assertIsNone(data["quiz_cooldown_until"])

    # ── 答题记录 ─────────────────────────────────────────────

    def test_save_attempt_failure(self):
        result = _run(self.db.save_user_attempt(1, 100, False))
        self.assertEqual(len(result["attempts"]), 1)
        self.assertFalse(result["attempts"][0]["success"])
        self.assertIsNone(result["last_success"])

    def test_save_attempt_success_updates_last_success(self):
        _run(self.db.save_user_attempt(1, 100, False))
        result = _run(self.db.save_user_attempt(1, 100, True))
        self.assertEqual(len(result["attempts"]), 2)
        self.assertIsNotNone(result["last_success"])
        self.assertTrue(result["attempts"][1]["success"])

    def test_attempts_ordered_by_timestamp(self):
        for _ in range(5):
            _run(self.db.save_user_attempt(1, 200, False))
        _run(self.db.save_user_attempt(1, 200, True))
        data = _run(self.db.get_user_data(1, 200))
        self.assertEqual(len(data["attempts"]), 6)
        timestamps = [a["timestamp"] for a in data["attempts"]]
        self.assertEqual(timestamps, sorted(timestamps))

    # ── Timeout ──────────────────────────────────────────────

    def test_timeout_not_set(self):
        self.assertFalse(_run(self.db.is_user_in_timeout(1, 300)))

    def test_timeout_active(self):
        _run(self.db.set_user_timeout(1, 300, 30))
        self.assertTrue(_run(self.db.is_user_in_timeout(1, 300)))

    def test_timeout_expired(self):
        _run(self.db.set_user_timeout(1, 300, 0))
        self.assertFalse(_run(self.db.is_user_in_timeout(1, 300)))

    # ── Quiz Cooldown ────────────────────────────────────────

    def test_cooldown_not_set(self):
        self.assertFalse(_run(self.db.is_user_in_quiz_cooldown(1, 400)))
        self.assertIsNone(_run(self.db.get_quiz_cooldown_remaining(1, 400)))

    def test_cooldown_active(self):
        _run(self.db.set_user_quiz_cooldown(1, 400, 60))
        self.assertTrue(_run(self.db.is_user_in_quiz_cooldown(1, 400)))
        remaining = _run(self.db.get_quiz_cooldown_remaining(1, 400))
        self.assertIsNotNone(remaining)
        self.assertGreater(remaining, 0)

    def test_cooldown_expired(self):
        _run(self.db.set_user_quiz_cooldown(1, 400, 0))
        self.assertFalse(_run(self.db.is_user_in_quiz_cooldown(1, 400)))
        self.assertIsNone(_run(self.db.get_quiz_cooldown_remaining(1, 400)))

    # ── 近期失败次数 ─────────────────────────────────────────

    def test_recent_failed_zero(self):
        count = _run(self.db.get_recent_failed_attempts(1, 500, 24))
        self.assertEqual(count, 0)

    def test_recent_failed_counts_consecutive(self):
        _run(self.db.save_user_attempt(1, 500, True))
        _run(self.db.save_user_attempt(1, 500, False))
        _run(self.db.save_user_attempt(1, 500, False))
        _run(self.db.save_user_attempt(1, 500, False))
        count = _run(self.db.get_recent_failed_attempts(1, 500, 24))
        self.assertEqual(count, 3)

    def test_recent_failed_resets_after_success(self):
        _run(self.db.save_user_attempt(1, 501, False))
        _run(self.db.save_user_attempt(1, 501, False))
        _run(self.db.save_user_attempt(1, 501, True))
        _run(self.db.save_user_attempt(1, 501, False))
        count = _run(self.db.get_recent_failed_attempts(1, 501, 24))
        self.assertEqual(count, 1)

    # ── 题目缓存 ─────────────────────────────────────────────

    def test_question_cache_empty(self):
        result = _run(self.db.get_user_questions(1, 600))
        self.assertIsNone(result)

    def test_question_cache_save_and_get(self):
        questions = [
            {"type": "single_choice", "answer": "A", "zh_cn": {"question": "测试", "choices": ["A.对", "B.错"]}},
        ]
        _run(self.db.save_user_questions(1, 600, questions))
        result = _run(self.db.get_user_questions(1, 600))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["answer"], "A")

    def test_question_cache_overwrite(self):
        q1 = [{"type": "single_choice", "answer": "A"}]
        q2 = [{"type": "fill_in_blank", "answer": "test"}]
        _run(self.db.save_user_questions(1, 601, q1))
        _run(self.db.save_user_questions(1, 601, q2))
        result = _run(self.db.get_user_questions(1, 601))
        self.assertEqual(result[0]["type"], "fill_in_blank")

    def test_question_cache_clear(self):
        _run(self.db.save_user_questions(1, 602, [{"x": 1}]))
        _run(self.db.clear_user_questions(1, 602))
        self.assertIsNone(_run(self.db.get_user_questions(1, 602)))

    # ── 批量导入 ─────────────────────────────────────────────

    def test_bulk_import(self):
        attempts = [
            {"timestamp": "2025-01-01T00:00:00+00:00", "success": False},
            {"timestamp": "2025-01-02T00:00:00+00:00", "success": True},
        ]
        _run(self.db.bulk_import_user(
            guild_id=10,
            user_id=700,
            last_success="2025-01-02T00:00:00+00:00",
            timeout_until=None,
            quiz_cooldown_until=None,
            attempts=attempts,
        ))
        _run(self.db.bulk_commit())

        data = _run(self.db.get_user_data(10, 700))
        self.assertEqual(len(data["attempts"]), 2)
        self.assertEqual(data["last_success"], "2025-01-02T00:00:00+00:00")

    def test_bulk_import_overwrite(self):
        _run(self.db.bulk_import_user(10, 701, "old", None, None, []))
        _run(self.db.bulk_import_user(10, 701, "new", None, None, []))
        _run(self.db.bulk_commit())
        data = _run(self.db.get_user_data(10, 701))
        self.assertEqual(data["last_success"], "new")

    # ── 多服务器隔离 ─────────────────────────────────────────

    def test_guild_isolation(self):
        _run(self.db.save_user_attempt(1, 800, True))
        _run(self.db.save_user_attempt(2, 800, False))

        data1 = _run(self.db.get_user_data(1, 800))
        data2 = _run(self.db.get_user_data(2, 800))

        self.assertIsNotNone(data1["last_success"])
        self.assertIsNone(data2["last_success"])
        self.assertEqual(len(data1["attempts"]), 1)
        self.assertEqual(len(data2["attempts"]), 1)


class TestMigrationConsistency(unittest.TestCase):
    """验证迁移后数据与原始 JSON 一致"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = pathlib.Path(self._tmp.name)
        self.db_path = self.tmp_path / "verify.db"
        self.db = VerifyDatabase(self.db_path)
        _run(self.db.init())

    def tearDown(self):
        _run(self.db.close())
        self._tmp.cleanup()

    def _make_json(self, guild_id: int, user_id: int, data: dict) -> pathlib.Path:
        d = self.tmp_path / "json_data" / str(guild_id)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{user_id}.json"
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return p

    def test_roundtrip(self):
        original = {
            "attempts": [
                {"timestamp": "2025-06-01T12:00:00+00:00", "success": False},
                {"timestamp": "2025-06-01T12:05:00+00:00", "success": False},
                {"timestamp": "2025-06-02T08:00:00+00:00", "success": True},
            ],
            "last_success": "2025-06-02T08:00:00+00:00",
            "timeout_until": "2025-06-01T12:30:00+00:00",
            "quiz_cooldown_until": None,
        }

        _run(self.db.bulk_import_user(
            guild_id=99,
            user_id=12345,
            last_success=original["last_success"],
            timeout_until=original["timeout_until"],
            quiz_cooldown_until=original["quiz_cooldown_until"],
            attempts=original["attempts"],
        ))
        _run(self.db.bulk_commit())

        data = _run(self.db.get_user_data(99, 12345))

        self.assertEqual(data["last_success"], original["last_success"])
        self.assertEqual(data["timeout_until"], original["timeout_until"])
        self.assertIsNone(data["quiz_cooldown_until"])
        self.assertEqual(len(data["attempts"]), len(original["attempts"]))

        for got, expected in zip(data["attempts"], original["attempts"]):
            self.assertEqual(got["timestamp"], expected["timestamp"])
            self.assertEqual(got["success"], expected["success"])

    def test_empty_user(self):
        original = {
            "attempts": [],
            "last_success": None,
            "timeout_until": None,
            "quiz_cooldown_until": None,
        }

        _run(self.db.bulk_import_user(99, 0, None, None, None, []))
        _run(self.db.bulk_commit())

        data = _run(self.db.get_user_data(99, 0))
        self.assertEqual(data["attempts"], [])
        self.assertIsNone(data["last_success"])

    def test_large_batch(self):
        """模拟批量导入 1000 个用户"""
        for uid in range(1000):
            attempts = [
                {"timestamp": f"2025-01-01T{uid % 24:02d}:00:00+00:00", "success": uid % 3 == 0}
            ]
            _run(self.db.bulk_import_user(
                guild_id=50,
                user_id=uid,
                last_success=attempts[0]["timestamp"] if uid % 3 == 0 else None,
                timeout_until=None,
                quiz_cooldown_until=None,
                attempts=attempts,
            ))
        _run(self.db.bulk_commit())

        # 抽样验证
        for uid in [0, 99, 500, 999]:
            data = _run(self.db.get_user_data(50, uid))
            self.assertEqual(len(data["attempts"]), 1)
            if uid % 3 == 0:
                self.assertIsNotNone(data["last_success"])
            else:
                self.assertIsNone(data["last_success"])


if __name__ == "__main__":
    unittest.main()
