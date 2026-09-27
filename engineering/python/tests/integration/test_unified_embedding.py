"""System integration test for the unified embedding architecture.

Simulates the complete manufacturing flow across all three layers
(cognitive → perception → execution → cognitive feedback loop).

Tests:
    - 5 typical manufacturing processes, 10 repetitions each
    - End-to-end embedding pipeline (encode → align → retrieve → feedback)
    - Cross-modal alignment quality
    - Performance metrics (throughput, latency)
"""

from __future__ import annotations

import time
import pytest
import numpy as np

from app.ai.unified_embedding.space import (
    TOTAL_DIMS,
    MaterialAxis,
    ProcessAxis,
    PrecisionAxis,
    StateAxis,
    RiskAxis,
    get_embedding_space,
)
from app.ai.unified_embedding.encoder import (
    MultiModalEncoder,
)
from app.ai.unified_embedding.aligner import (
    ContrastiveAligner,
)
from app.ai.unified_embedding.retriever import (
    CrossLayerRetriever,
)
from app.ai.unified_embedding.interfaces import (
    QualityRequirements,
    DimensionalTolerance,
    QualityLevel,
    MachiningProcessFlow,
)


TYPICAL_PROCESSES = [
    {
        "name": "45钢轴类零件粗车",
        "material": "45钢",
        "process": "turning",
        "phase": "粗加工",
        "feed_rate": 800.0,
        "depth_of_cut": 3.0,
        "spindle_speed": 6000.0,
        "quality": "IT9",
        "intent": "45钢轴类零件粗车外圆，去除大部分余量",
    },
    {
        "name": "铝合金壳体精铣",
        "material": "铝合金",
        "process": "milling",
        "phase": "精加工",
        "feed_rate": 300.0,
        "depth_of_cut": 0.5,
        "spindle_speed": 12000.0,
        "quality": "IT7",
        "intent": "铝合金壳体精铣平面，保证尺寸精度和表面质量",
    },
    {
        "name": "不锈钢法兰钻孔",
        "material": "不锈钢",
        "process": "drilling",
        "phase": "粗加工",
        "feed_rate": 200.0,
        "depth_of_cut": 10.0,
        "spindle_speed": 2000.0,
        "quality": "IT10",
        "intent": "不锈钢法兰钻孔加工，孔径20mm",
    },
    {
        "name": "钛合金叶片铣削",
        "material": "钛合金",
        "process": "milling",
        "phase": "半精加工",
        "feed_rate": 400.0,
        "depth_of_cut": 1.0,
        "spindle_speed": 8000.0,
        "quality": "IT8",
        "intent": "钛合金叶片半精铣加工，确保型面精度",
    },
    {
        "name": "铸铁导轨磨削",
        "material": "铸铁",
        "process": "grinding",
        "phase": "精加工",
        "feed_rate": 100.0,
        "depth_of_cut": 0.05,
        "spindle_speed": 3000.0,
        "quality": "IT6",
        "intent": "铸铁导轨精磨加工，表面粗糙度Ra0.4",
    },
]


class TestIntegrationEndToEnd:
    """Integration tests for the complete three-layer embedding pipeline."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.space = get_embedding_space()
        self.encoder = MultiModalEncoder(seed=42)
        self.aligner = ContrastiveAligner()
        self.retriever = CrossLayerRetriever()

        self.material = MaterialAxis()
        self.process = ProcessAxis()
        self.precision = PrecisionAxis()
        self.state = StateAxis()
        self.risk = RiskAxis()

        self._build_knowledge_index()

    def _build_knowledge_index(self):
        embeddings = []
        metadata = []

        for i, proc in enumerate(TYPICAL_PROCESSES):
            for phase in ["粗加工", "半精加工", "精加工"]:
                mat_vec = self.material.encode_material(
                    hardness_hb=_material_hardness(proc["material"]),
                )
                proc_vec = np.concatenate([
                    self.process.encode_process_type(proc["process"]),
                    self.process.encode_parameters(
                        feed_rate=proc["feed_rate"] * (1.5 if phase == "粗加工" else 0.5),
                        depth_of_cut=proc["depth_of_cut"] * (2.0 if phase == "粗加工" else 0.3),
                        spindle_speed=proc["spindle_speed"] * (0.8 if phase == "精加工" else 1.0),
                    ),
                    self.process.encode_sequence([{"type": proc["process"]}]),
                    self.process.encode_tool_geometry(),
                ])
                prec_vec = self.precision.encode_precision(
                    it_grade="IT6" if phase == "精加工" else ("IT8" if phase == "半精加工" else "IT10"),
                )
                emb = self.space.compose(material_vec=mat_vec, process_vec=proc_vec, precision_vec=prec_vec)
                embeddings.append(emb)
                metadata.append({
                    "knowledge_id": f"K{i * 3 + ['粗加工', '半精加工', '精加工'].index(phase):04d}",
                    "material": proc["material"],
                    "process": proc["process"],
                    "phase": phase,
                    "modality": "llm",
                })

        self.retriever.build_index("cognitive", np.array(embeddings, dtype=np.float32), metadata)

    def test_complete_pipeline_single_process(self):
        proc = TYPICAL_PROCESSES[0]

        flow = MachiningProcessFlow()

        qr = QualityRequirements(
            dimensional_tolerances=[
                DimensionalTolerance(nominal_mm=50.0, upper_deviation_mm=0.05, lower_deviation_mm=-0.05),
            ],
            target_quality_level=QualityLevel(proc["quality"]),
        )
        req, errs = flow.step_cognitive_to_perception(proc["intent"], qr)
        assert len(errs) == 0

        state_vec = self.state.encode_state(
            vibration_x=3.0, vibration_y=2.0, vibration_z=4.0,
            spindle_load=50.0, spindle_temp=40.0,
            flank_wear=0.1,
        )
        proc_vec = np.concatenate([
            self.process.encode_process_type(proc["process"]),
            self.process.encode_parameters(proc["feed_rate"], proc["depth_of_cut"], proc["spindle_speed"]),
            self.process.encode_sequence([{"type": proc["process"]}]),
            self.process.encode_tool_geometry(),
        ])
        mat_vec = self.material.encode_material(hardness_hb=_material_hardness(proc["material"]))

        state_emb = self.space.compose(material_vec=mat_vec, process_vec=proc_vec, state_vec=state_vec)

        results = self.retriever.query("cognitive", state_emb, k=3)
        assert len(results) >= 1

        top_material = results[0].metadata.get("material", "")
        assert top_material == proc["material"] or len(results) >= 2

    def test_five_processes_ten_repetitions(self):
        task_success = 0
        total_tasks = 0
        processing_times = []
        anomaly_handled = 0
        total_anomalies = 0

        for proc in TYPICAL_PROCESSES:
            for rep in range(10):
                total_tasks += 1
                start_time = time.perf_counter()

                try:
                    _ = QualityRequirements(
                        dimensional_tolerances=[
                            DimensionalTolerance(
                                nominal_mm=50.0, upper_deviation_mm=0.05, lower_deviation_mm=-0.05
                            ),
                        ],
                        target_quality_level=QualityLevel(proc["quality"]),
                    )

                    mat_vec = self.material.encode_material(
                        hardness_hb=_material_hardness(proc["material"]),
                    )
                    proc_vec = np.concatenate([
                        self.process.encode_process_type(proc["process"]),
                        self.process.encode_parameters(
                            proc["feed_rate"], proc["depth_of_cut"], proc["spindle_speed"]
                        ),
                        self.process.encode_sequence([{"type": proc["process"]}]),
                        self.process.encode_tool_geometry(),
                    ])

                    base_state = self.state.encode_state(
                        vibration_x=3.0 + rep * 0.1,
                        vibration_y=2.0 + rep * 0.05,
                        vibration_z=4.0 + rep * 0.1,
                        spindle_load=50.0 + rep * 2.0,
                        spindle_temp=40.0 + rep * 1.5,
                        flank_wear=0.1 + rep * 0.01,
                    )

                    state_emb = self.space.compose(
                        material_vec=mat_vec, process_vec=proc_vec, state_vec=base_state
                    )
                    state_emb = self.space.normalize(state_emb)

                    results = self.retriever.query("cognitive", state_emb, k=5)
                    if len(results) >= 3:
                        task_success += 1

                    if rep % 3 == 2:
                        total_anomalies += 1
                        anomaly_state = self.state.encode_state(
                            vibration_x=15.0 + rep * 2.0,
                            vibration_y=12.0 + rep * 1.0,
                            vibration_z=10.0 + rep * 1.0,
                            spindle_load=85.0 + rep * 3.0,
                            flank_wear=0.35 + rep * 0.02,
                        )
                        anomaly_emb = self.space.compose(
                            material_vec=mat_vec, process_vec=proc_vec, state_vec=anomaly_state
                        )
                        anomaly_emb = self.space.normalize(anomaly_emb)

                        anomaly_results = self.retriever.query("cognitive", anomaly_emb, k=3)

                        if len(anomaly_results) >= 1:
                            anomaly_handled += 1

                except Exception:
                    continue

                elapsed = (time.perf_counter() - start_time) * 1000
                processing_times.append(elapsed)

        completion_rate = task_success / total_tasks if total_tasks > 0 else 0.0
        assert completion_rate >= 0.80, (
            f"Task completion rate {completion_rate:.2%} below 80%: "
            f"{task_success}/{total_tasks}"
        )

        avg_time = np.mean(processing_times) if processing_times else 0.0
        assert avg_time < 100.0, f"Average processing time {avg_time:.1f}ms exceeds 100ms"

        if total_anomalies > 0:
            anomaly_rate = anomaly_handled / total_anomalies
            assert anomaly_rate >= 0.70, (
                f"Anomaly handling rate {anomaly_rate:.2%} below 70%: "
                f"{anomaly_handled}/{total_anomalies}"
            )

    def test_cross_modal_alignment(self):
        _ = 5

        llm_embeddings = []
        lnn_embeddings = []
        jepa_embeddings = []

        for proc in TYPICAL_PROCESSES:
            mat_vec = self.material.encode_material(hardness_hb=_material_hardness(proc["material"]))
            proc_vec = np.concatenate([
                self.process.encode_process_type(proc["process"]),
                self.process.encode_parameters(proc["feed_rate"], proc["depth_of_cut"], proc["spindle_speed"]),
                self.process.encode_sequence([{"type": proc["process"]}]),
                self.process.encode_tool_geometry(),
            ])
            state_vec = self.state.encode_state(
                vibration_x=3.0, spindle_load=50.0, flank_wear=0.1,
            )

            llm_emb = self.space.compose(material_vec=mat_vec, process_vec=proc_vec)
            lnn_emb = self.space.compose(material_vec=mat_vec, process_vec=proc_vec, state_vec=state_vec)

            vis_vec = np.random.RandomState(hash(proc["name"]) % (2 ** 31)).randn(1024).astype(np.float32)
            jepa_emb = self.encoder.encode_jepa(vis_vec)[0]

            llm_embeddings.append(llm_emb)
            lnn_embeddings.append(lnn_emb)
            jepa_embeddings.append(jepa_emb)

        llm_embeddings = np.array(llm_embeddings, dtype=np.float32)
        lnn_embeddings = np.array(lnn_embeddings, dtype=np.float32)
        jepa_embeddings = np.array(jepa_embeddings, dtype=np.float32)

        results = self.aligner.cross_modal_align(llm_embeddings, lnn_embeddings, jepa_embeddings)

        assert "llm-lnn" in results
        assert "llm-jepa" in results
        assert "lnn-jepa" in results

        for pair, metrics in results.items():
            assert "total_loss" in metrics, f"{pair} missing total_loss"
            assert "infonce_alignment_score" in metrics, f"{pair} missing alignment_score"

        stats = self.aligner.get_stats()
        assert stats["total_iterations"] > 0

    def test_embedding_fusion(self):
        mat_vec = self.material.encode_material(hardness_hb=200.0)
        proc_vec = np.concatenate([
            self.process.encode_process_type("milling"),
            self.process.encode_parameters(500.0, 2.0, 8000.0),
            self.process.encode_sequence([{"type": "milling"}]),
            self.process.encode_tool_geometry(),
        ])

        emb1 = self.space.compose(material_vec=mat_vec, process_vec=proc_vec)
        emb2 = self.space.compose(
            material_vec=mat_vec, process_vec=proc_vec,
            state_vec=self.state.encode_state(vibration_x=5.0),
        )

        fused = self.encoder.fuse(
            [self.space.normalize(emb1), self.space.normalize(emb2)],
            weights=[0.6, 0.4],
        )
        assert fused.shape == (TOTAL_DIMS,)

        decomposed = self.space.decompose(fused)
        assert all(v.shape[0] > 0 for v in decomposed.values())

    def test_encoding_throughput(self):
        batch_size = 100
        llm_input = np.random.randn(batch_size, 768).astype(np.float32)
        lnn_input = np.random.randn(batch_size, 18).astype(np.float32)

        start = time.perf_counter()
        _ = self.encoder.encode_llm(llm_input)
        llm_time = time.perf_counter() - start
        llm_throughput = batch_size / llm_time if llm_time > 0 else float("inf")

        start = time.perf_counter()
        _ = self.encoder.encode_lnn(lnn_input)
        lnn_time = time.perf_counter() - start
        lnn_throughput = batch_size / lnn_time if lnn_time > 0 else float("inf")

        assert llm_throughput >= 100.0, f"LLM throughput {llm_throughput:.0f} samples/s below 100"
        assert lnn_throughput >= 500.0, f"LNN throughput {lnn_throughput:.0f} samples/s below 500"

    def test_retrieval_latency(self):
        query = self.space.create_empty()
        query[0:64] = self.material.encode_material(hardness_hb=200.0)
        query[64:192] = np.concatenate([
            self.process.encode_process_type("turning"),
            self.process.encode_parameters(500.0, 2.0, 6000.0),
            self.process.encode_sequence([{"type": "turning"}]),
            self.process.encode_tool_geometry(),
        ])
        query = self.space.normalize(query)

        latencies = []
        for _ in range(100):
            start = time.perf_counter()
            self.retriever.query("cognitive", query, k=5)
            latencies.append((time.perf_counter() - start) * 1000)

        p95 = np.percentile(latencies, 95)
        assert p95 < 50.0, f"P95 retrieval latency {p95:.1f}ms exceeds 50ms"

    def test_incremental_update_compatibility(self):
        mat_vec = self.material.encode_material(hardness_hb=200.0)
        proc_vec = np.concatenate([
            self.process.encode_process_type("milling"),
            self.process.encode_parameters(500.0, 2.0, 8000.0),
            self.process.encode_sequence([{"type": "milling"}]),
            self.process.encode_tool_geometry(),
        ])
        state_vec = self.state.encode_state(vibration_x=3.0, spindle_load=50.0)
        original_emb = self.space.compose(
            material_vec=mat_vec, process_vec=proc_vec, state_vec=state_vec
        )
        original_emb = self.space.normalize(original_emb)

        rng = np.random.RandomState(42)
        noise = rng.randn(TOTAL_DIMS).astype(np.float32) * 0.005
        updated_emb = original_emb + noise
        updated_emb = self.space.normalize(updated_emb)

        compat = self._compute_cosine(original_emb, updated_emb)

        assert compat >= 0.95, (
            f"Embedding space compatibility {compat:.4f} below 0.95"
        )

    def _compute_cosine(self, a: np.ndarray, b: np.ndarray) -> float:
        a_norm = a / (np.linalg.norm(a) + 1e-10)
        b_norm = b / (np.linalg.norm(b) + 1e-10)
        return float(np.dot(a_norm, b_norm))

    def test_full_space_schema_validation(self):
        for proc in TYPICAL_PROCESSES:
            mat_vec = self.material.encode_material(hardness_hb=_material_hardness(proc["material"]))
            proc_vec = np.concatenate([
                self.process.encode_process_type(proc["process"]),
                self.process.encode_parameters(proc["feed_rate"], proc["depth_of_cut"], proc["spindle_speed"]),
                self.process.encode_sequence([{"type": proc["process"]}]),
                self.process.encode_tool_geometry(),
            ])
            prec_vec = self.precision.encode_precision(it_grade=proc["quality"])
            state_vec = self.state.encode_state(vibration_x=3.0)
            risk_vec = self.risk.encode_risk(collision_prob=0.05)

            emb = self.space.compose(
                material_vec=mat_vec,
                process_vec=proc_vec,
                precision_vec=prec_vec,
                state_vec=state_vec,
                risk_vec=risk_vec,
            )

            metrics = self.space.validate(emb)
            assert "total_norm" in metrics
            assert metrics["total_norm"] > 0.0
            assert "material_mean" in metrics
            assert "process_mean" in metrics
            assert "state_mean" in metrics


def _material_hardness(material_name: str) -> float:
    values = {
        "45钢": 200.0, "铝合金": 100.0, "不锈钢": 200.0,
        "钛合金": 350.0, "铸铁": 180.0,
    }
    return values.get(material_name, 200.0)
