"""Interface contract test for the three-layer architecture.

Validates that all layer inputs/outputs conform to their defined schemas,
including boundary value testing and error scenario coverage.

Coverage targets:
    - Interface field coverage: 100%
    - Anomaly scenario coverage: >= 90%
"""

from __future__ import annotations

import json
import numpy as np

from app.ai.unified_embedding.interfaces import (
    CognitiveToPerceptionRequest,
    CognitiveToPerceptionResponse,
    PerceptionToExecutionRequest,
    ExecutionToCognitiveRequest,
    QualityRequirements,
    DimensionalTolerance,
    SurfaceRoughnessSpec,
    QualityLevel,
    SensorConfig,
    SensorType,
    FeatureExtractionAlgorithm,
    GeometryInput,
    PointCloudData,
    CuttingParameters,
    MonitoringPointConfig,
    PredictionBaseline,
    RealTimeState,
    AnomalyEvent,
    EventType,
    EventSeverity,
    AdjustmentSuggestion,
    FeedbackSignal,
    AdjustmentPriority,
    MachiningProcessFlow,
)


class TestCognitiveToPerceptionInterface:
    """Tests for Cognitive → Perception layer interface."""

    def test_valid_request_construction(self):
        qr = QualityRequirements(
            dimensional_tolerances=[
                DimensionalTolerance(nominal_mm=50.0, upper_deviation_mm=0.05, lower_deviation_mm=-0.05),
            ],
            surface_roughness=SurfaceRoughnessSpec(ra_um=1.6),
            target_quality_level=QualityLevel.IT7,
        )
        req = CognitiveToPerceptionRequest(
            process_intent="45钢零件粗铣平面",
            quality_requirements=qr,
        )
        errors = req.validate()
        assert len(errors) == 0, f"Unexpected validation errors: {errors}"

    def test_request_json_serialization(self):
        qr = QualityRequirements(
            dimensional_tolerances=[
                DimensionalTolerance(nominal_mm=100.0, upper_deviation_mm=0.1, lower_deviation_mm=-0.1),
            ],
            surface_roughness=SurfaceRoughnessSpec(ra_um=3.2),
        )
        req = CognitiveToPerceptionRequest(process_intent="铣削加工", quality_requirements=qr)
        json_str = req.to_json()
        parsed = json.loads(json_str)
        assert parsed["process_intent"] == "铣削加工"
        assert len(parsed["quality_requirements"]["dimensional_tolerances"]) == 1

    def test_request_json_deserialization(self):
        qr = QualityRequirements(
            dimensional_tolerances=[
                DimensionalTolerance(nominal_mm=50.0, upper_deviation_mm=0.02, lower_deviation_mm=-0.02),
            ],
        )
        req = CognitiveToPerceptionRequest(process_intent="钻孔加工", quality_requirements=qr)
        json_str = req.to_json()
        restored = CognitiveToPerceptionRequest.from_json(json_str)
        assert restored.process_intent == "钻孔加工"

    def test_empty_process_intent(self):
        qr = QualityRequirements()
        req = CognitiveToPerceptionRequest(process_intent="", quality_requirements=qr)
        errors = req.validate()
        assert len(errors) > 0

    def test_process_intent_max_length(self):
        qr = QualityRequirements()
        req = CognitiveToPerceptionRequest(process_intent="A" * 513, quality_requirements=qr)
        errors = req.validate()
        assert len(errors) > 0

    def test_process_intent_boundary_512(self):
        qr = QualityRequirements()
        req = CognitiveToPerceptionRequest(process_intent="A" * 512, quality_requirements=qr)
        errors = req.validate()
        assert len(errors) == 0

    def test_all_quality_tolerances(self):
        qr = QualityRequirements(
            dimensional_tolerances=[
                DimensionalTolerance(
                    nominal_mm=10.0, upper_deviation_mm=0.02, lower_deviation_mm=-0.02,
                    it_grade=QualityLevel.IT7,
                ),
                DimensionalTolerance(
                    nominal_mm=25.0, upper_deviation_mm=0.03, lower_deviation_mm=-0.03,
                    it_grade=QualityLevel.IT8,
                ),
                DimensionalTolerance(
                    nominal_mm=50.0, upper_deviation_mm=0.05, lower_deviation_mm=-0.05,
                    it_grade=QualityLevel.IT9,
                ),
            ],
            surface_roughness=SurfaceRoughnessSpec(ra_um=1.6, rz_um=6.3, rmax_um=10.0),
            geometric_tolerances_mm={"flatness": 0.02, "cylindricity": 0.03},
            max_burr_height_mm=0.1,
            target_quality_level=QualityLevel.IT7,
        )
        req = CognitiveToPerceptionRequest(process_intent="精密加工", quality_requirements=qr)
        errors = req.validate()
        assert len(errors) == 0

    def test_response_json_serialization(self):
        resp = CognitiveToPerceptionResponse(
            request_id="test-123",
            sensor_configs=[
                SensorConfig(
                    sensor_type=SensorType.ACCELEROMETER,
                    installation_position="spindle",
                    sampling_frequency_hz=1000.0,
                    measurement_range=(-50.0, 50.0),
                    resolution=0.01,
                ),
            ],
            feature_algorithm=FeatureExtractionAlgorithm.RESNET50,
            sampling_frequency_hz=100.0,
        )
        json_str = resp.to_json()
        parsed = json.loads(json_str)
        assert parsed["request_id"] == "test-123"

    def test_response_json_deserialization(self):
        resp = CognitiveToPerceptionResponse(
            request_id="test-456",
            sensor_configs=[],
            feature_algorithm=FeatureExtractionAlgorithm.VIT,
            sampling_frequency_hz=200.0,
        )
        json_str = resp.to_json()
        restored = CognitiveToPerceptionResponse.from_json(json_str)
        assert restored.request_id == "test-456"
        assert restored.feature_algorithm == FeatureExtractionAlgorithm.VIT

    def test_invalid_sensor_position(self):
        sc = SensorConfig(
            sensor_type=SensorType.ACCELEROMETER,
            installation_position="invalid_position",
            sampling_frequency_hz=1000.0,
            measurement_range=(-50.0, 50.0),
            resolution=0.01,
        )
        errors = sc.validate()
        assert len(errors) > 0

    def test_sensor_frequency_boundaries(self):
        sc_low = SensorConfig(
            sensor_type=SensorType.THERMOCOUPLE,
            installation_position="spindle",
            sampling_frequency_hz=0.0,
            measurement_range=(0.0, 500.0),
            resolution=0.1,
        )
        assert len(sc_low.validate()) > 0

        sc_high = SensorConfig(
            sensor_type=SensorType.THERMOCOUPLE,
            installation_position="spindle",
            sampling_frequency_hz=200000.0,
            measurement_range=(0.0, 500.0),
            resolution=0.1,
        )
        assert len(sc_high.validate()) > 0

    def test_all_sensor_types(self):
        for st in SensorType:
            sc = SensorConfig(
                sensor_type=st,
                installation_position="spindle",
                sampling_frequency_hz=1000.0,
                measurement_range=(-50.0, 50.0),
                resolution=0.01,
            )
            errors = sc.validate()
            assert len(errors) == 0, f"SensorType {st} failed: {errors}"


class TestPerceptionToExecutionInterface:
    """Tests for Perception → Execution layer interface."""

    def test_valid_request_construction(self):
        geom = GeometryInput(
            stl_path="test_model.stl",
            stock_dimensions_mm=(100.0, 100.0, 50.0),
            material="steel",
        )
        cp = CuttingParameters(
            feed_rate_mm_min=500.0,
            depth_of_cut_mm=2.0,
            spindle_speed_rpm=8000.0,
        )
        req = PerceptionToExecutionRequest(geometry=geom, cutting_parameters=cp)
        errors = req.validate()
        assert len(errors) == 0

    def test_point_cloud_geometry(self):
        pc = PointCloudData(
            points=np.random.randn(1000, 3).astype(np.float32),
            point_count=1000,
        )
        geom = GeometryInput(point_cloud=pc)
        cp = CuttingParameters(feed_rate_mm_min=500.0, depth_of_cut_mm=2.0, spindle_speed_rpm=8000.0)
        req = PerceptionToExecutionRequest(geometry=geom, cutting_parameters=cp)
        errors = req.validate()
        assert len(errors) == 0

    def test_point_cloud_serialization(self):
        pc = PointCloudData(
            points=np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32),
            point_count=2,
        )
        data = pc.serialize()
        restored = PointCloudData.deserialize(data, 2)
        assert np.allclose(restored.points, pc.points)

    def test_no_geometry_provided(self):
        geom = GeometryInput()
        cp = CuttingParameters(feed_rate_mm_min=500.0, depth_of_cut_mm=2.0, spindle_speed_rpm=8000.0)
        req = PerceptionToExecutionRequest(geometry=geom, cutting_parameters=cp)
        errors = req.validate()
        assert len(errors) > 0

    def test_invalid_stl_path(self):
        geom = GeometryInput(stl_path="test_model.obj")
        cp = CuttingParameters(feed_rate_mm_min=500.0, depth_of_cut_mm=2.0, spindle_speed_rpm=8000.0)
        req = PerceptionToExecutionRequest(geometry=geom, cutting_parameters=cp)
        errors = req.validate()
        assert len(errors) > 0

    def test_valid_step_path(self):
        geom = GeometryInput(step_path="test_model.step")
        cp = CuttingParameters(feed_rate_mm_min=500.0, depth_of_cut_mm=2.0, spindle_speed_rpm=8000.0)
        req = PerceptionToExecutionRequest(geometry=geom, cutting_parameters=cp)
        errors = req.validate()
        assert len(errors) == 0

    def test_cutting_parameters_boundaries(self):
        cp_min = CuttingParameters(feed_rate_mm_min=0.0, depth_of_cut_mm=0.0, spindle_speed_rpm=0.0)
        assert len(cp_min.validate()) > 0

        cp_max = CuttingParameters(
            feed_rate_mm_min=20000.0, depth_of_cut_mm=200.0, spindle_speed_rpm=200000.0
        )
        assert len(cp_max.validate()) > 0

    def test_all_cutting_parameters_fields(self):
        cp = CuttingParameters(
            feed_rate_mm_min=500.0,
            depth_of_cut_mm=2.0,
            spindle_speed_rpm=8000.0,
            step_over_mm=5.0,
            cutting_speed_m_min=150.0,
            coolant_on=True,
            approach_distance_mm=5.0,
            retract_distance_mm=10.0,
        )
        errors = cp.validate()
        assert len(errors) == 0

    def test_monitoring_point_config(self):
        mp = MonitoringPointConfig(
            sensor_type=SensorType.ACCELEROMETER,
            installation_position="spindle",
            sampling_frequency_hz=1000.0,
        )
        errors = mp.validate()
        assert len(errors) == 0

    def test_monitoring_point_invalid_position(self):
        mp = MonitoringPointConfig(
            sensor_type=SensorType.ACCELEROMETER,
            installation_position="invalid",
            sampling_frequency_hz=1000.0,
        )
        errors = mp.validate()
        assert len(errors) > 0

    def test_prediction_baseline(self):
        pb = PredictionBaseline(
            expected_surface_roughness_ra=1.6,
            expected_tool_wear_rate_um_per_min=10.0,
            expected_cutting_force_n=200.0,
            expected_power_consumption_kw=3.0,
            control_thresholds={"vibration_max": 10.0, "temperature_max": 80.0},
            confidence=0.85,
        )
        errors = pb.validate()
        assert len(errors) == 0

    def test_prediction_baseline_invalid_confidence(self):
        pb = PredictionBaseline(
            expected_surface_roughness_ra=1.6,
            expected_tool_wear_rate_um_per_min=10.0,
            expected_cutting_force_n=200.0,
            expected_power_consumption_kw=3.0,
            confidence=1.5,
        )
        errors = pb.validate()
        assert len(errors) > 0


class TestExecutionToCognitiveInterface:
    """Tests for Execution → Cognitive layer interface."""

    def test_valid_request_construction(self):
        rts = RealTimeState(
            spindle_speed_rpm=8000.0,
            spindle_load_pct=60.0,
            feed_rate_mm_min=500.0,
            vibration_x=5.0,
            vibration_y=3.0,
            vibration_z=2.0,
            spindle_temp_c=45.0,
            tool_temp_c=120.0,
            coolant_temp_c=25.0,
            cutting_force_x_n=100.0,
            cutting_force_y_n=80.0,
            cutting_force_z_n=200.0,
            tool_wear_mm=0.15,
            acoustic_emission_rms=0.5,
            position_x_mm=25.0,
            position_y_mm=30.0,
            position_z_mm=-5.0,
            duty_cycle_pct=60.0,
        )
        req = ExecutionToCognitiveRequest(real_time_state=rts)
        errors = req.validate()
        assert len(errors) == 0

    def test_real_time_state_to_dict(self):
        rts = RealTimeState(spindle_speed_rpm=8000.0, spindle_load_pct=60.0)
        d = rts.to_dict()
        assert d["spindle_speed_rpm"] == 8000.0
        assert d["spindle_load_pct"] == 60.0

    def test_real_time_state_to_numpy(self):
        rts = RealTimeState(spindle_speed_rpm=8000.0, spindle_load_pct=60.0)
        arr = rts.to_numpy()
        assert arr.shape == (18,)
        assert arr.dtype == np.float32

    def test_negative_spindle_speed(self):
        rts = RealTimeState(spindle_speed_rpm=-100.0)
        errors = rts.validate()
        assert len(errors) > 0

    def test_spindle_load_boundary(self):
        rts_ok = RealTimeState(spindle_load_pct=150.0)
        assert len(rts_ok.validate()) == 0

        rts_bad = RealTimeState(spindle_load_pct=151.0)
        assert len(rts_bad.validate()) > 0

    def test_tool_wear_boundary(self):
        rts_ok = RealTimeState(tool_wear_mm=5.0)
        assert len(rts_ok.validate()) == 0

        rts_bad = RealTimeState(tool_wear_mm=5.1)
        assert len(rts_bad.validate()) > 0

    def test_anomaly_event(self):
        evt = AnomalyEvent(
            event_type=EventType.VIBRATION_ANOMALY,
            severity=EventSeverity.WARNING,
            description="振动幅值超过阈值",
            source_sensor=SensorType.ACCELEROMETER,
            measured_value=12.0,
            threshold_value=10.0,
            duration_ms=500.0,
        )
        errors = evt.validate()
        assert len(errors) == 0

    def test_all_event_types(self):
        for et in EventType:
            evt = AnomalyEvent(event_type=et, severity=EventSeverity.WARNING)
            errors = evt.validate()
            assert len(errors) == 0, f"EventType {et} failed: {errors}"

    def test_all_severity_levels(self):
        for sev in EventSeverity:
            evt = AnomalyEvent(event_type=EventType.VIBRATION_ANOMALY, severity=sev)
            errors = evt.validate()
            assert len(errors) == 0

    def test_negative_duration(self):
        evt = AnomalyEvent(duration_ms=-100.0)
        errors = evt.validate()
        assert len(errors) > 0

    def test_adjustment_suggestion(self):
        sug = AdjustmentSuggestion(
            description="降低进给速度至400mm/min",
            suggested_parameters={"feed_rate_mm_min": 400.0},
            confidence=0.89,
            priority=AdjustmentPriority.HIGH,
            reasoning="当前振动偏大，适当降低进给速度可改善表面质量",
        )
        errors = sug.validate()
        assert len(errors) == 0

    def test_adjustment_invalid_confidence(self):
        sug = AdjustmentSuggestion(confidence=-0.1)
        errors = sug.validate()
        assert len(errors) > 0

        sug = AdjustmentSuggestion(confidence=1.5)
        errors = sug.validate()
        assert len(errors) > 0

    def test_all_priority_levels(self):
        for pri in AdjustmentPriority:
            sug = AdjustmentSuggestion(priority=pri)
            errors = sug.validate()
            assert len(errors) == 0

    def test_feedback_signal(self):
        fs = FeedbackSignal(
            suggestions=[
                AdjustmentSuggestion(
                    description="test",
                    confidence=0.9,
                    priority=AdjustmentPriority.HIGH,
                ),
            ],
            overall_confidence=0.85,
            execution_priority=AdjustmentPriority.HIGH,
            estimated_impact="预计表面粗糙度降至Ra1.2",
            requires_halt=False,
        )
        errors = fs.validate()
        assert len(errors) == 0

    def test_feedback_signal_invalid_confidence(self):
        fs = FeedbackSignal(overall_confidence=2.0)
        errors = fs.validate()
        assert len(errors) > 0

    def test_request_with_anomaly_events(self):
        rts = RealTimeState(spindle_speed_rpm=8000.0)
        events = [
            AnomalyEvent(
                event_type=EventType.TOOL_WEAR_THRESHOLD,
                severity=EventSeverity.WARNING,
                description="刀具磨损接近阈值",
                measured_value=0.28,
                threshold_value=0.30,
                duration_ms=1000.0,
            ),
            AnomalyEvent(
                event_type=EventType.TEMPERATURE_ANOMALY,
                severity=EventSeverity.CRITICAL,
                description="主轴温度异常升高",
                measured_value=85.0,
                threshold_value=70.0,
                duration_ms=3000.0,
            ),
        ]
        req = ExecutionToCognitiveRequest(real_time_state=rts, anomaly_events=events)
        errors = req.validate()
        assert len(errors) == 0


class TestMachiningProcessFlow:
    """Tests for the complete three-layer process flow orchestration."""

    def test_complete_flow(self):
        flow = MachiningProcessFlow()

        qr = QualityRequirements(
            dimensional_tolerances=[
                DimensionalTolerance(nominal_mm=50.0, upper_deviation_mm=0.05, lower_deviation_mm=-0.05),
            ],
            target_quality_level=QualityLevel.IT7,
        )
        req1, errs1 = flow.step_cognitive_to_perception("45钢粗铣平面", qr)
        assert len(errs1) == 0

        geom = GeometryInput(stl_path="test.stl", material="45钢")
        cp = CuttingParameters(feed_rate_mm_min=500.0, depth_of_cut_mm=2.0, spindle_speed_rpm=8000.0)
        req2, errs2 = flow.step_perception_to_execution(geom, cp)
        assert len(errs2) == 0

        rts = RealTimeState(spindle_speed_rpm=8000.0, spindle_load_pct=60.0)
        req3, errs3 = flow.step_execution_to_cognitive(rts)
        assert len(errs3) == 0

        report = flow.get_flow_report()
        assert report["total_steps"] == 3
        assert report["valid_steps"] == 3
        assert report["error_count"] == 0

    def test_flow_with_errors(self):
        flow = MachiningProcessFlow()

        qr = QualityRequirements()
        req, errs = flow.step_cognitive_to_perception("", qr)
        assert len(errs) > 0

        report = flow.get_flow_report()
        assert report["valid_steps"] == 0
        assert report["error_count"] > 0

    def test_flow_all_quality_levels(self):
        for ql in QualityLevel:
            flow = MachiningProcessFlow()
            qr = QualityRequirements(
                dimensional_tolerances=[
                    DimensionalTolerance(
                        nominal_mm=50.0, upper_deviation_mm=0.05,
                        lower_deviation_mm=-0.05, it_grade=ql,
                    ),
                ],
                target_quality_level=ql,
            )
            _, errs = flow.step_cognitive_to_perception(f"test-{ql.value}", qr)
            assert len(errs) == 0, f"QualityLevel {ql} failed: {errs}"


class TestAnomalyScenarioCoverage:
    """Edge case and anomaly scenario tests."""

    def test_negative_nominal(self):
        tol = DimensionalTolerance(nominal_mm=-1.0, upper_deviation_mm=0.05, lower_deviation_mm=-0.05)
        errors = tol.validate()
        assert len(errors) > 0

    def test_inverted_deviations(self):
        tol = DimensionalTolerance(nominal_mm=50.0, upper_deviation_mm=-0.05, lower_deviation_mm=0.05)
        errors = tol.validate()
        assert len(errors) > 0

    def test_surface_roughness_out_of_range(self):
        sr = SurfaceRoughnessSpec(ra_um=0.001)
        errors = sr.validate()
        assert len(errors) > 0

        sr = SurfaceRoughnessSpec(ra_um=100.0)
        errors = sr.validate()
        assert len(errors) > 0

    def test_negative_geometric_tolerance(self):
        qr = QualityRequirements(geometric_tolerances_mm={"flatness": -0.01})
        errors = qr.validate()
        assert len(errors) > 0

    def test_burr_height_out_of_range(self):
        qr = QualityRequirements(max_burr_height_mm=10.0)
        errors = qr.validate()
        assert len(errors) > 0

    def test_measurement_range_inverted(self):
        sc = SensorConfig(
            sensor_type=SensorType.ACCELEROMETER,
            installation_position="spindle",
            sampling_frequency_hz=1000.0,
            measurement_range=(50.0, -50.0),
            resolution=0.01,
        )
        errors = sc.validate()
        assert len(errors) > 0

    def test_empty_quality_requirements(self):
        qr = QualityRequirements()
        errors = qr.validate()
        assert len(errors) == 0  # Valid with defaults

    def test_full_quality_requirements(self):
        qr = QualityRequirements(
            dimensional_tolerances=[
                DimensionalTolerance(nominal_mm=10.0, upper_deviation_mm=0.01, lower_deviation_mm=-0.01),
                DimensionalTolerance(nominal_mm=25.0, upper_deviation_mm=0.02, lower_deviation_mm=-0.02),
            ],
            surface_roughness=SurfaceRoughnessSpec(ra_um=0.8, rz_um=3.2, rmax_um=6.3),
            geometric_tolerances_mm={
                "flatness": 0.01,
                "cylindricity": 0.02,
                "parallelism": 0.03,
                "perpendicularity": 0.04,
            },
            max_burr_height_mm=0.05,
            target_quality_level=QualityLevel.IT5,
        )
        errors = qr.validate()
        assert len(errors) == 0
