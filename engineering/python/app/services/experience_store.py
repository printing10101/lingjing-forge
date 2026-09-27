import json
import logging
import numpy as np
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Experience:
    experience_id: str
    task_id: str
    process: str
    parameters: dict[str, Any]
    metrics: dict[str, Any]
    validation_result: dict[str, Any]
    ground_truth_validation: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ExperienceStore:
    def __init__(self, storage_dir: str = "experiences"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._experiences: dict[str, Experience] = {}
        self._validation_history: dict[str, list[dict]] = {}
        self._load_experiences()

    def save_experience(
        self,
        task_id: str,
        experience: dict,
        process: str | None = None,
    ) -> dict:
        exp_id = f"exp-{datetime.now().strftime('%Y%m%d%H%M%S')}-{task_id[:8]}"

        exp = Experience(
            experience_id=exp_id,
            task_id=task_id,
            process=process or "unknown",
            parameters=experience.get("parameters", {}),
            metrics=experience.get("metrics", {}),
            validation_result=experience.get("validation_result", {}),
            metadata=experience.get("metadata", {}),
        )

        self._experiences[exp_id] = exp
        self._save_to_disk()

        return {
            "experience_id": exp_id,
            "status": "saved",
        }

    def save_experience_with_validation(
        self,
        task_id: str,
        experience: dict,
        ground_truth_adapter=None,
        process: str | None = None,
    ) -> dict:
        exp_id = f"exp-{datetime.now().strftime('%Y%m%d%H%M%S')}-{task_id[:8]}"

        validation_result = {}
        if ground_truth_adapter and process:
            validation_result = ground_truth_adapter.validate_experience(
                experience=experience,
                process=process,
            )

        exp = Experience(
            experience_id=exp_id,
            task_id=task_id,
            process=process or "unknown",
            parameters=experience.get("parameters", {}),
            metrics=experience.get("metrics", {}),
            validation_result=experience.get("validation_result", {}),
            ground_truth_validation=validation_result,
            metadata=experience.get("metadata", {}),
        )

        self._experiences[exp_id] = exp

        if exp_id not in self._validation_history:
            self._validation_history[exp_id] = []
        self._validation_history[exp_id].append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "validation_result": validation_result,
            }
        )

        self._save_to_disk()

        return {
            "experience_id": exp_id,
            "validation_result": validation_result,
        }

    def get_experience(self, experience_id: str) -> Experience | None:
        return self._experiences.get(experience_id)

    def list_experiences(
        self,
        filters: dict | None = None,
        limit: int = 100,
    ) -> list[dict]:
        results = []
        for exp in self._experiences.values():
            if filters:
                match = all(getattr(exp, k, None) == v for k, v in filters.items())
                if not match:
                    continue
            results.append(asdict(exp))
            if len(results) >= limit:
                break
        return results

    def search_with_ground_truth(
        self,
        query: dict,
        top_k: int = 5,
        include_ground_truth: bool = True,
    ) -> dict:
        experiences = self.list_experiences(filters=query, limit=top_k * 2)

        experience_scores = []
        for exp_dict in experiences:
            score = self._calculate_relevance_score(exp_dict, query)
            experience_scores.append((exp_dict, score))

        experience_scores.sort(key=lambda x: x[1], reverse=True)
        top_experiences = [exp for exp, _ in experience_scores[:top_k]]

        ground_truth_context = {}
        if include_ground_truth and query.get("process"):
            try:
                from app.services.ground_truth_adapter import BoschGroundTruthAdapter

                gt_adapter = BoschGroundTruthAdapter()
                gt_adapter.load_ground_truth()

                success_rate = gt_adapter.get_process_success_rate(
                    process=query["process"],
                    machine=query.get("machine"),
                )

                similar_cases = []
                if query.get("vibration_features"):
                    similar_cases = gt_adapter.find_similar_cases(
                        query_features=query["vibration_features"],
                        top_k=3,
                    )

                ground_truth_context = {
                    "process_success_rate": success_rate,
                    "similar_ground_truth_cases": similar_cases,
                }
            except ImportError:
                logger.warning("ground_truth_adapter 模块不可用，跳过地面真实验证")
            except (OSError, ValueError, KeyError, TypeError) as e:
                logger.warning("地面真实验证失败: %s", e)

        return {
            "experiences": top_experiences,
            "ground_truth_context": ground_truth_context,
        }

    def get_experience_reliability(self, experience_id: str) -> dict:
        exp = self._experiences.get(experience_id)
        if not exp:
            return {
                "reliability_score": 0.0,
                "validation_count": 0,
                "consistency_rate": 0.0,
                "last_validated": None,
            }

        history = self._validation_history.get(experience_id, [])
        validation_count = len(history)

        if validation_count == 0:
            gt_validation = exp.ground_truth_validation
            if gt_validation:
                consistency_rate = 1.0 if gt_validation.get("is_consistent") else 0.0
                last_validated = exp.created_at
            else:
                consistency_rate = 0.0
                last_validated = None
        else:
            consistent_count = sum(1 for h in history if h.get("validation_result", {}).get("is_consistent", False))
            consistency_rate = consistent_count / validation_count
            last_validated = history[-1]["timestamp"]

        reliability_score = self._calculate_reliability_score(validation_count, consistency_rate)

        return {
            "reliability_score": round(reliability_score, 4),
            "validation_count": validation_count,
            "consistency_rate": round(consistency_rate, 4),
            "last_validated": last_validated,
        }

    def _calculate_relevance_score(self, exp_dict: dict, query: dict) -> float:
        score = 0.0

        if query.get("process") and exp_dict.get("process") == query["process"]:
            score += 1.0

        query_params = query.get("parameters", {})
        exp_params = exp_dict.get("parameters", {})
        if query_params and exp_params:
            common_keys = set(query_params.keys()) & set(exp_params.keys())
            if common_keys:
                param_similarity = sum(
                    1.0
                    - min(
                        abs(query_params[k] - exp_params[k]) / max(abs(query_params[k]), 1e-10),
                        1.0,
                    )
                    for k in common_keys
                ) / len(common_keys)
                score += param_similarity * 0.5

        query_metrics = query.get("metrics", {})
        exp_metrics = exp_dict.get("metrics", {})
        if query_metrics and exp_metrics:
            common_keys = set(query_metrics.keys()) & set(exp_metrics.keys())
            if common_keys:
                metric_similarity = sum(
                    1.0
                    - min(
                        abs(query_metrics[k] - exp_metrics[k]) / max(abs(query_metrics[k]), 1e-10),
                        1.0,
                    )
                    for k in common_keys
                ) / len(common_keys)
                score += metric_similarity * 0.3

        return score

    @staticmethod
    def _calculate_reliability_score(validation_count: int, consistency_rate: float) -> float:
        if validation_count == 0:
            return 0.5

        confidence_weight = min(validation_count / 10.0, 1.0)
        reliability = consistency_rate * 0.7 + confidence_weight * 0.3
        return max(0.0, min(1.0, reliability))

    def _save_to_disk(self):
        file_path = self.storage_dir / "experiences.json"
        data = {
            "experiences": {eid: asdict(exp) for eid, exp in self._experiences.items()},
            "validation_history": self._validation_history,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def import_uniwear_experiments(
        self,
        data_dir: str = "python/data/uniwear",
    ) -> dict:
        result = self.import_from_uniwear(uniwear_data_dir=data_dir)
        return {
            "imported_count": result["imported_count"],
            "by_dataset": {
                "nuaa": result["nuaa_experiences"],
                "phm2010": result["phm2010_experiences"],
            },
            "experience_ids": result["experience_ids"],
        }

    def search_by_material(self, material: str, limit: int = 20) -> list[dict]:
        return self.query_by_material(material, limit=limit, fuzzy=True)

    def get_material_statistics(self) -> dict:
        summary = self.get_material_wear_summary()
        result = {}
        for mat, mat_data in summary.items():
            result[mat] = {
                "experience_count": mat_data["experiment_count"],
                "total_samples": sum(
                    e.get("sample_count", 0)
                    for e in mat_data.get("experiments", [])
                    if isinstance(e, dict) and "sample_count" in e
                ),
                "avg_wear_rate": mat_data["avg_wear_rate"],
                "min_wear_rate": mat_data["min_wear_rate"],
                "max_wear_rate": mat_data["max_wear_rate"],
                "datasets": sorted(
                    {e.get("dataset", "unknown") for e in mat_data.get("experiments", []) if isinstance(e, dict)}
                ),
            }
        return result

    def _load_experiences(self):
        file_path = self.storage_dir / "experiences.json"
        if not file_path.exists():
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            for eid, exp_data in data.get("experiences", {}).items():
                exp = Experience(**exp_data)
                self._experiences[eid] = exp

            self._validation_history = data.get("validation_history", {})
            logger.info("Loaded %d experiences from disk", len(self._experiences))
        except (json.JSONDecodeError, OSError, ValueError, KeyError, TypeError) as e:
            logger.warning("Failed to load experiences: %s", e)
            backup_path = file_path.with_suffix(".json.bak")
            try:
                file_path.rename(backup_path)
                logger.warning("Corrupted experiences file backed up to %s", backup_path)
            except (OSError, ValueError) as be:
                logger.error("Failed to backup corrupted experiences file: %s", be)

    def import_from_uniwear(
        self,
        uniwear_data_dir: str = "python/data/uniwear",
    ) -> dict:
        from app.data.uniwear_loader import (
            UniwearDataLoader,
            UniwearDataset,
            NUAA_MATERIAL,
            NUAA_MATERIAL_FULL,
            PHM2010_MATERIAL,
            PHM2010_MATERIAL_FULL,
        )

        loader = UniwearDataLoader(data_dir=uniwear_data_dir)
        imported: dict = {"nuaa": [], "phm2010": [], "total": 0}

        ds_configs = [
            (UniwearDataset.NUAA, "nuaa", NUAA_MATERIAL, NUAA_MATERIAL_FULL),
            (
                UniwearDataset.PHM2010,
                "phm2010",
                PHM2010_MATERIAL,
                PHM2010_MATERIAL_FULL,
            ),
        ]

        for ds, ds_key, material, material_full in ds_configs:
            experiments = loader.get_experiment_tags(ds)
            for exp in experiments:
                try:
                    stats = loader.compute_statistics(ds, experiment_tag=exp)
                    wear_df = loader.get_wear_data(ds, experiment_tag=exp)

                    wear_stats = stats.get("wear_stats", {})
                    signal_stats = stats.get("signal_stats", {})

                    process_name = f"{ds_key.upper()}正交切削" if ds_key == "nuaa" else f"{ds_key.upper()}全寿命切削"

                    experience_data = {
                        "parameters": {
                            "material": material,
                            "material_full": material_full,
                            "experiment": exp,
                            "dataset": ds_key,
                            "process": process_name,
                        },
                        "metrics": {
                            "initial_wear": wear_stats.get("initial_wear", 0),
                            "final_wear": wear_stats.get("final_wear", 0),
                            "max_wear": wear_stats.get("max_wear", 0),
                            "mean_wear_rate": wear_stats.get("mean_wear_rate", 0),
                            "total_wear_increment": wear_stats.get("total_wear_increment", 0),
                            "sample_count": wear_stats.get("sample_count", 0),
                        },
                        "signal_summary": {
                            col: {
                                "rms": s.get("rms", 0),
                                "mean": s.get("mean", 0),
                                "std": s.get("std", 0),
                            }
                            for col, s in signal_stats.items()
                        },
                        "validation_result": {
                            "is_valid": True,
                            "source": f"uniwear-{ds_key}",
                            "experiment": exp,
                        },
                        "metadata": {
                            "source": f"uniwear-{ds_key}",
                            "material": material,
                            "experiment": exp,
                            "wear_curve_length": len(wear_df),
                        },
                    }

                    result = self.save_experience(
                        task_id=f"uniwear-{ds_key}-{exp}",
                        experience=experience_data,
                        process=process_name,
                    )
                    imported[ds_key].append(result.get("experience_id", ""))
                    imported["total"] += 1

                    logger.info(
                        "Imported uniwear experience: %s/%s (material=%s)",
                        ds_key,
                        exp,
                        material,
                    )
                except (OSError, ValueError, KeyError, TypeError) as e:
                    logger.warning(
                        "Failed to import uniwear experience %s/%s: %s",
                        ds_key,
                        exp,
                        e,
                    )

        return {
            "imported_count": imported["total"],
            "nuaa_experiences": len(imported["nuaa"]),
            "phm2010_experiences": len(imported["phm2010"]),
            "experience_ids": imported,
        }

    def query_by_material(self, material: str, limit: int = 20, fuzzy: bool = False) -> list[dict]:
        results = []
        for exp in self._experiences.values():
            exp_params = exp.parameters if isinstance(exp.parameters, dict) else {}
            exp_meta = exp.metadata if isinstance(exp.metadata, dict) else {}
            if fuzzy:
                exp_material = exp_params.get("material", "").upper()
                exp_material_full = exp_params.get("material_full", "").lower()
                exp_meta_material = exp_meta.get("material", "").upper()
                query_material = material.upper()
                material_match = (
                    query_material in exp_material
                    or query_material.lower() in exp_material_full
                    or query_material in exp_meta_material
                )
            else:
                material_match = (
                    exp_params.get("material", "").upper() == material.upper()
                    or exp_meta.get("material", "").upper() == material.upper()
                )
            if material_match:
                results.append(asdict(exp))
                if len(results) >= limit:
                    break
        return results

    def get_material_wear_summary(self) -> dict:
        materials: dict = {}
        for exp in self._experiences.values():
            params = exp.parameters if isinstance(exp.parameters, dict) else {}
            material = params.get("material", "unknown")

            if material not in materials:
                materials[material] = []

            metrics = exp.metrics if isinstance(exp.metrics, dict) else {}
            materials[material].append(
                {
                    "experience_id": exp.experience_id,
                    "experiment": params.get("experiment", "unknown"),
                    "mean_wear_rate": metrics.get("mean_wear_rate", 0),
                    "final_wear": metrics.get("final_wear", 0),
                }
            )

        summary = {}
        for mat, exps in materials.items():
            wear_rates = [e["mean_wear_rate"] for e in exps if e["mean_wear_rate"] and e["mean_wear_rate"] > 0]
            summary[mat] = {
                "experiment_count": len(exps),
                "avg_wear_rate": round(float(np.mean(wear_rates)), 8) if wear_rates else 0,
                "max_wear_rate": round(float(np.max(wear_rates)), 8) if wear_rates else 0,
                "min_wear_rate": round(float(np.min(wear_rates)), 8) if wear_rates else 0,
                "experiments": exps,
            }

        return summary
