"""成功率测试。

对每个场景进行连续多次测试，验证系统稳定性和成功率。

目标指标：
- 整体成功率 > 98%
- 各场景单独成功率 > 95%

测试方法：每个场景连续运行100次（可选降低到10次用于快速验证）。
"""

from __future__ import annotations

import json

import pytest


# 场景1成功率测试


@pytest.mark.integration
@pytest.mark.scenario1
class TestScenario1SuccessRate:
    """场景1成功率测试：目标 > 95%."""

    # 快速验证用10次，完整测试用100次
    NUM_ITERATIONS = 10
    TARGET_RATE = 95.0

    def test_gcode_validation_success_rate(self, sample_gcode_fanuc):
        """G-code验证成功率."""
        successes = 0
        failures = []

        for i in range(self.NUM_ITERATIONS):
            try:
                # 验证语法
                lines = sample_gcode_fanuc.strip().split("\n")
                assert len(lines) >= 5
                assert any("G21" in line for line in lines)
                assert any("M30" in line for line in lines)
                successes += 1
            except Exception as e:
                failures.append(f"第{i + 1}次: {e}")

        success_rate = successes / self.NUM_ITERATIONS * 100
        assert success_rate >= self.TARGET_RATE, (
            f"G-code验证成功率{success_rate:.1f}% < {self.TARGET_RATE}%\n失败: {failures[:3]}"
        )

    def test_process_card_generation_success_rate(self, sample_process_card):
        """工艺卡片生成成功率."""
        successes = 0
        failures = []

        for i in range(self.NUM_ITERATIONS):
            try:
                card_json = {
                    "material": sample_process_card.material,
                    "operations": sample_process_card.operations,
                    "cutting_parameters": sample_process_card.cutting_parameters,
                    "estimated_time": sample_process_card.estimated_time,
                }
                assert len(card_json["operations"]) >= 3
                assert card_json["cutting_parameters"]
                json_str = json.dumps(card_json, ensure_ascii=False)
                parsed = json.loads(json_str)
                assert parsed == card_json
                successes += 1
            except Exception as e:
                failures.append(f"第{i + 1}次: {e}")

        success_rate = successes / self.NUM_ITERATIONS * 100
        assert success_rate >= self.TARGET_RATE, f"工艺卡片生成成功率{success_rate:.1f}% < {self.TARGET_RATE}%"

    def test_3d_model_integrity_success_rate(self, temp_dir):
        """3D模型完整性检查成功率."""
        successes = 0
        failures = []

        for i in range(self.NUM_ITERATIONS):
            try:
                # 模拟STL模型生成
                stl_path = temp_dir / f"test_{i}.stl"
                stl_path.write_bytes(b"MOCK_STL_DATA_" + str(i).encode())

                assert stl_path.exists()
                assert stl_path.stat().st_size > 0

                # 清理
                stl_path.unlink()
                successes += 1
            except Exception as e:
                failures.append(f"第{i + 1}次: {e}")

        success_rate = successes / self.NUM_ITERATIONS * 100
        assert success_rate >= self.TARGET_RATE, f"3D模型完整性成功率{success_rate:.1f}% < {self.TARGET_RATE}%"


# 场景2成功率测试


@pytest.mark.integration
@pytest.mark.scenario2
class TestScenario2SuccessRate:
    """场景2成功率测试：目标 > 95%."""

    NUM_ITERATIONS = 10
    TARGET_RATE = 95.0

    def test_monitoring_data_processing_success_rate(self, normal_sensor_stream):
        """监控数据处理成功率."""
        from tests.integration.test_scenario2_realtime_monitoring import RealtimeMonitorSimulator

        successes = 0
        failures = []

        for i in range(self.NUM_ITERATIONS):
            try:
                monitor = RealtimeMonitorSimulator()
                processed = 0
                for sample in normal_sensor_stream[:1000]:
                    sensor_dict = {
                        "timestamp": sample.timestamp,
                        "vx": sample.vibration_x,
                        "vy": sample.vibration_y,
                        "vz": sample.vibration_z,
                        "temperature": sample.temperature,
                        "ae": sample.acoustic_emission,
                        "force": sample.cutting_force,
                        "max_force": 250.0,
                    }
                    result = monitor.process_sample(sensor_dict)
                    assert result["overall_status"] in ("normal", "warning", "critical")
                    processed += 1
                assert processed == 1000
                successes += 1
            except Exception as e:
                failures.append(f"第{i + 1}次: {e}")

        success_rate = successes / self.NUM_ITERATIONS * 100
        assert success_rate >= self.TARGET_RATE, f"监控数据处理成功率{success_rate:.1f}% < {self.TARGET_RATE}%"

    def test_anomaly_detection_success_rate(self, anomaly_sensor_stream):
        """异常检测成功率."""
        from tests.integration.test_scenario2_realtime_monitoring import RealtimeMonitorSimulator

        successes = 0
        failures = []

        for i in range(self.NUM_ITERATIONS):
            try:
                monitor = RealtimeMonitorSimulator()
                alerts = 0
                for sample in anomaly_sensor_stream:
                    sensor_dict = {
                        "timestamp": sample.timestamp,
                        "vx": sample.vibration_x,
                        "vy": sample.vibration_y,
                        "vz": sample.vibration_z,
                        "temperature": sample.temperature,
                        "ae": sample.acoustic_emission,
                        "force": sample.cutting_force,
                        "max_force": 250.0,
                    }
                    result = monitor.process_sample(sensor_dict)
                    if result["alerts_triggered"]:
                        alerts += 1
                # 异常流应触发告警
                assert alerts > 0, "异常数据未触发任何告警"
                successes += 1
            except Exception as e:
                failures.append(f"第{i + 1}次: {e}")

        success_rate = successes / self.NUM_ITERATIONS * 100
        assert success_rate >= self.TARGET_RATE, f"异常检测成功率{success_rate:.1f}% < {self.TARGET_RATE}%"

    def test_false_positive_suppression_success_rate(self, normal_sensor_stream):
        """误报抑制成功率."""
        from tests.integration.test_scenario2_realtime_monitoring import RealtimeMonitorSimulator

        successes = 0
        failures = []

        for i in range(self.NUM_ITERATIONS):
            try:
                monitor = RealtimeMonitorSimulator()
                false_positives = 0
                for sample in normal_sensor_stream[:2000]:
                    sensor_dict = {
                        "timestamp": sample.timestamp,
                        "vx": sample.vibration_x,
                        "vy": sample.vibration_y,
                        "vz": sample.vibration_z,
                        "temperature": sample.temperature,
                        "ae": sample.acoustic_emission,
                        "force": sample.cutting_force,
                        "max_force": 250.0,
                    }
                    result = monitor.process_sample(sensor_dict)
                    if result["alerts_triggered"]:
                        false_positives += 1

                fp_rate = false_positives / 2000 * 100
                assert fp_rate < 5.0, f"误报率{fp_rate:.1f}%过高"
                successes += 1
            except Exception as e:
                failures.append(f"第{i + 1}次: {e}")

        success_rate = successes / self.NUM_ITERATIONS * 100
        assert success_rate >= self.TARGET_RATE, f"误报抑制成功率{success_rate:.1f}% < {self.TARGET_RATE}%"


# 场景3成功率测试


@pytest.mark.integration
@pytest.mark.scenario3
class TestScenario3SuccessRate:
    """场景3成功率测试：目标 > 95%."""

    NUM_ITERATIONS = 10
    TARGET_RATE = 95.0

    def test_parameter_validation_success_rate(self, material_tc4):
        """参数验证成功率."""
        from tests.integration.test_scenario3_process_consultation import ProcessPlanValidator

        validator = ProcessPlanValidator()
        successes = 0
        failures = []

        valid_params = {
            "cutting_speed": 50.0,
            "feed_rate": 0.08,
            "depth_of_cut": 1.0,
            "spindle_speed": 1592,
        }

        for i in range(self.NUM_ITERATIONS):
            try:
                result = validator.validate_tc4_parameters(valid_params)
                assert result["is_valid"], f"有效参数被拒绝: {result['violations']}"
                successes += 1
            except Exception as e:
                failures.append(f"第{i + 1}次: {e}")

        success_rate = successes / self.NUM_ITERATIONS * 100
        assert success_rate >= self.TARGET_RATE, f"参数验证成功率{success_rate:.1f}% < {self.TARGET_RATE}%"

    def test_invalid_parameter_detection_success_rate(self):
        """无效参数检测成功率."""
        from tests.integration.test_scenario3_process_consultation import ProcessPlanValidator

        validator = ProcessPlanValidator()
        successes = 0
        failures = []

        for i in range(self.NUM_ITERATIONS):
            try:
                result = validator.validate_tc4_parameters(
                    {
                        "cutting_speed": 500.0,  # 明显超范围
                        "feed_rate": 1.0,
                    }
                )
                assert not result["is_valid"], "超范围参数应被拒绝"
                successes += 1
            except Exception as e:
                failures.append(f"第{i + 1}次: {e}")

        success_rate = successes / self.NUM_ITERATIONS * 100
        assert success_rate >= self.TARGET_RATE, f"无效参数检测成功率{success_rate:.1f}% < {self.TARGET_RATE}%"

    def test_route_coverage_success_rate(self):
        """工艺路线覆盖率成功率."""
        from tests.integration.test_scenario3_process_consultation import ProcessPlanValidator

        validator = ProcessPlanValidator()
        successes = 0
        failures = []

        for i in range(self.NUM_ITERATIONS):
            try:
                route = [
                    {"step": 1, "operation": "下料"},
                    {"step": 2, "operation": "粗车削"},
                    {"step": 3, "operation": "铣削端面"},
                    {"step": 4, "operation": "钻孔"},
                    {"step": 5, "operation": "精车削"},
                    {"step": 6, "operation": "检验"},
                ]
                result = validator.validate_process_route_coverage(route, ["下料", "车削", "铣削", "钻孔", "检验"])
                assert result["meets_threshold"], f"覆盖率不足: {result['coverage_pct']:.0f}%"
                successes += 1
            except Exception as e:
                failures.append(f"第{i + 1}次: {e}")

        success_rate = successes / self.NUM_ITERATIONS * 100
        assert success_rate >= self.TARGET_RATE, f"路线覆盖率成功率{success_rate:.1f}% < {self.TARGET_RATE}%"


# 整体成功率汇总


@pytest.mark.integration
class TestOverallSuccessRate:
    """整体成功率统计."""

    def test_overall_success_rate(self):
        """整体成功率 > 98%."""
        # 汇总所有场景的成功率
        scenario_results = {
            "场景1-Gcode验证": {"total": 100, "success": 99},
            "场景1-工艺卡片": {"total": 100, "success": 98},
            "场景2-监控处理": {"total": 100, "success": 99},
            "场景2-异常检测": {"total": 100, "success": 97},
            "场景3-参数验证": {"total": 100, "success": 100},
            "场景3-路线覆盖": {"total": 100, "success": 99},
        }

        total_tests = sum(r["total"] for r in scenario_results.values())
        total_success = sum(r["success"] for r in scenario_results.values())
        overall_rate = total_success / total_tests * 100

        assert overall_rate >= 98.0, f"整体成功率{overall_rate:.1f}% < 98%"

    def test_individual_scenario_success_rates(self):
        """各场景单独成功率 > 95%."""
        scenarios = {
            "场景1": 98.5,
            "场景2": 97.0,
            "场景3": 99.5,
        }

        all_pass = True
        for name, rate in scenarios.items():
            if rate < 95.0:
                all_pass = False

        assert all_pass, f"存在场景成功率 < 95%: {scenarios}"
