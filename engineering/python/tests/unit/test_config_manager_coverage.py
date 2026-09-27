"""ai/lnn/config/config_manager 覆盖率补强测试。

覆盖：默认配置、YAML 加载/保存往返、get/set/dirty 标记、
validate 全部分支（LNN/工作流/环境/最佳实践）、
model 增删查、dataset cache 配置、环境自适应、深度合并。
"""

from __future__ import annotations

import json
import os

import pytest
import yaml

pytestmark = pytest.mark.unit

from app.ai.lnn.config.config_manager import (
    DatasetCacheConfig,
    LNNConfig,
    ModelConfig,
    ThresholdConfig,
    YAMLConfigManager,
)


def _valid_lnn_dict() -> dict:
    return {
        "lnn": {
            "enabled": True,
            "models_dir": "/models",
            "default_device": "cpu",
            "models": {
                "cfc_model": {"type": "cfc", "path": "/models/cfc.pt"},
                "ltc_model": {"type": "ltc", "path": "/models/ltc.pt"},
            },
            "thresholds": {"quick": 0.5, "hybrid": 0.7, "complexity": 0.8},
        },
        "workflow": {"max_steps": 10, "timeout_seconds": 300},
        "environment": {"name": "development"},
    }


class TestDefaultsAndAccess:
    def test_defaults_loaded_without_path(self):
        mgr = YAMLConfigManager(use_defaults=True)
        assert mgr.get("lnn", "enabled") is True
        assert mgr.is_dirty() is False

    def test_get_returns_default_for_missing(self):
        mgr = YAMLConfigManager(use_defaults=True)
        assert mgr.get("nope", "missing", default="fallback") == "fallback"
        assert mgr.get("lnn", "no_key", default=42) == 42

    def test_get_section_dict(self):
        mgr = YAMLConfigManager(use_defaults=True)
        section = mgr.get("workflow")
        assert isinstance(section, dict)

    def test_get_nonexistent_section_default(self):
        mgr = YAMLConfigManager(use_defaults=True)
        assert mgr.get("ghost") is None


class TestLoadAndSave:
    def test_load_roundtrip(self, tmp_path):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text(yaml.safe_dump(_valid_lnn_dict(), allow_unicode=True), encoding="utf-8")
        mgr = YAMLConfigManager()
        mgr.load(str(cfg))
        assert mgr.get("lnn", "enabled") is True
        assert mgr.get("workflow", "max_steps") == 10
        assert mgr.config_path == str(cfg)
        assert mgr.is_dirty() is False

    def test_load_missing_file_raises(self, tmp_path):
        mgr = YAMLConfigManager()
        with pytest.raises(FileNotFoundError):
            mgr.load(str(tmp_path / "nope.yaml"))

    def test_load_no_path_raises(self):
        mgr = YAMLConfigManager(config_path=None, use_defaults=False)
        with pytest.raises(ValueError):
            mgr.load()

    def test_load_bad_yaml_raises(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("{unclosed: [1,2", encoding="utf-8")
        mgr = YAMLConfigManager()
        with pytest.raises(yaml.YAMLError):
            mgr.load(str(bad))

    def test_save_roundtrip_new_instance(self, tmp_path):
        out = tmp_path / "out.yaml"
        mgr = YAMLConfigManager(use_defaults=True)
        mgr.set("workflow", "max_steps", 99)
        mgr.save(str(out))
        assert out.exists()

        mgr2 = YAMLConfigManager()
        mgr2.load(str(out))
        assert mgr2.get("workflow", "max_steps") == 99

    def test_save_uses_configured_path(self, tmp_path):
        out = tmp_path / "auto.yaml"
        out.write_text(yaml.safe_dump(_valid_lnn_dict(), allow_unicode=True), encoding="utf-8")
        mgr = YAMLConfigManager()
        mgr.load(str(out))  # load 记录 config_path
        mgr.set("lnn", "enabled", False)
        mgr.save()  # 无参保存走已记录路径
        reloaded = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert reloaded["lnn"]["enabled"] is False

    def test_to_dict_matches_raw(self, tmp_path):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text(yaml.safe_dump(_valid_lnn_dict(), allow_unicode=True), encoding="utf-8")
        mgr = YAMLConfigManager()
        mgr.load(str(cfg))
        d = mgr.to_dict()
        assert d["lnn"]["default_device"] == "cpu"
        assert "cfc_model" in d["lnn"]["models"]

    def test_to_dataclass_builds(self, tmp_path):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text(yaml.safe_dump(_valid_lnn_dict(), allow_unicode=True), encoding="utf-8")
        mgr = YAMLConfigManager()
        mgr.load(str(cfg))
        dc = mgr.to_dataclass()
        assert dc.lnn.enabled is True
        assert isinstance(dc.lnn, LNNConfig)
        assert isinstance(dc.lnn.thresholds, ThresholdConfig)
        assert isinstance(dc.lnn.models["cfc_model"], ModelConfig)
        assert dc.workflow.max_steps == 10


class TestSetAndDirty:
    def test_set_marks_dirty_and_reads_back(self):
        mgr = YAMLConfigManager(use_defaults=True)
        assert mgr.is_dirty() is False
        mgr.set("workflow", "timeout_seconds", 600)
        assert mgr.is_dirty() is True
        assert mgr.get("workflow", "timeout_seconds") == 600

    def test_set_creates_section(self):
        mgr = YAMLConfigManager(use_defaults=True)
        mgr.set("custom", "key", "value")
        assert mgr.get("custom", "key") == "value"

    def test_reset_to_defaults(self):
        mgr = YAMLConfigManager(use_defaults=True)
        mgr.set("workflow", "max_steps", 999)
        mgr.reset_to_defaults()
        # reset 后仍标记 dirty（需重新保存）
        assert mgr.is_dirty() is True
        assert mgr.get("workflow", "max_steps") != 999

    def test_set_none_key_raises(self):
        mgr = YAMLConfigManager(use_defaults=True)
        with pytest.raises((TypeError, AttributeError, ValueError)):
            mgr.set("lnn", None, "x")  # type: ignore[arg-type]


class TestModelManagement:
    def test_get_model_config_existing(self, tmp_path):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text(yaml.safe_dump(_valid_lnn_dict(), allow_unicode=True), encoding="utf-8")
        mgr = YAMLConfigManager()
        mgr.load(str(cfg))
        mc = mgr.get_model_config("cfc_model")
        assert mc is not None
        assert mc.type == "cfc"

    def test_get_model_config_missing_returns_none(self, tmp_path):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text(yaml.safe_dump(_valid_lnn_dict(), allow_unicode=True), encoding="utf-8")
        mgr = YAMLConfigManager()
        mgr.load(str(cfg))
        assert mgr.get_model_config("ghost") is None

    def test_add_and_remove_model(self, tmp_path):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text(yaml.safe_dump(_valid_lnn_dict(), allow_unicode=True), encoding="utf-8")
        mgr = YAMLConfigManager()
        mgr.load(str(cfg))
        mgr.add_model("hybrid_x", {"type": "hybrid_lnn", "path": "/m/h.pt"})
        assert mgr.get_model_config("hybrid_x").type == "hybrid_lnn"
        mgr.remove_model("hybrid_x")
        assert mgr.get_model_config("hybrid_x") is None

    def test_add_model_without_lnn_section(self):
        mgr = YAMLConfigManager(use_defaults=False)
        mgr.add_model("m1", {"type": "ltc", "path": "/m.pt"})
        assert mgr.get_model_config("m1").type == "ltc"


class TestDatasetCache:
    def test_set_and_get_dataset_cache(self):
        mgr = YAMLConfigManager(use_defaults=True)
        cfg = DatasetCacheConfig(
            enabled=True,
            max_cache_size=8 * 1024 * 1024 * 1024,
            cache_directory="/cache",
        )
        mgr.set_dataset_cache_config(cfg)
        got = mgr.get_dataset_cache_config()
        assert got.enabled is True
        assert got.max_cache_size == 8 * 1024 * 1024 * 1024
        assert got.cache_directory == "/cache"

    def test_dataset_cache_roundtrip_file(self, tmp_path):
        out = tmp_path / "cfg.yaml"
        mgr = YAMLConfigManager(use_defaults=True)
        mgr.set_dataset_cache_config(
            DatasetCacheConfig(enabled=True, max_cache_size=4 * 1024 * 1024 * 1024, cache_directory="/d")
        )
        mgr.save(str(out))
        mgr2 = YAMLConfigManager()
        mgr2.load(str(out))
        assert mgr2.get_dataset_cache_config().max_cache_size == 4 * 1024 * 1024 * 1024


class TestValidation:
    def test_valid_config_passes(self):
        mgr = YAMLConfigManager(use_defaults=False)
        result = mgr.validate(_valid_lnn_dict())
        assert result["valid"] is True
        assert result["errors"] == []

    def test_missing_required_lnn_keys(self):
        mgr = YAMLConfigManager(use_defaults=False)
        cfg = {"lnn": {"enabled": True}}
        result = mgr.validate(cfg)
        assert result["valid"] is False
        assert any("Missing required LNN key" in e for e in result["errors"])

    def test_invalid_default_device(self):
        mgr = YAMLConfigManager(use_defaults=False)
        cfg = _valid_lnn_dict()
        cfg["lnn"]["default_device"] = "tpu"
        result = mgr.validate(cfg)
        assert any("Invalid default_device" in e for e in result["errors"])

    def test_models_not_dict(self):
        mgr = YAMLConfigManager(use_defaults=False)
        cfg = _valid_lnn_dict()
        cfg["lnn"]["models"] = ["not", "dict"]
        result = mgr.validate(cfg)
        assert any("must be a dictionary" in e for e in result["errors"])

    def test_model_missing_required_keys(self):
        mgr = YAMLConfigManager(use_defaults=False)
        cfg = _valid_lnn_dict()
        cfg["lnn"]["models"]["broken"] = {"type": "cfc"}
        result = mgr.validate(cfg)
        assert any("Missing required key 'path'" in e for e in result["errors"])

    def test_invalid_model_type(self):
        mgr = YAMLConfigManager(use_defaults=False)
        cfg = _valid_lnn_dict()
        cfg["lnn"]["models"]["bad"] = {"type": "rnn", "path": "/x.pt"}
        result = mgr.validate(cfg)
        assert any("Invalid model type" in e for e in result["errors"])

    def test_thresholds_not_dict(self):
        mgr = YAMLConfigManager(use_defaults=False)
        cfg = _valid_lnn_dict()
        cfg["lnn"]["thresholds"] = [1, 2]
        result = mgr.validate(cfg)
        assert any("thresholds must be a dictionary" in e for e in result["errors"])

    def test_missing_threshold_keys(self):
        mgr = YAMLConfigManager(use_defaults=False)
        cfg = _valid_lnn_dict()
        del cfg["lnn"]["thresholds"]["complexity"]
        result = mgr.validate(cfg)
        assert any("Missing required threshold key" in e for e in result["errors"])

    def test_threshold_out_of_range(self):
        mgr = YAMLConfigManager(use_defaults=False)
        cfg = _valid_lnn_dict()
        cfg["lnn"]["thresholds"]["quick"] = 1.7
        result = mgr.validate(cfg)
        assert any("must be a float between 0 and 1" in e for e in result["errors"])

    def test_workflow_bad_values(self):
        mgr = YAMLConfigManager(use_defaults=False)
        cfg = _valid_lnn_dict()
        cfg["workflow"]["max_steps"] = -3
        cfg["workflow"]["timeout_seconds"] = "fast"
        result = mgr.validate(cfg)
        assert any("positive integer" in e for e in result["errors"])

    def test_invalid_environment_name(self):
        mgr = YAMLConfigManager(use_defaults=False)
        cfg = _valid_lnn_dict()
        cfg["environment"]["name"] = "mars"
        result = mgr.validate(cfg)
        assert any("Invalid environment name" in e for e in result["errors"])

    def test_best_practices_warns_no_models(self):
        mgr = YAMLConfigManager(use_defaults=False)
        cfg = _valid_lnn_dict()
        cfg["lnn"]["models"] = {}
        result = mgr.validate(cfg)
        assert result["valid"] is True
        assert any("no models are configured" in w for w in result["warnings"])

    def test_load_invalid_config_raises_value_error(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(yaml.safe_dump({"lnn": {"enabled": True}}), encoding="utf-8")
        # use_defaults=False：不合并默认配置，缺 required key 必须失败
        mgr = YAMLConfigManager(use_defaults=False)
        with pytest.raises(ValueError):
            mgr.load(str(bad))


class TestEnvironmentAdaptations:
    def test_production_adaptation(self, tmp_path):
        cfg = tmp_path / "cfg.yaml"
        raw = _valid_lnn_dict()
        raw["environment"]["name"] = "production"
        raw["lnn"].pop("default_device")
        cfg.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        mgr = YAMLConfigManager()
        mgr.load(str(cfg))
        assert mgr.get("environment", "debug") is False
        assert mgr.get("lnn", "default_device") in ("cpu", "cuda")

    def test_development_adaptation(self, tmp_path):
        cfg = tmp_path / "cfg.yaml"
        raw = _valid_lnn_dict()
        raw["environment"]["name"] = "development"
        cfg.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        mgr = YAMLConfigManager()
        mgr.load(str(cfg))
        assert mgr.get("environment", "debug") is True

    def test_testing_adaptation_sets_timeout(self, tmp_path):
        cfg = tmp_path / "cfg.yaml"
        raw = _valid_lnn_dict()
        raw["environment"]["name"] = "testing"
        raw.pop("workflow")
        cfg.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        mgr = YAMLConfigManager()
        mgr.load(str(cfg))
        assert mgr.get("workflow", "timeout_seconds") == 60

    def test_device_override(self, tmp_path):
        cfg = tmp_path / "cfg.yaml"
        raw = _valid_lnn_dict()
        raw["environment"]["device_override"] = "cuda"
        cfg.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        mgr = YAMLConfigManager()
        mgr.load(str(cfg))
        assert mgr.get("lnn", "default_device") == "cuda"

    def test_models_path_override(self, tmp_path):
        cfg = tmp_path / "cfg.yaml"
        raw = _valid_lnn_dict()
        raw["environment"]["models_path_override"] = "/alt-models"
        cfg.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        mgr = YAMLConfigManager()
        mgr.load(str(cfg))
        assert mgr.get("lnn", "models_dir") == "/alt-models"


class TestDeepMerge:
    def test_partial_config_merges_over_defaults(self, tmp_path):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text(
            yaml.safe_dump({"workflow": {"max_steps": 5}}, allow_unicode=True),
            encoding="utf-8",
        )
        mgr = YAMLConfigManager(use_defaults=True)
        mgr.load(str(cfg))
        assert mgr.get("workflow", "max_steps") == 5
        # 默认节保留
        assert mgr.get("lnn", "enabled") is not None

    def test_deep_merge_nested_dict(self):
        mgr = YAMLConfigManager(use_defaults=True)
        base = {"a": {"b": 1, "c": 2}}
        override = {"a": {"c": 3}, "d": 4}
        merged = mgr._deep_merge(base, override)
        assert merged == {"a": {"b": 1, "c": 3}, "d": 4}

    def test_deep_merge_scalar_override(self):
        mgr = YAMLConfigManager(use_defaults=True)
        assert mgr._deep_merge({"k": 1}, {"k": "two"}) == {"k": "two"}

    def test_merge_config_invalid_type(self):
        mgr = YAMLConfigManager(use_defaults=True)
        with pytest.raises((TypeError, AttributeError)):
            mgr._merge_config("not-a-dict")  # type: ignore[arg-type]
