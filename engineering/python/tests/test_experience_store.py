"""
Test Experience Store Implementation

Tests for:
- ExperienceStore: Experience storage and retrieval with relevance scoring
- Experience dataclass
- Ground truth integration
- Material-based search and statistics
- Reliability scoring
"""

import pytest
import tempfile
import shutil
import threading
from pathlib import Path

from app.services.experience_store import ExperienceStore, Experience


@pytest.fixture
def temp_storage_dir():
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def store(temp_storage_dir):
    return ExperienceStore(storage_dir=temp_storage_dir)


@pytest.fixture
def store_with_experiences(temp_storage_dir):
    store = ExperienceStore(storage_dir=temp_storage_dir)

    experiences = [
        {
            "parameters": {"material": "45钢", "process": "车削", "cutting_speed": 120},
            "metrics": {"accuracy": 0.92, "mean_wear_rate": 0.002},
            "validation_result": {"is_valid": True},
            "metadata": {"source": "test"},
        },
        {
            "parameters": {
                "material": "不锈钢",
                "process": "铣削",
                "cutting_speed": 100,
            },
            "metrics": {"accuracy": 0.88, "mean_wear_rate": 0.003},
            "validation_result": {"is_valid": True},
            "metadata": {"source": "test"},
        },
        {
            "parameters": {"material": "45钢", "process": "车削", "cutting_speed": 150},
            "metrics": {"accuracy": 0.95, "mean_wear_rate": 0.0015},
            "validation_result": {"is_valid": True},
            "metadata": {"source": "test"},
        },
    ]

    for i, exp in enumerate(experiences):
        store.save_experience(
            task_id=f"task_{i}", experience=exp, process=exp["parameters"]["process"]
        )

    return store


class TestExperienceDataclass:
    """Test Experience dataclass"""

    def test_default_values(self):
        exp = Experience(
            experience_id="exp1",
            task_id="task1",
            process="车削",
            parameters={},
            metrics={},
            validation_result={},
        )
        assert exp.experience_id == "exp1"
        assert exp.task_id == "task1"
        assert exp.process == "车削"
        assert exp.ground_truth_validation == {}
        assert exp.metadata == {}
        assert exp.created_at is not None
        assert exp.updated_at is not None

    def test_custom_metadata(self):
        exp = Experience(
            experience_id="exp2",
            task_id="task2",
            process="铣削",
            parameters={"speed": 100},
            metrics={"accuracy": 0.9},
            validation_result={"is_valid": True},
            ground_truth_validation={"consistency": 0.95},
            metadata={"source": "production"},
        )
        assert exp.metadata["source"] == "production"
        assert exp.ground_truth_validation["consistency"] == 0.95


class TestExperienceStoreInitialization:
    """Test store initialization"""

    def test_creates_storage_directory(self, temp_storage_dir):
        ExperienceStore(storage_dir=temp_storage_dir)
        assert Path(temp_storage_dir).exists()

    def test_initializes_empty(self, store):
        experiences = store.list_experiences()
        assert len(experiences) == 0

    def test_loads_existing_experiences(self, temp_storage_dir):
        exp_dir = Path(temp_storage_dir)
        exp_dir.mkdir(parents=True, exist_ok=True)

        import json

        with open(exp_dir / "experiences.json", "w") as f:
            json.dump(
                {
                    "experiences": {
                        "exp1": {
                            "experience_id": "exp1",
                            "task_id": "task1",
                            "process": "test",
                            "parameters": {},
                            "metrics": {},
                            "validation_result": {},
                            "created_at": "2024-01-01T00:00:00",
                            "updated_at": "2024-01-01T00:00:00",
                        }
                    },
                    "validation_history": {},
                    "updated_at": "2024-01-01T00:00:00",
                },
                f,
            )

        store = ExperienceStore(storage_dir=temp_storage_dir)
        assert len(store.list_experiences()) == 1


class TestExperienceStoreSave:
    """Test experience saving"""

    def test_save_experience(self, store):
        experience = {
            "parameters": {"material": "45钢", "speed": 120},
            "metrics": {"accuracy": 0.95},
            "validation_result": {"is_valid": True},
        }

        result = store.save_experience(task_id="task1", experience=experience)

        assert "experience_id" in result
        assert result["status"] == "saved"

    def test_save_experience_with_process(self, store):
        experience = {
            "parameters": {"material": "不锈钢"},
            "metrics": {"accuracy": 0.90},
            "validation_result": {},
        }

        result = store.save_experience(
            task_id="task2",
            experience=experience,
            process="车削",
        )

        saved = store.get_experience(result["experience_id"])
        assert saved.process == "车削"

    def test_save_experience_generates_unique_id(self, store):
        for i in range(10):
            result = store.save_experience(
                task_id=f"task_{i}",
                experience={"parameters": {}, "metrics": {}},
            )
            task_str = f"task_{i}"
            assert result["experience_id"].endswith(f"-{task_str[:8]}")

    def test_save_experience_persists_to_disk(self, temp_storage_dir):
        store = ExperienceStore(storage_dir=temp_storage_dir)
        store.save_experience(
            task_id="persist_test",
            experience={"parameters": {"key": "value"}, "metrics": {}},
        )

        store2 = ExperienceStore(storage_dir=temp_storage_dir)
        experiences = store2.list_experiences()
        assert len(experiences) == 1


class TestExperienceStoreGet:
    """Test experience retrieval"""

    def test_get_existing_experience(self, store_with_experiences):
        exp_id = store_with_experiences.list_experiences()[0]["experience_id"]
        exp = store_with_experiences.get_experience(exp_id)

        assert exp is not None
        assert exp.experience_id == exp_id

    def test_get_nonexistent_experience(self, store):
        exp = store.get_experience("nonexistent_id")
        assert exp is None


class TestExperienceStoreList:
    """Test experience listing"""

    def test_list_all_experiences(self, store_with_experiences):
        results = store_with_experiences.list_experiences()
        assert len(results) == 3

    def test_list_with_limit(self, store_with_experiences):
        results = store_with_experiences.list_experiences(limit=2)
        assert len(results) == 2

    def test_list_with_process_filter(self, store_with_experiences):
        results = store_with_experiences.list_experiences(filters={"process": "车削"})
        assert all(r["process"] == "车削" for r in results)
        assert len(results) == 2

    def test_list_with_material_filter(self, store_with_experiences):
        results = store_with_experiences.list_experiences(filters={"process": "车削"})
        assert all(r["process"] == "车削" for r in results)
        assert len(results) == 2

    def test_list_empty_filters(self, store):
        results = store.list_experiences(filters={})
        assert len(results) == 0


class TestExperienceStoreRelevanceScoring:
    """Test relevance score calculation"""

    def test_relevance_score_process_match(self, store_with_experiences):
        query = {"process": "车削"}
        scores = []

        for exp_dict in store_with_experiences.list_experiences():
            score = store_with_experiences._calculate_relevance_score(exp_dict, query)
            scores.append(score)

        max_score = max(scores)
        assert max_score > 0

    def test_relevance_score_parameter_similarity(self, store_with_experiences):
        query = {
            "parameters": {"cutting_speed": 120},
        }
        exp_dict = store_with_experiences.list_experiences()[0]

        score = store_with_experiences._calculate_relevance_score(exp_dict, query)
        assert score >= 0

    def test_relevance_score_metric_similarity(self, store_with_experiences):
        query = {"metrics": {"accuracy": 0.92}}
        exp_dict = store_with_experiences.list_experiences()[0]

        score = store_with_experiences._calculate_relevance_score(exp_dict, query)
        assert score >= 0


class TestExperienceStoreReliability:
    """Test reliability scoring"""

    def test_reliability_no_validations(self, store):
        result = store.save_experience(
            task_id="no_val",
            experience={
                "parameters": {},
                "metrics": {},
                "validation_result": {},
            },
        )

        reliability = store.get_experience_reliability(result["experience_id"])
        assert reliability["validation_count"] == 0
        assert reliability["consistency_rate"] == 0.0
        assert reliability["reliability_score"] == 0.5

    def test_reliability_with_ground_truth_valid(self, store):
        result = store.save_experience(
            task_id="gt_valid",
            experience={
                "parameters": {},
                "metrics": {},
                "validation_result": {},
            },
        )

        exp = store.get_experience(result["experience_id"])
        exp.ground_truth_validation = {"is_consistent": True}
        store._experiences[exp.experience_id] = exp

        reliability = store.get_experience_reliability(result["experience_id"])
        assert reliability["consistency_rate"] == 1.0

    def test_reliability_with_ground_truth_invalid(self, store):
        result = store.save_experience(
            task_id="gt_invalid",
            experience={
                "parameters": {},
                "metrics": {},
                "validation_result": {},
            },
        )

        exp = store.get_experience(result["experience_id"])
        exp.ground_truth_validation = {"is_consistent": False}
        store._experiences[exp.experience_id] = exp

        reliability = store.get_experience_reliability(result["experience_id"])
        assert reliability["consistency_rate"] == 0.0

    def test_reliability_with_validation_history(self, store):
        result = store.save_experience(
            task_id="with_history",
            experience={
                "parameters": {},
                "metrics": {},
                "validation_result": {},
            },
        )

        exp_id = result["experience_id"]
        from datetime import datetime

        store._validation_history[exp_id] = [
            {
                "timestamp": datetime.now().isoformat(),
                "validation_result": {"is_consistent": True},
            },
            {
                "timestamp": datetime.now().isoformat(),
                "validation_result": {"is_consistent": True},
            },
            {
                "timestamp": datetime.now().isoformat(),
                "validation_result": {"is_consistent": False},
            },
        ]

        reliability = store.get_experience_reliability(exp_id)
        assert reliability["validation_count"] == 3
        assert abs(reliability["consistency_rate"] - 0.6667) < 0.001

    def test_reliability_calculation_formula(self, store):
        score = store._calculate_reliability_score(
            validation_count=0, consistency_rate=0.0
        )
        assert score == 0.5

        score = store._calculate_reliability_score(
            validation_count=10, consistency_rate=1.0
        )
        assert score == pytest.approx(1.0)

        score = store._calculate_reliability_score(
            validation_count=10, consistency_rate=0.0
        )
        assert score < 1.0

    def test_reliability_nonexistent_experience(self, store):
        reliability = store.get_experience_reliability("nonexistent_id")
        assert reliability["reliability_score"] == 0.0
        assert reliability["validation_count"] == 0


class TestExperienceStoreSearch:
    """Test search functionality"""

    def test_search_by_material_exact(self, store_with_experiences):
        results = store_with_experiences.search_by_material("45钢")
        assert len(results) >= 1
        assert all(
            "45钢" in str(r.get("parameters", {}).get("material", "")) for r in results
        )

    def test_search_by_material_fuzzy(self, store_with_experiences):
        results = store_with_experiences.search_by_material("45", limit=10)
        assert len(results) >= 1

    def test_search_with_ground_truth_context(self, store_with_experiences):
        query = {"process": "车削", "material": "45钢"}

        result = store_with_experiences.search_with_ground_truth(
            query=query,
            top_k=5,
            include_ground_truth=True,
        )

        assert "experiences" in result
        assert "ground_truth_context" in result


class TestExperienceStoreMaterialStatistics:
    """Test material statistics"""

    def test_material_statistics_empty(self, store):
        stats = store.get_material_statistics()
        assert len(stats) == 0

    def test_material_statistics_with_experiences(self, store_with_experiences):
        stats = store_with_experiences.get_material_statistics()

        assert "45钢" in stats
        assert "不锈钢" in stats
        assert stats["45钢"]["experience_count"] == 2

    def test_material_statistics_wear_rates(self, store_with_experiences):
        stats = store_with_experiences.get_material_statistics()

        assert "avg_wear_rate" in stats["45钢"]
        assert "min_wear_rate" in stats["45钢"]
        assert "max_wear_rate" in stats["45钢"]

    def test_material_wear_summary(self, store_with_experiences):
        summary = store_with_experiences.get_material_wear_summary()

        assert "45钢" in summary
        assert summary["45钢"]["experiment_count"] == 2


class TestExperienceStoreQueryByMaterial:
    """Test material query methods"""

    def test_query_by_material_exact_match(self, store_with_experiences):
        results = store_with_experiences.query_by_material("45钢", fuzzy=False)
        assert len(results) >= 1

    def test_query_by_material_fuzzy_match(self, store_with_experiences):
        results = store_with_experiences.query_by_material("45", fuzzy=True)
        assert len(results) >= 1

    def test_query_by_material_no_match(self, store_with_experiences):
        results = store_with_experiences.query_by_material("不存在的材料", fuzzy=True)
        assert len(results) == 0

    def test_query_by_material_respects_limit(self, store_with_experiences):
        results = store_with_experiences.query_by_material("45钢", limit=1, fuzzy=True)
        assert len(results) == 1


class TestExperienceStoreConcurrency:
    """Test concurrent access"""

    def test_concurrent_save_operations(self, store):
        errors = []
        saved_ids = []

        def save_experiences(thread_id):
            try:
                for i in range(10):
                    result = store.save_experience(
                        task_id=f"concurrent_{thread_id}_{i}",
                        experience={
                            "parameters": {"thread": thread_id},
                            "metrics": {},
                        },
                    )
                    saved_ids.append(result["experience_id"])
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=save_experiences, args=(i,)) for i in range(5)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(saved_ids) == 50

    def test_concurrent_read_operations(self, store_with_experiences):
        results = []
        errors = []

        def read_experiences():
            try:
                for _ in range(20):
                    exp = store_with_experiences.list_experiences()
                    results.append(len(exp))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=read_experiences) for _ in range(5)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert all(r == 3 for r in results)


class TestExperienceStoreEdgeCases:
    """Test edge cases"""

    def test_save_with_empty_parameters(self, store):
        result = store.save_experience(
            task_id="empty_params",
            experience={
                "parameters": {},
                "metrics": {},
            },
        )
        assert result["status"] == "saved"

    def test_save_with_complex_metrics(self, store):
        experience = {
            "parameters": {"material": "test"},
            "metrics": {
                "accuracy": 0.95,
                "precision": 0.92,
                "recall": 0.88,
                "f1_score": 0.90,
            },
            "validation_result": {"is_valid": True},
        }

        result = store.save_experience(task_id="complex", experience=experience)
        saved = store.get_experience(result["experience_id"])

        assert saved.metrics["f1_score"] == 0.90

    def test_list_with_nonexistent_filter(self, store_with_experiences):
        results = store_with_experiences.list_experiences(
            filters={"nonexistent_key": "value"}
        )
        assert len(results) == 0

    def test_reliability_with_zero_divisor_handled(self, store):
        score = store._calculate_reliability_score(
            validation_count=0, consistency_rate=0.0
        )
        assert score == 0.5

    def test_relevance_score_empty_query(self, store_with_experiences):
        exp_dict = store_with_experiences.list_experiences()[0]
        score = store_with_experiences._calculate_relevance_score(exp_dict, {})
        assert score == 0.0

    def test_material_wear_summary_handles_missing_metrics(self, store):
        store.save_experience(
            task_id="missing_metrics",
            experience={
                "parameters": {"material": "unknown"},
                "metrics": {},
            },
        )

        summary = store.get_material_wear_summary()
        assert "unknown" in summary


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
