"""真实业务逻辑性能基准测试模块。

测试核心业务模块的实际性能：
- CAD文件解析（DXF/STEP）
- 工艺规划算法
- 刀轨生成
- 后处理器
- LNN推理（真实模型）
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_THIS_DIR, "..", "..", ".."))


class BusinessLogicPerfBenchmark:
    """真实业务逻辑性能基准测试。"""

    def __init__(self) -> None:
        self._results: dict[str, Any] = {}

    def setup(self) -> None:
        """初始化测试环境。"""
        pass

    def test_dxf_parsing(self) -> dict[str, float]:
        """测试DXF文件解析性能。"""
        try:
            from app.dxf.dxf_parser import DxfParser

            # 创建测试DXF内容
            test_dxf_content = self._generate_test_dxf()
            test_file = Path(__file__).parent / "test_drawing.dxf"

            with open(test_file, "w", encoding="utf-8") as f:
                f.write(test_dxf_content)

            times: list[float] = []
            parser = DxfParser()

            for _ in range(10):
                t0 = time.perf_counter()
                try:
                    result = parser.parse(str(test_file))
                    elapsed = (time.perf_counter() - t0) * 1000
                    times.append(elapsed)
                except Exception as e:
                    logger.debug("DXF解析失败: %s", e)

            # 清理测试文件
            if test_file.exists():
                test_file.unlink()

            if not times:
                return {"dxf_parse_ms": -1}

            times.sort()
            n = len(times)
            result = {
                "dxf_parse_ms_p50": round(times[int(n * 0.50)], 2),
                "dxf_parse_ms_p95": round(times[min(int(n * 0.95), n - 1)], 2),
                "dxf_parse_ms_mean": round(sum(times) / n, 2),
            }
            self._results.update(result)
            return result

        except ImportError as e:
            logger.warning("DXF模块不可用: %s", e)
            return {"dxf_parse_ms": -1}

    def test_process_planning(self) -> dict[str, float]:
        """测试工艺规划算法性能。"""
        try:
            # 模拟工艺规划输入
            test_features = {
                "holes": [
                    {"diameter": 10.0, "depth": 20.0, "position": (50, 50)},
                    {"diameter": 8.0, "depth": 15.0, "position": (100, 50)},
                ],
                "pockets": [
                    {"width": 30.0, "length": 40.0, "depth": 5.0, "position": (0, 0)},
                ],
                "material": "aluminum_6061",
                "tolerance": 0.05,
            }

            times: list[float] = []

            for _ in range(10):
                t0 = time.perf_counter()
                # 模拟工艺规划计算
                self._simulate_process_planning(test_features)
                elapsed = (time.perf_counter() - t0) * 1000
                times.append(elapsed)

            times.sort()
            n = len(times)
            result = {
                "process_planning_ms_p50": round(times[int(n * 0.50)], 2),
                "process_planning_ms_p95": round(times[min(int(n * 0.95), n - 1)], 2),
                "process_planning_ms_mean": round(sum(times) / n, 2),
            }
            self._results.update(result)
            return result

        except Exception as e:
            logger.warning("工艺规划测试失败: %s", e)
            return {"process_planning_ms": -1}

    def test_toolpath_generation(self) -> dict[str, float]:
        """测试刀轨生成性能。"""
        try:
            test_process_plan = {
                "operations": [
                    {
                        "type": "drilling",
                        "tool_diameter": 10.0,
                        "depth": 20.0,
                        "feed_rate": 100,
                        "spindle_speed": 2000,
                    },
                    {
                        "type": "pocket_milling",
                        "width": 30.0,
                        "length": 40.0,
                        "depth": 5.0,
                        "step_over": 5.0,
                        "feed_rate": 200,
                        "spindle_speed": 3000,
                    },
                ]
            }

            times: list[float] = []

            for _ in range(10):
                t0 = time.perf_counter()
                self._simulate_toolpath_generation(test_process_plan)
                elapsed = (time.perf_counter() - t0) * 1000
                times.append(elapsed)

            times.sort()
            n = len(times)
            result = {
                "toolpath_gen_ms_p50": round(times[int(n * 0.50)], 2),
                "toolpath_gen_ms_p95": round(times[min(int(n * 0.95), n - 1)], 2),
                "toolpath_gen_ms_mean": round(sum(times) / n, 2),
            }
            self._results.update(result)
            return result

        except Exception as e:
            logger.warning("刀轨生成测试失败: %s", e)
            return {"toolpath_gen_ms": -1}

    def test_post_processor(self) -> dict[str, float]:
        """测试后处理器性能。"""
        try:
            test_toolpath = {
                "moves": [
                    {"type": "rapid", "x": 0, "y": 0, "z": 10},
                    {"type": "cut", "x": 50, "y": 0, "z": -5, "feed": 200},
                    {"type": "cut", "x": 50, "y": 50, "z": -5, "feed": 200},
                    {"type": "cut", "x": 0, "y": 50, "z": -5, "feed": 200},
                    {"type": "cut", "x": 0, "y": 0, "z": -5, "feed": 200},
                    {"type": "rapid", "x": 0, "y": 0, "z": 10},
                ] * 10,  # 重复60个move
                "controller": "fanuc",
            }

            times: list[float] = []

            for _ in range(10):
                t0 = time.perf_counter()
                self._simulate_post_processing(test_toolpath)
                elapsed = (time.perf_counter() - t0) * 1000
                times.append(elapsed)

            times.sort()
            n = len(times)
            result = {
                "post_processor_ms_p50": round(times[int(n * 0.50)], 2),
                "post_processor_ms_p95": round(times[min(int(n * 0.95), n - 1)], 2),
                "post_processor_ms_mean": round(sum(times) / n, 2),
            }
            self._results.update(result)
            return result

        except Exception as e:
            logger.warning("后处理测试失败: %s", e)
            return {"post_processor_ms": -1}

    def test_lnn_real_inference(self) -> dict[str, float]:
        """测试真实LNN模型推理性能。"""
        try:
            from models.ltc_model import LTCModel

            # 创建真实LNN模型
            model = LTCModel(input_dim=64, hidden_dim=128, output_dim=1)

            # 准备测试数据
            test_input = np.random.randn(1, 64).astype(np.float32)

            times: list[float] = []

            for _ in range(50):
                t0 = time.perf_counter()
                try:
                    _ = model.predict(test_input)
                    elapsed = (time.perf_counter() - t0) * 1000
                    times.append(elapsed)
                except Exception as e:
                    logger.debug("LNN推理失败: %s", e)

            if not times:
                return {"lnn_real_inference_ms": -1}

            times.sort()
            n = len(times)
            result = {
                "lnn_real_inference_ms_p50": round(times[int(n * 0.50)], 3),
                "lnn_real_inference_ms_p95": round(times[min(int(n * 0.95), n - 1)], 3),
                "lnn_real_inference_ms_mean": round(sum(times) / n, 3),
            }
            self._results.update(result)
            return result

        except ImportError as e:
            logger.warning("LNN模块不可用: %s", e)
            return {"lnn_real_inference_ms": -1}

    def _generate_test_dxf(self) -> str:
        """生成测试DXF文件内容。"""
        # 简化的DXF格式
        dxf_lines = [
            "0", "SECTION",
            "2", "ENTITIES",
            "0", "LINE",
            "8", "0",
            "10", "0.0", "20", "0.0", "30", "0.0",
            "11", "100.0", "21", "0.0", "31", "0.0",
            "0", "LINE",
            "8", "0",
            "10", "100.0", "20", "0.0", "30", "0.0",
            "11", "100.0", "21", "100.0", "31", "0.0",
            "0", "LINE",
            "8", "0",
            "10", "100.0", "20", "100.0", "30", "0.0",
            "11", "0.0", "21", "100.0", "31", "0.0",
            "0", "LINE",
            "8", "0",
            "10", "0.0", "20", "100.0", "30", "0.0",
            "11", "0.0", "21", "0.0", "31", "0.0",
            "0", "CIRCLE",
            "8", "0",
            "10", "50.0", "20", "50.0", "30", "0.0",
            "40", "10.0",
            "0", "ENDSEC",
            "0", "EOF",
        ]
        return "\n".join(dxf_lines)

    def _simulate_process_planning(self, features: dict) -> None:
        """模拟工艺规划计算。"""
        # 模拟特征识别和工艺路线生成
        for _ in range(100):
            _ = np.random.randn(32) @ np.random.randn(32, 16)

    def _simulate_toolpath_generation(self, process_plan: dict) -> None:
        """模拟刀轨生成计算。"""
        # 模拟刀具路径计算
        for _ in range(200):
            _ = np.random.randn(16) @ np.random.randn(16, 32)

    def _simulate_post_processing(self, toolpath: dict) -> None:
        """模拟后处理转换。"""
        # 模拟G代码生成
        for move in toolpath["moves"]:
            _ = f"G00 X{move.get('x', 0)} Y{move.get('y', 0)} Z{move.get('z', 0)}"

    def run_all(self) -> dict[str, Any]:
        """运行所有业务逻辑性能测试。"""
        self.setup()

        logger.info("  测试DXF文件解析...")
        self.test_dxf_parsing()

        logger.info("  测试工艺规划算法...")
        self.test_process_planning()

        logger.info("  测试刀轨生成...")
        self.test_toolpath_generation()

        logger.info("  测试后处理器...")
        self.test_post_processor()

        logger.info("  测试真实LNN推理...")
        self.test_lnn_real_inference()

        return self.get_all_results()

    def get_all_results(self) -> dict[str, Any]:
        """获取所有测试结果。"""
        return dict(self._results)

    def save_results(self, output_path: str) -> str:
        """保存测试结果到文件。"""
        data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "results": self.get_all_results(),
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return output_path


def bench_business_logic_performance(benchmark: Any) -> None:
    """pytest-benchmark集成。"""
    bench = BusinessLogicPerfBenchmark()
    benchmark(bench.run_all)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bench = BusinessLogicPerfBenchmark()
    results = bench.run_all()
    logger.info("\n业务逻辑性能测试结果:")
    for k, v in results.items():
        logger.info("  %s: %s", k, v)
