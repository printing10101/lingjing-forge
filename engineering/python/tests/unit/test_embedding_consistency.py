"""Embedding consistency test for the unified manufacturing semantic space.

Verifies that repeated encoding of the same semantic concept produces
consistent embeddings (cosine similarity > 0.9 across 3 trials).

Test dataset: 100 typical manufacturing scenarios.
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

MANUFACTURING_SCENARIOS = [
    "45钢粗车外圆",
    "45钢粗加工",
    "铝合金精铣平面",
    "不锈钢钻孔加工",
    "钛合金高速铣削",
    "铸铁磨削平面",
    "铜合金铰孔加工",
    "45钢调质后半精车",
    "铝合金薄壁件铣削",
    "不锈钢深孔钻削",
    "模具钢淬火后磨削",
    "铝合金型材切断",
    "钛合金叶片铣削",
    "铸铁缸体镗孔",
    "45钢螺纹加工",
    "铝合金壳体铣削",
    "不锈钢管材切割",
    "铜电极电火花加工",
    "高温合金车削",
    "铝合金焊接件加工",
    "45钢淬火后精车",
    "不锈钢法兰钻孔",
    "钛合金框架铣削",
    "铸铁导轨磨削",
    "铝合金压铸件去毛刺",
    "45钢键槽铣削",
    "不锈钢薄板激光切割",
    "铜合金精车外圆",
    "钛合金支架铣削",
    "铝合金散热片加工",
    "45钢调质后铣平面",
    "不锈钢管螺纹加工",
    "钛合金涡轮盘车削",
    "铸铁箱体镗孔",
    "铝合金轮毂加工",
    "45钢轴类零件车削",
    "不锈钢容器焊接后加工",
    "铜基复合材料铣削",
    "钛合金起落架加工",
    "铝合金结构件铣削",
    "45钢花键加工",
    "不锈钢阀门体加工",
    "钛合金医用植入物加工",
    "铸铁发动机缸体加工",
    "铝合金电子封装件加工",
    "45钢齿轮滚齿",
    "不锈钢泵体加工",
    "钛合金航空结构件加工",
    "铜合金导电零件加工",
    "铝合金桁架结构加工",
    "45钢轴承座镗孔",
    "不锈钢反应釜加工",
    "钛合金人工关节加工",
    "铸铁机床床身加工",
    "铝合金列车车体加工",
    "45钢联轴器加工",
    "不锈钢换热器加工",
    "钛合金压气机盘加工",
    "铜合金散热器加工",
    "铝合金船舶结构件加工",
    "45钢曲轴加工",
    "不锈钢压力容器加工",
    "钛合金燃烧室加工",
    "铸铁涡轮壳加工",
    "铝合金光学镜架加工",
    "45钢模具型腔加工",
    "不锈钢医疗器械加工",
    "钛合金紧固件加工",
    "铜合金轴承加工",
    "铝合金机器人手臂加工",
    "45钢凸轮轴加工",
    "不锈钢食品设备加工",
    "钛合金导弹壳体加工",
    "铸铁制动盘加工",
    "铝合金无人机框架加工",
    "45钢连杆加工",
    "不锈钢化工管道加工",
    "钛合金深海设备加工",
    "铜合金电极加工",
    "铝合金卫星结构件加工",
    "45钢活塞加工",
    "不锈钢建筑装饰件加工",
    "钛合金赛车零件加工",
    "铸铁离合器壳体加工",
    "铝合金高铁部件加工",
    "45钢传动轴加工",
    "不锈钢精密仪器加工",
    "钛合金高尔夫球头加工",
    "铜合金接插件加工",
    "铝合金风电部件加工",
    "45钢飞轮加工",
    "不锈钢钟表零件加工",
    "钛合金自行车架加工",
    "铸铁泵体加工",
    "铝合金医疗器械加工",
    "45钢法兰加工",
    "不锈钢弹簧加工",
    "钛合金眼镜架加工",
    "铜合金阀门加工",
    "铝合金光伏支架加工",
    "45钢螺母加工",
    "不锈钢波纹管加工",
    "钛合金假肢加工",
    "铜合金模具加工",
]


class TestEmbeddingConsistency:
    """Tests embedding consistency across repeated encodings of the same concept."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.space = get_embedding_space()
        self.encoder = MultiModalEncoder(seed=42)
        self.consistency_threshold = 0.9
        self.llm_input_dim = 768
        self.lnn_input_dim = 18
        self.jepa_input_dim = 1024

    def _generate_llm_features(self, scenario: str, seed: int) -> np.ndarray:
        rng = np.random.RandomState(seed)
        chars = list(scenario)
        base = np.zeros(self.llm_input_dim, dtype=np.float32)
        for i, ch in enumerate(chars):
            idx = (ord(ch) * 7 + i * 13) % self.llm_input_dim
            base[idx] += 0.05
        base += rng.randn(self.llm_input_dim).astype(np.float32) * 0.001
        return base / (np.linalg.norm(base) + 1e-10)

    def _generate_lnn_features(self, scenario: str, seed: int) -> np.ndarray:
        rng = np.random.RandomState(seed)
        base = np.zeros(self.lnn_input_dim, dtype=np.float32)
        if "粗" in scenario:
            base[0] = 0.8
            base[4] = 0.3
            base[5] = 0.5
        if "精" in scenario:
            base[0] = 0.3
            base[4] = 0.1
            base[5] = 0.2
        if "半精" in scenario:
            base[0] = 0.5
            base[4] = 0.2
            base[5] = 0.35
        if "车" in scenario:
            base[1] = 0.7
        if "铣" in scenario:
            base[2] = 0.7
        if "钻" in scenario:
            base[3] = 0.7
        if "磨" in scenario:
            base[5] = 0.7
        if "镗" in scenario:
            base[6] = 0.7
        if "钢" in scenario:
            base[7] = 0.5
        if "铝" in scenario:
            base[8] = 0.6
        if "钛" in scenario:
            base[9] = 0.7
        if "铸铁" in scenario:
            base[10] = 0.5
        if "铜" in scenario:
            base[11] = 0.4
        if "不锈钢" in scenario:
            base[12] = 0.6
        base += rng.randn(self.lnn_input_dim).astype(np.float32) * 0.005
        return base / (np.linalg.norm(base) + 1e-10)

    def _generate_jepa_features(self, scenario: str, seed: int) -> np.ndarray:
        rng = np.random.RandomState(seed)
        base = np.zeros(self.jepa_input_dim, dtype=np.float32)
        for i, ch in enumerate(scenario):
            idx = (ord(ch) * 11 + i * 17) % self.jepa_input_dim
            base[idx] += 0.03
        base += rng.randn(self.jepa_input_dim).astype(np.float32) * 0.002
        return base / (np.linalg.norm(base) + 1e-10)

    def _compute_cosine(self, a: np.ndarray, b: np.ndarray) -> float:
        a_norm = a / (np.linalg.norm(a) + 1e-10)
        b_norm = b / (np.linalg.norm(b) + 1e-10)
        return float(np.dot(a_norm, b_norm))

    def test_embedding_space_dimensions(self):
        embeddings = self.space.create_empty()
        assert embeddings.shape == (TOTAL_DIMS,), f"Expected shape ({TOTAL_DIMS},), got {embeddings.shape}"

        axes = self.space.decompose(embeddings)
        assert axes["material"].shape == (64,)
        assert axes["process"].shape == (128,)
        assert axes["precision"].shape == (32,)
        assert axes["state"].shape == (128,)
        assert axes["risk"].shape == (32,)
        assert axes["reserved"].shape == (128,)

    def test_llm_embedding_consistency(self):
        """Test LLM embedding consistency: same input → identical or near-identical embeddings.

        Deterministic encoder should produce consistent embeddings for the same input.
        Slight perturbations simulate sensor noise over time intervals.
        """
        for scenario in MANUFACTURING_SCENARIOS[:30]:
            embeddings = []
            for trial in range(3):
                base = self._generate_llm_features(scenario, seed=42)
                rng = np.random.RandomState(42 + trial)
                noise = rng.randn(self.llm_input_dim).astype(np.float32) * 0.001
                features = base + noise
                features = features / (np.linalg.norm(features) + 1e-10)
                emb = self.encoder.encode_llm(features)
                embeddings.append(emb[0] if emb.ndim == 2 else emb)

            sim_12 = self._compute_cosine(embeddings[0], embeddings[1])
            sim_13 = self._compute_cosine(embeddings[0], embeddings[2])
            sim_23 = self._compute_cosine(embeddings[1], embeddings[2])

            assert sim_12 > self.consistency_threshold, (
                f"LLM consistency failure for '{scenario}': "
                f"sim(trial1,trial2)={sim_12:.4f} < {self.consistency_threshold}"
            )
            assert sim_13 > self.consistency_threshold, (
                f"LLM consistency failure for '{scenario}': "
                f"sim(trial1,trial3)={sim_13:.4f} < {self.consistency_threshold}"
            )
            assert sim_23 > self.consistency_threshold, (
                f"LLM consistency failure for '{scenario}': "
                f"sim(trial2,trial3)={sim_23:.4f} < {self.consistency_threshold}"
            )

    def test_lnn_embedding_consistency(self):
        """Test LNN embedding consistency: same input with small perturbations."""
        for scenario in MANUFACTURING_SCENARIOS[:30]:
            embeddings = []
            for trial in range(3):
                base = self._generate_lnn_features(scenario, seed=42)
                rng = np.random.RandomState(42 + trial)
                noise = rng.randn(self.lnn_input_dim).astype(np.float32) * 0.005
                features = base + noise
                features = features / (np.linalg.norm(features) + 1e-10)
                emb = self.encoder.encode_lnn(features)
                embeddings.append(emb[0] if emb.ndim == 2 else emb)

            sim_12 = self._compute_cosine(embeddings[0], embeddings[1])
            sim_13 = self._compute_cosine(embeddings[0], embeddings[2])

            assert sim_12 > self.consistency_threshold, (
                f"LNN consistency failure for '{scenario}': sim(trial1,trial2)={sim_12:.4f}"
            )
            assert sim_13 > self.consistency_threshold, (
                f"LNN consistency failure for '{scenario}': sim(trial1,trial3)={sim_13:.4f}"
            )

    def test_jepa_embedding_consistency(self):
        """Test JEPA embedding consistency: same input with small perturbations."""
        for scenario in MANUFACTURING_SCENARIOS[:30]:
            embeddings = []
            for trial in range(3):
                base = self._generate_jepa_features(scenario, seed=42)
                rng = np.random.RandomState(42 + trial)
                noise = rng.randn(self.jepa_input_dim).astype(np.float32) * 0.002
                features = base + noise
                features = features / (np.linalg.norm(features) + 1e-10)
                emb = self.encoder.encode_jepa(features)
                embeddings.append(emb[0] if emb.ndim == 2 else emb)

            sim_12 = self._compute_cosine(embeddings[0], embeddings[1])
            sim_13 = self._compute_cosine(embeddings[0], embeddings[2])

            assert sim_12 > self.consistency_threshold, (
                f"JEPA consistency failure for '{scenario}': sim(trial1,trial2)={sim_12:.4f}"
            )
            assert sim_13 > self.consistency_threshold, (
                f"JEPA consistency failure for '{scenario}': sim(trial1,trial3)={sim_13:.4f}"
            )

    def test_identical_input_deterministic(self):
        """Verifies encoder is 100% deterministic for identical inputs."""
        scenario = "45钢粗加工"
        features = self._generate_llm_features(scenario, seed=42)
        emb1 = self.encoder.encode_llm(features)[0]
        time.sleep(0.1)  # Simulate time interval
        emb2 = self.encoder.encode_llm(features)[0]
        sim = self._compute_cosine(emb1, emb2)
        assert sim > 0.9999, f"Identical input should produce identical output, got sim={sim:.6f}"

    def test_material_axis_encoding(self):
        """Verify material axis encoding produces sensible values in [-1, 1]."""
        material = MaterialAxis()
        vec = material.encode_material(
            hardness_hb=200.0,
            thermal_conductivity=50.0,
            ductility_pct=30.0,
            tensile_strength_mpa=600.0,
            density_gcm3=7.85,
            elastic_modulus_gpa=210.0,
        )
        assert np.all(vec >= -1.0) and np.all(vec <= 1.0), "Material values out of [-1, 1]"
        assert vec.shape == (64,)

        full_emb = self.space.compose(material_vec=vec)
        assert full_emb.shape == (TOTAL_DIMS,)

    def test_process_axis_encoding(self):
        """Verify process axis encoding for different manufacturing methods."""
        process = ProcessAxis()
        for method in ["turning", "milling", "drilling", "grinding"]:
            vec = process.encode_process_type(method)
            assert vec.shape == (32,)
            assert np.any(vec > 0.5), f"Process type '{method}' not well encoded"

        combined = np.concatenate([
            process.encode_process_type("turning"),
            process.encode_parameters(feed_rate=500.0, depth_of_cut=2.0, spindle_speed=8000.0),
            process.encode_sequence([{"type": "turning"}, {"type": "milling"}]),
            process.encode_tool_geometry(tool_diameter=20.0, tool_length=100.0),
        ])
        assert combined.shape == (128,)

        full_emb = self.space.compose(process_vec=combined)
        assert full_emb.shape == (TOTAL_DIMS,)

    def test_precision_axis_encoding(self):
        """Verify precision axis encoding and IT grade mapping."""
        precision = PrecisionAxis()
        for grade in ["IT5", "IT8", "IT12"]:
            val = precision.it_grade_to_value(grade)
            assert -1.0 <= val <= 1.0, f"IT grade {grade} value {val} out of range"

        vec = precision.encode_precision(it_grade="IT7", surface_roughness_ra=1.6)
        assert vec.shape == (32,)
        assert np.all(vec >= -1.0) and np.all(vec <= 1.0)

    def test_state_axis_encoding(self):
        """Verify state axis encoding with sensor fusion."""
        state = StateAxis()
        vec = state.encode_state(
            vibration_x=5.0, vibration_y=3.0, vibration_z=2.0,
            spindle_temp=45.0, tool_temp=120.0, coolant_temp=25.0,
            flank_wear=0.15, spindle_power=5.0, spindle_load=60.0,
        )
        assert vec.shape == (128,)
        assert np.all(vec >= -1.0) and np.all(vec <= 1.0)

        sensor_vec = state.encode_sensor_fusion({
            "vibration_x": 5.0, "vibration_y": 3.0, "vibration_z": 2.0,
            "spindle_temp": 45.0, "tool_temp": 120.0,
        })
        assert sensor_vec.shape == (128,)

    def test_risk_axis_encoding(self):
        """Verify risk axis encoding with 0-1 normalized values."""
        risk = RiskAxis()
        vec = risk.encode_risk(
            collision_prob=0.1, breakage_prob=0.05,
            thermal_risk=0.2, quality_risk=0.15,
        )
        assert vec.shape == (32,)
        assert np.all(vec >= 0.0) and np.all(vec <= 1.0)

    def test_full_embedding_composition(self):
        """Test composing a full 512-dim embedding from all axes."""
        material = MaterialAxis()
        process = ProcessAxis()
        precision = PrecisionAxis()
        state = StateAxis()
        risk = RiskAxis()

        full = self.space.compose(
            material_vec=material.encode_material(hardness_hb=200.0),
            process_vec=np.concatenate([
                process.encode_process_type("milling"),
                process.encode_parameters(500.0, 2.0, 8000.0),
                process.encode_sequence([{"type": "milling"}]),
                process.encode_tool_geometry(20.0, 100.0),
            ]),
            precision_vec=precision.encode_precision("IT7", 1.6),
            state_vec=state.encode_state(vibration_x=5.0),
            risk_vec=risk.encode_risk(collision_prob=0.1),
        )
        assert full.shape == (TOTAL_DIMS,)
        metrics = self.space.validate(full)
        assert "total_norm" in metrics

    def test_embedding_normalization(self):
        """Verify normalization produces unit-norm vectors."""
        emb = self.space.create_empty()
        emb[0:64] = np.random.randn(64).astype(np.float32)
        normalized = self.space.normalize(emb)
        norm = np.linalg.norm(normalized)
        assert abs(norm - 1.0) < 1e-6, f"Normalized norm is {norm}, expected ~1.0"

    def test_axis_similarity(self):
        """Verify per-axis similarity decomposition."""
        emb_a = self.space.create_empty()
        emb_b = self.space.create_empty()
        emb_a[0:64] = np.ones(64, dtype=np.float32) * 0.5
        emb_b[0:64] = np.ones(64, dtype=np.float32) * 0.5

        axis_sims = self.space.axis_similarity(emb_a, emb_b)
        assert "material" in axis_sims
        assert axis_sims["material"] > 0.99

    def test_embedding_schema_export(self):
        """Verify embedding space schema export."""
        schema = self.space.to_schema()
        assert schema["total_dims"] == TOTAL_DIMS
        assert "axes" in schema
        assert "material" in schema["axes"]
        assert "process" in schema["axes"]
        assert "reserved" in schema

    def test_100_scenario_consistency_batch(self):
        """Extended test: verify consistency across all 100 manufacturing scenarios."""
        failures = 0
        for scenario in MANUFACTURING_SCENARIOS:
            features = []
            for trial in range(3):
                f = self._generate_llm_features(scenario, seed=42 * (trial + 1))
                features.append(f)
            emb = [self.encoder.encode_llm(f)[0] for f in features]
            sim = self._compute_cosine(emb[0], emb[1])
            if sim < 0.85:
                failures += 1

        failure_rate = failures / len(MANUFACTURING_SCENARIOS)
        assert failure_rate < 0.05, (
            f"Consistency failure rate {failure_rate:.2%} exceeds 5% threshold: "
            f"{failures}/{len(MANUFACTURING_SCENARIOS)} scenarios failed"
        )

    def test_different_concepts_distinct(self):
        """Verify that different concepts produce distinct embeddings."""
        emb_a = self.encoder.encode_llm(
            self._generate_llm_features("45钢粗车外圆", seed=42)
        )[0]
        emb_b = self.encoder.encode_llm(
            self._generate_llm_features("铝合金精铣平面", seed=42)
        )[0]
        emb_c = self.encoder.encode_llm(
            self._generate_llm_features("不锈钢钻孔加工", seed=42)
        )[0]

        sim_ab = self._compute_cosine(emb_a, emb_b)
        sim_ac = self._compute_cosine(emb_a, emb_c)
        sim_bc = self._compute_cosine(emb_b, emb_c)

        assert sim_ab < 0.999, "Different concepts should not be identical"
        assert sim_ac < 0.999, "Different concepts should not be identical"
        assert sim_bc < 0.999, "Different concepts should not be identical"
