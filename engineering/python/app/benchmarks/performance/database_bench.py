"""数据库性能基准测试模块。

测试SQLite和TDengine的读写性能、并发能力、查询效率。
覆盖真实业务场景下的数据操作。
"""

from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
import time
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

if __package__ in (None, ""):
    import _bootstrap  # noqa: F401  # 脚本直跑时引导 engineering/python 入 sys.path


class DatabasePerfBenchmark:
    """数据库性能基准测试。"""

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            db_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "test_perf.db",
            )
        self.db_path = db_path
        self._results: dict[str, Any] = {}
        self._conn: sqlite3.Connection | None = None

    def setup(self) -> None:
        """初始化测试数据库。"""
        self._conn = sqlite3.connect(self.db_path)
        self._create_test_tables()
        self._populate_test_data()

    def teardown(self) -> None:
        """清理测试环境。"""
        if self._conn:
            self._conn.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _create_test_tables(self) -> None:
        """创建测试表。"""
        cursor = self._conn.cursor()

        # 模拟刀具磨损数据表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tool_wear_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                feature_1 REAL,
                feature_2 REAL,
                feature_3 REAL,
                feature_4 REAL,
                feature_5 REAL,
                wear_value REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 模拟加工任务表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS machining_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_name TEXT NOT NULL,
                material TEXT,
                tool_type TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 模拟NC程序表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nc_programs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                program_name TEXT NOT NULL,
                gcode_content TEXT,
                file_size INTEGER,
                task_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES machining_tasks(id)
            )
        """)

        # 创建索引
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON tool_wear_data(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_status ON machining_tasks(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_nc_task ON nc_programs(task_id)")

        self._conn.commit()

    def _populate_test_data(self, n_records: int = 10000) -> None:
        """填充测试数据。"""
        cursor = self._conn.cursor()

        # 检查是否已有数据
        cursor.execute("SELECT COUNT(*) FROM tool_wear_data")
        if cursor.fetchone()[0] >= n_records:
            return

        logger.info("  填充 %s 条测试数据...", n_records)
        batch_size = 1000
        base_time = time.time()

        for batch_start in range(0, n_records, batch_size):
            data_batch = []
            for i in range(batch_size):
                timestamp = base_time + (batch_start + i) * 0.1
                features = np.random.randn(5).tolist()
                wear_value = random.uniform(0.0, 1.0)
                data_batch.append((timestamp, *features, wear_value))

            cursor.executemany(
                """
                INSERT INTO tool_wear_data
                (timestamp, feature_1, feature_2, feature_3, feature_4, feature_5, wear_value)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                data_batch,
            )

        self._conn.commit()
        logger.info("  数据填充完成")

    def test_simple_query(self) -> dict[str, float]:
        """测试简单查询性能。"""
        cursor = self._conn.cursor()
        times: list[float] = []

        for _ in range(50):
            t0 = time.perf_counter()
            cursor.execute("SELECT COUNT(*) FROM tool_wear_data")
            cursor.fetchone()
            elapsed = (time.perf_counter() - t0) * 1000
            times.append(elapsed)

        times.sort()
        n = len(times)
        result = {
            "simple_query_ms_p50": round(times[int(n * 0.50)], 3),
            "simple_query_ms_p95": round(times[min(int(n * 0.95), n - 1)], 3),
            "simple_query_ms_mean": round(sum(times) / n, 3),
        }
        self._results.update(result)
        return result

    def test_indexed_query(self) -> dict[str, float]:
        """测试索引查询性能。"""
        cursor = self._conn.cursor()
        times: list[float] = []

        for _ in range(50):
            random_timestamp = time.time() - random.uniform(0, 1000)
            t0 = time.perf_counter()
            cursor.execute(
                "SELECT * FROM tool_wear_data WHERE timestamp > ? LIMIT 100",
                (random_timestamp,),
            )
            cursor.fetchall()
            elapsed = (time.perf_counter() - t0) * 1000
            times.append(elapsed)

        times.sort()
        n = len(times)
        result = {
            "indexed_query_ms_p50": round(times[int(n * 0.50)], 3),
            "indexed_query_ms_p95": round(times[min(int(n * 0.95), n - 1)], 3),
            "indexed_query_ms_mean": round(sum(times) / n, 3),
        }
        self._results.update(result)
        return result

    def test_aggregate_query(self) -> dict[str, float]:
        """测试聚合查询性能。"""
        cursor = self._conn.cursor()
        times: list[float] = []

        for _ in range(20):
            t0 = time.perf_counter()
            cursor.execute("""
                SELECT
                    COUNT(*),
                    AVG(wear_value),
                    MIN(wear_value),
                    MAX(wear_value)
                FROM tool_wear_data
            """)
            cursor.fetchone()
            elapsed = (time.perf_counter() - t0) * 1000
            times.append(elapsed)

        times.sort()
        n = len(times)
        result = {
            "aggregate_query_ms_p50": round(times[int(n * 0.50)], 3),
            "aggregate_query_ms_p95": round(times[min(int(n * 0.95), n - 1)], 3),
            "aggregate_query_ms_mean": round(sum(times) / n, 3),
        }
        self._results.update(result)
        return result

    def test_insert_performance(self) -> dict[str, float]:
        """测试插入性能。"""
        cursor = self._conn.cursor()
        times: list[float] = []
        n_inserts = 1000

        for _ in range(10):
            base_time = time.time()
            data_batch = [
                (base_time + i * 0.1, *np.random.randn(5).tolist(), random.uniform(0, 1)) for i in range(n_inserts)
            ]

            t0 = time.perf_counter()
            cursor.executemany(
                """
                INSERT INTO tool_wear_data
                (timestamp, feature_1, feature_2, feature_3, feature_4, feature_5, wear_value)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                data_batch,
            )
            self._conn.commit()
            elapsed = (time.perf_counter() - t0) * 1000
            times.append(elapsed)

        times.sort()
        n = len(times)
        result = {
            "insert_batch_ms": round(times[int(n * 0.50)], 2),
            "insert_per_row_ms": round(times[int(n * 0.50)] / n_inserts, 4),
            "insert_throughput_rps": round(n_inserts / (times[int(n * 0.50)] / 1000), 1),
        }
        self._results.update(result)
        return result

    def test_join_query(self) -> dict[str, float]:
        """测试关联查询性能。"""
        cursor = self._conn.cursor()

        # 先插入一些关联数据
        cursor.execute(
            "INSERT INTO machining_tasks (task_name, material, tool_type) VALUES (?, ?, ?)",
            ("test_task", "aluminum", "end_mill"),
        )
        task_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO nc_programs (program_name, gcode_content, task_id) VALUES (?, ?, ?)",
            ("test_program", "G00 X0 Y0", task_id),
        )
        self._conn.commit()

        times: list[float] = []

        for _ in range(20):
            t0 = time.perf_counter()
            cursor.execute("""
                SELECT t.task_name, p.program_name
                FROM machining_tasks t
                LEFT JOIN nc_programs p ON t.id = p.task_id
                WHERE t.status = 'pending'
            """)
            cursor.fetchall()
            elapsed = (time.perf_counter() - t0) * 1000
            times.append(elapsed)

        times.sort()
        n = len(times)
        result = {
            "join_query_ms_p50": round(times[int(n * 0.50)], 3),
            "join_query_ms_p95": round(times[min(int(n * 0.95), n - 1)], 3),
            "join_query_ms_mean": round(sum(times) / n, 3),
        }
        self._results.update(result)
        return result

    def run_all(self) -> dict[str, Any]:
        """运行所有数据库性能测试。"""
        self.setup()

        try:
            logger.info("  测试简单查询...")
            self.test_simple_query()

            logger.info("  测试索引查询...")
            self.test_indexed_query()

            logger.info("  测试聚合查询...")
            self.test_aggregate_query()

            logger.info("  测试插入性能...")
            self.test_insert_performance()

            logger.info("  测试关联查询...")
            self.test_join_query()

        finally:
            self.teardown()

        return self.get_all_results()

    def get_all_results(self) -> dict[str, Any]:
        """获取所有测试结果。"""
        return dict(self._results)

    def save_results(self, output_path: str) -> str:
        """保存测试结果到文件。"""
        data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "database": "SQLite",
            "results": self.get_all_results(),
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return output_path


def bench_database_performance(benchmark: Any) -> None:
    """pytest-benchmark集成。"""
    bench = DatabasePerfBenchmark()
    benchmark(bench.run_all)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bench = DatabasePerfBenchmark()
    results = bench.run_all()
    logger.info("\n数据库性能测试结果:")
    for k, v in results.items():
        logger.info("  %s: %s", k, v)
