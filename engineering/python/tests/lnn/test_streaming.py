"""Streaming Inference 模块单元测试.

验证借鉴 lingbot-map GCT 架构思想实现的流式推理模块：
- StreamingConfig 参数校验
- PagedHiddenStateCache 分页缓存与 LRU 淘汰
- KeyframeSelector 三种关键帧判定模式
- AnchorContext 锚点漂移修正
- TrajectoryMemory 轨迹记忆约束
- StreamingPredictor 端到端流式推理（含 mock LNNPredictor）
- HybridInferenceEngine 流式扩展 API（infer_stream / infer_windowed）
- StreamingReportRenderer 离线渲染（matplotlib 不可用时降级）
"""

import os
import tempfile
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest

from app.ai.lnn.core import EngineType, InferenceResult
from app.ai.lnn.inference.predictor import LNNPredictor, PredictionResult
from app.ai.lnn.inference.streaming import (
    AnchorContext,
    HiddenStatePage,
    KeyframeDecision,
    KeyframeSelector,
    PagedHiddenStateCache,
    StreamingConfig,
    StreamingPredictor,
    TrajectoryMemory,
)
from app.ai.lnn.engine import HybridInferenceEngine
from app.ai.lnn.visualization import (
    StreamingReportRenderer,
    VisualizationConfig,
    extract_streaming_metrics,
    render_streaming_report,
)


# StreamingConfig


class TestStreamingConfig:
    def test_default_config_valid(self):
        cfg = StreamingConfig()
        cfg.validate()  # should not raise

    def test_invalid_keyframe_interval(self):
        cfg = StreamingConfig(keyframe_interval=0)
        with pytest.raises(ValueError, match="keyframe_interval"):
            cfg.validate()

    def test_invalid_keyframe_mode(self):
        cfg = StreamingConfig(keyframe_mode="unknown")
        with pytest.raises(ValueError, match="未知 keyframe_mode"):
            cfg.validate()

    def test_invalid_anchor_correction_strength(self):
        cfg = StreamingConfig(anchor_correction_strength=1.5)
        with pytest.raises(ValueError, match="anchor_correction_strength"):
            cfg.validate()

    def test_invalid_trajectory_correction_strength(self):
        cfg = StreamingConfig(trajectory_correction_strength=-0.1)
        with pytest.raises(ValueError, match="trajectory_correction_strength"):
            cfg.validate()

    def test_invalid_window_size(self):
        cfg = StreamingConfig(window_size=0)
        with pytest.raises(ValueError, match="window_size"):
            cfg.validate()

    def test_negative_overlap_keyframes(self):
        cfg = StreamingConfig(overlap_keyframes=-1)
        with pytest.raises(ValueError, match="overlap_keyframes"):
            cfg.validate()

    def test_invalid_max_cache_pages(self):
        cfg = StreamingConfig(max_cache_pages=0)
        with pytest.raises(ValueError, match="max_cache_pages"):
            cfg.validate()


# PagedHiddenStateCache


class TestPagedHiddenStateCache:
    def test_put_and_get(self):
        cache = PagedHiddenStateCache(max_pages=8, device="cpu")
        arr = np.array([1.0, 2.0, 3.0])
        cache.put(1, arr)
        retrieved = cache.get(1)
        assert retrieved is not None
        np.testing.assert_array_equal(retrieved, arr)

    def test_get_nonexistent(self):
        cache = PagedHiddenStateCache(max_pages=8, device="cpu")
        assert cache.get(999) is None

    def test_latest_frame_id(self):
        cache = PagedHiddenStateCache(max_pages=8, device="cpu")
        assert cache.latest_frame_id() is None
        cache.put(5, np.array([1.0]))
        cache.put(10, np.array([2.0]))
        assert cache.latest_frame_id() == 10

    def test_recent_frames(self):
        cache = PagedHiddenStateCache(max_pages=8, device="cpu")
        for fid in [1, 3, 5, 7, 9]:
            cache.put(fid, np.array([float(fid)]))
        recent = cache.recent_frames(3)
        assert recent == [5, 7, 9]

    def test_recent_frames_zero(self):
        cache = PagedHiddenStateCache(max_pages=8, device="cpu")
        cache.put(1, np.array([1.0]))
        assert cache.recent_frames(0) == []

    def test_lru_eviction(self):
        cache = PagedHiddenStateCache(max_pages=2, device="cpu")
        cache.put(1, np.array([1.0]))
        cache.put(2, np.array([2.0]))
        # 访问 frame 1 使其变新，frame 2 应被淘汰
        cache.get(1)
        cache.put(3, np.array([3.0]))  # 触发淘汰
        stats = cache.stats()
        assert stats["eviction_count"] >= 1
        assert stats["page_count"] == 2

    def test_clear(self):
        cache = PagedHiddenStateCache(max_pages=8, device="cpu")
        cache.put(1, np.array([1.0]))
        cache.clear()
        assert cache.stats()["page_count"] == 0

    def test_stats_structure(self):
        cache = PagedHiddenStateCache(max_pages=8, device="cpu")
        cache.put(1, np.array([1.0]))
        stats = cache.stats()
        assert "page_count" in stats
        assert "max_pages" in stats
        assert "eviction_count" in stats
        assert "device" in stats

    def test_thread_safety(self):
        """并发写入不应丢失页或导致数据竞争。"""
        cache = PagedHiddenStateCache(max_pages=512, device="cpu")

        def writer(offset):
            for i in range(offset, offset + 50):
                cache.put(i, np.array([float(i)]))

        threads = [threading.Thread(target=writer, args=(o,)) for o in (0, 50, 100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 不一定全部保留（max_pages=512 > 150，所以应该全在）
        assert cache.stats()["page_count"] == 150


# KeyframeSelector


class TestKeyframeSelector:
    def test_interval_mode_first_frame_is_keyframe(self):
        sel = KeyframeSelector(interval=5, mode="interval")
        decision = sel.decide(np.array([[1.0, 2.0]]))
        assert decision.is_keyframe is True
        assert decision.reason == "interval"

    def test_interval_mode_periodic(self):
        sel = KeyframeSelector(interval=3, mode="interval")
        decisions = [sel.decide(np.array([[1.0]])) for _ in range(10)]
        keyframe_flags = [d.is_keyframe for d in decisions]
        # 第 1, 4, 7, 10 帧应为关键帧
        assert keyframe_flags == [True, False, False, True, False, False, True, False, False, True]

    def test_energy_mode_spike_triggers_keyframe(self):
        sel = KeyframeSelector(interval=100, mode="energy", energy_threshold=1.5)
        # 首帧建立基线
        sel.decide(np.array([[1.0, 1.0]]))
        # 稳定帧
        stable = sel.decide(np.array([[1.0, 1.0]]))
        assert stable.is_keyframe is False
        # 能量突变（10x）
        spike = sel.decide(np.array([[10.0, 10.0]]))
        assert spike.is_keyframe is True
        assert "spike" in spike.reason

    def test_hybrid_mode(self):
        sel = KeyframeSelector(interval=3, mode="hybrid")
        for _ in range(5):
            decision = sel.decide(np.array([[1.0]]))
            assert isinstance(decision, KeyframeDecision)
            assert isinstance(decision.is_keyframe, bool)

    def test_reset(self):
        sel = KeyframeSelector(interval=2, mode="interval")
        sel.decide(np.array([[1.0]]))
        sel.reset()
        # reset 后首帧应再次成为关键帧
        decision = sel.decide(np.array([[1.0]]))
        assert decision.is_keyframe is True

    def test_force_flag(self):
        sel = KeyframeSelector(interval=100, mode="interval")
        sel.decide(np.array([[1.0]]))  # 首帧
        decision = sel.decide(np.array([[1.0]]), force_keyframe=True)
        assert decision.is_keyframe is True


# AnchorContext


class TestAnchorContext:
    def test_disabled_passthrough(self):
        anchor = AnchorContext(enabled=False)
        arr = np.array([1.0, 2.0, 3.0])
        corrected, drift = anchor.correct(arr)
        np.testing.assert_array_equal(corrected, arr)
        assert drift == 0.0

    def test_update_and_correct(self):
        anchor = AnchorContext(update_rate=0.5, correction_strength=0.1, enabled=True)
        anchor.update(np.array([1.0, 1.0]))
        corrected, drift = anchor.correct(np.array([2.0, 2.0]))
        # 漂移应 > 0
        assert drift > 0.0
        # 修正后向锚点拉回
        assert corrected[0] < 2.0

    def test_correction_strength_zero_no_correction(self):
        anchor = AnchorContext(update_rate=0.5, correction_strength=0.0, enabled=True)
        anchor.update(np.array([1.0]))
        arr = np.array([2.0])
        corrected, _ = anchor.correct(arr)
        np.testing.assert_array_equal(corrected, arr)

    def test_reset(self):
        anchor = AnchorContext(enabled=True)
        anchor.update(np.array([1.0]))
        anchor.reset()
        assert anchor.stats()["initialized"] is False

    def test_stats_structure(self):
        anchor = AnchorContext(enabled=True)
        anchor.update(np.array([1.0]))
        stats = anchor.stats()
        assert "enabled" in stats
        assert "initialized" in stats
        assert "update_count" in stats
        assert "correction_strength" in stats

    def test_thread_safety(self):
        """并发 update/correct 不应抛异常。"""
        anchor = AnchorContext(update_rate=0.1, correction_strength=0.05)
        anchor.update(np.array([1.0]))

        def worker():
            for _ in range(50):
                anchor.update(np.random.rand(4), is_stable=True)
                anchor.correct(np.random.rand(4))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert anchor.stats()["update_count"] >= 50


# TrajectoryMemory


class TestTrajectoryMemory:
    def test_push_and_correct(self):
        mem = TrajectoryMemory(window_size=8, correction_strength=0.1)
        for v in [1.0, 1.0, 1.0, 1.0]:
            mem.push(np.array([v]))
        corrected, deviation = mem.correct(np.array([2.0]))
        # 与轨迹均值偏差应 > 0
        assert deviation >= 0.0
        # 修正后向均值靠拢
        assert corrected[0] < 2.0

    def test_empty_trajectory_passthrough(self):
        mem = TrajectoryMemory(window_size=8)
        arr = np.array([1.0, 2.0])
        corrected, deviation = mem.correct(arr)
        np.testing.assert_array_equal(corrected, arr)
        assert deviation == 0.0

    def test_correction_strength_zero(self):
        mem = TrajectoryMemory(window_size=4, correction_strength=0.0)
        for v in [1.0, 1.0, 1.0]:
            mem.push(np.array([v]))
        arr = np.array([5.0])
        corrected, _ = mem.correct(arr)
        np.testing.assert_array_equal(corrected, arr)

    def test_window_size_cap(self):
        mem = TrajectoryMemory(window_size=3)
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            mem.push(np.array([v]))
        stats = mem.stats()
        assert stats["current_size"] == 3

    def test_reset(self):
        mem = TrajectoryMemory(window_size=4)
        mem.push(np.array([1.0]))
        mem.reset()
        assert mem.stats()["current_size"] == 0

    def test_thread_safety(self):
        mem = TrajectoryMemory(window_size=64)

        def worker():
            for _ in range(50):
                mem.push(np.array([np.random.rand()]))
                mem.correct(np.array([0.5]))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()


# StreamingPredictor (端到端，使用 mock LNNPredictor)


def _make_mock_predictor(value=0.8, confidence=0.9):
    """构造一个不依赖 torch 的 mock LNNPredictor。"""
    predictor = MagicMock(spec=LNNPredictor)
    predictor.model_name = "test_streaming_model"
    predictor.device = "cpu"

    def mock_preprocess(data):
        arr = np.asarray(data, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        return arr, {"input_shape": arr.shape}

    predictor._preprocess = mock_preprocess

    def mock_predict(input_data, return_confidence=False):
        arr = np.asarray(input_data, dtype=np.float64)
        return PredictionResult(
            value=np.array([[value]]),
            confidence=confidence,
            inference_time=5.0,
            model_info={"name": "test_streaming_model", "device": "cpu"},
        )

    predictor.predict = mock_predict
    return predictor


class TestStreamingPredictor:
    def test_predict_frame_returns_result(self):
        sp = StreamingPredictor(
            predictor=_make_mock_predictor(),
            config=StreamingConfig(keyframe_mode="interval", keyframe_interval=1),
        )
        result = sp.predict_frame(np.array([[1.0, 2.0]]))
        assert isinstance(result, PredictionResult)
        info = result.model_info
        assert info.get("streaming_mode") is True
        assert "is_keyframe" in info
        assert "frame_energy" in info
        assert "anchor_drift" in info
        assert "trajectory_deviation" in info

    def test_predict_stream(self):
        sp = StreamingPredictor(predictor=_make_mock_predictor())
        stream = [np.array([[1.0]]) for _ in range(5)]
        results = list(sp.predict_stream(stream))
        assert len(results) == 5
        assert all(isinstance(r, PredictionResult) for r in results)

    def test_predict_windowed_no_window(self):
        """window_size=None 时退化为逐帧推理。"""
        sp = StreamingPredictor(
            predictor=_make_mock_predictor(),
            config=StreamingConfig(window_size=None),
        )
        data = [np.array([[1.0]]) for _ in range(10)]
        results = sp.predict_windowed(data)
        assert len(results) == 10

    def test_predict_windowed_with_overlap(self):
        sp = StreamingPredictor(
            predictor=_make_mock_predictor(),
            config=StreamingConfig(window_size=4, overlap_keyframes=1),
        )
        data = [np.array([[float(i)]]) for i in range(10)]
        results = sp.predict_windowed(data)
        # 窗口化会因 carryover 拼接导致总帧数 >= 输入帧数
        assert len(results) >= 10
        # 验证关键帧确实被记录（至少首帧必为关键帧）
        kf_count = sum(1 for r in results if r.model_info.get("is_keyframe"))
        assert kf_count >= 1

    def test_force_keyframe(self):
        sp = StreamingPredictor(
            predictor=_make_mock_predictor(),
            config=StreamingConfig(keyframe_mode="interval", keyframe_interval=100),
        )
        # 首帧
        sp.predict_frame(np.array([[1.0]]))
        # 强制关键帧
        result = sp.predict_frame(np.array([[1.0]]), force_keyframe=True)
        assert result.model_info.get("is_keyframe") is True

    def test_get_statistics(self):
        sp = StreamingPredictor(predictor=_make_mock_predictor())
        for _ in range(3):
            sp.predict_frame(np.array([[1.0]]))
        stats = sp.get_statistics()
        assert "total_frames" in stats
        assert stats["total_frames"] == 3
        assert "keyframes" in stats
        assert "cache" in stats
        assert "anchor" in stats
        assert "trajectory" in stats

    def test_reset(self):
        sp = StreamingPredictor(predictor=_make_mock_predictor())
        sp.predict_frame(np.array([[1.0]]))
        sp.reset()
        assert sp.get_statistics()["total_frames"] == 0

    def test_seed_reproducibility(self):
        """相同种子下两次构造的 predictor 应产出相同的关键帧判定序列。"""
        cfg = StreamingConfig(keyframe_mode="energy", keyframe_interval=100, energy_threshold=1.5)
        sp1 = StreamingPredictor(predictor=_make_mock_predictor(), config=cfg, seed=42)
        sp2 = StreamingPredictor(predictor=_make_mock_predictor(), config=cfg, seed=42)
        data_seq = [
            np.array([[1.0, 1.0]]),
            np.array([[1.0, 1.0]]),
            np.array([[10.0, 10.0]]),  # spike
        ]
        flags1 = [sp1.predict_frame(d).model_info["is_keyframe"] for d in data_seq]
        flags2 = [sp2.predict_frame(d).model_info["is_keyframe"] for d in data_seq]
        assert flags1 == flags2


# HybridInferenceEngine 流式扩展


class TestHybridInferenceEngineStreaming:
    def test_register_streaming_predictor(self):
        engine = HybridInferenceEngine()
        sp = StreamingPredictor(predictor=_make_mock_predictor())
        engine.register_streaming_predictor("test_model", sp)
        assert engine.get_engine_stats()["streaming_predictor_count"] == 1
        # 内部 LNNPredictor 也应被同步注册
        assert "test_model" in engine._lnn_predictors

    def test_register_streaming_predictor_empty_name(self):
        engine = HybridInferenceEngine()
        with pytest.raises(ValueError, match="model_name"):
            engine.register_streaming_predictor("", MagicMock())

    def test_build_streaming_predictor(self):
        engine = HybridInferenceEngine()
        engine.register_lnn_predictor("base_model", _make_mock_predictor())
        sp = engine.build_streaming_predictor("base_model")
        assert sp is not None
        assert engine.get_engine_stats()["streaming_predictor_count"] == 1

    def test_build_streaming_predictor_not_registered(self):
        engine = HybridInferenceEngine()
        with pytest.raises(ValueError, match="未注册"):
            engine.build_streaming_predictor("nonexistent")

    def test_infer_stream(self):
        engine = HybridInferenceEngine()
        sp = StreamingPredictor(predictor=_make_mock_predictor())
        engine.register_streaming_predictor("test_model", sp)
        stream = [np.array([[1.0]]) for _ in range(5)]
        results = list(engine.infer_stream("test_model", stream))
        assert len(results) == 5
        assert all(isinstance(r, InferenceResult) for r in results)
        stats = engine.get_engine_stats()
        assert stats["streaming_frames_processed"] == 5

    def test_infer_stream_model_not_registered(self):
        engine = HybridInferenceEngine()
        with pytest.raises(ValueError, match="未注册"):
            list(engine.infer_stream("nonexistent", iter([np.array([[1.0]])])))

    def test_infer_windowed(self):
        engine = HybridInferenceEngine()
        sp = StreamingPredictor(
            predictor=_make_mock_predictor(),
            config=StreamingConfig(window_size=3, overlap_keyframes=1),
        )
        engine.register_streaming_predictor("test_model", sp)
        data = [np.array([[float(i)]]) for i in range(8)]
        results = engine.infer_windowed("test_model", data)
        # 窗口化因 carryover 拼接可能产生 >= 8 个结果
        assert len(results) >= 8
        assert all(isinstance(r, InferenceResult) for r in results)
        stats = engine.get_engine_stats()
        assert stats["streaming_windows_processed"] >= 1
        assert stats["streaming_frames_processed"] >= 8

    def test_reset_streaming(self):
        engine = HybridInferenceEngine()
        sp = StreamingPredictor(predictor=_make_mock_predictor())
        engine.register_streaming_predictor("test_model", sp)
        sp.predict_frame(np.array([[1.0]]))
        engine.reset_streaming("test_model")
        assert sp.get_statistics()["total_frames"] == 0

    def test_engine_stats_includes_streaming_details(self):
        engine = HybridInferenceEngine()
        sp = StreamingPredictor(predictor=_make_mock_predictor())
        engine.register_streaming_predictor("test_model", sp)
        stats = engine.get_engine_stats()
        assert "streaming_details" in stats
        assert "test_model" in stats["streaming_details"]
        assert "streaming_available" in stats

    def test_infer_single_shot_unaffected(self):
        """注册流式预测器后，单次 infer() 仍应正常工作。"""
        engine = HybridInferenceEngine()
        sp = StreamingPredictor(predictor=_make_mock_predictor())
        engine.register_streaming_predictor("test_model", sp)
        result = engine.infer(
            task_description="chatter prediction",
            input_data=np.array([[1.0, 2.0]]),
        )
        assert result is not None
        # infer() 可能返回 FusionResult（有 final_prediction）或
        # InferenceResult（有 prediction）。两种情况都应产出非空预测或回退标记。
        final_pred = getattr(result, "final_prediction", None)
        single_pred = getattr(result, "prediction", None)
        metadata = getattr(result, "metadata", {}) or {}
        has_prediction = final_pred is not None or single_pred is not None
        has_fallback = bool(metadata.get("fallback"))
        assert has_prediction or has_fallback


# Visualization


class TestStreamingReportRenderer:
    def _make_results(self, n=20, with_keyframes=True):
        results = []
        for i in range(n):
            is_kf = with_keyframes and (i % 5 == 0)
            results.append(
                PredictionResult(
                    value=np.array([float(i)]),
                    confidence=0.9 - 0.01 * i,
                    inference_time=5.0 + i * 0.1,
                    model_info={
                        "is_keyframe": is_kf,
                        "keyframe_reason": "interval" if is_kf else None,
                        "frame_energy": float(i * i),
                        "anchor_drift": float(i * 0.01),
                        "trajectory_deviation": float(i * 0.005),
                        "frame_id": i + 1,
                    },
                )
            )
        return results

    def test_extract_streaming_metrics(self):
        results = self._make_results(10)
        metrics = extract_streaming_metrics(results)
        assert metrics["values"].shape == (10,)
        assert metrics["is_keyframe"].sum() == 2  # i=0, 5
        assert metrics["frame_ids"][0] == 1

    def test_render_csv_json(self):
        results = self._make_results(15)
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "report.png")
            renderer = StreamingReportRenderer()
            outputs = renderer.render(
                results=results,
                output_path=out_path,
                model_name="test_model",
            )
            assert "csv_path" in outputs
            assert "json_path" in outputs
            assert os.path.exists(outputs["csv_path"])
            assert os.path.exists(outputs["json_path"])

    def test_render_png_when_mpl_available(self):
        """matplotlib 可用时应产出 PNG。"""
        try:
            import matplotlib  # noqa: F401

            has_mpl = True
        except ImportError:
            has_mpl = False
        if not has_mpl:
            pytest.skip("matplotlib 不可用，跳过 PNG 渲染测试")

        results = self._make_results(20)
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "report.png")
            outputs = render_streaming_report(results, output_path=out_path, model_name="test_model")
            assert "png_path" in outputs
            assert os.path.exists(outputs["png_path"])

    def test_config_seed_reproducibility(self):
        cfg = VisualizationConfig(seed=42)
        assert cfg.seed == 42

    def test_json_statistics_aggregation(self):
        results = self._make_results(10)
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "report.png")
            outputs = render_streaming_report(results, output_path=out_path)
            import json

            with open(outputs["json_path"], encoding="utf-8") as f:
                payload = json.load(f)
            assert payload["frame_count"] == 10
            assert payload["keyframe_count"] == 2
            assert "statistics" in payload
            assert "max_anchor_drift" in payload["statistics"]
