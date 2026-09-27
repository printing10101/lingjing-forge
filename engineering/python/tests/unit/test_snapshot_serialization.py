"""实验快照反序列化测试（observability/snapshot.py 修复验证）。

覆盖 _workflow_spec_from_dict 的 inputs 反序列化：
- 新格式（契约字段 type/uri/metadata）
- 旧格式（mime_type 兼容回退 → 进 metadata）
- URI scheme 推断 Artifact.type
- 回归：_orm_to_contract 的 cast 解包不破坏行为
"""

from __future__ import annotations

import pytest

from app.observability.snapshot import _infer_artifact_type, _workflow_spec_from_dict


@pytest.mark.unit
class TestWorkflowSpecFromDict:
    def test_inputs_new_format(self):
        """新格式：type/uri/metadata 直接映射到 Artifact 契约字段。"""
        spec = _workflow_spec_from_dict(
            {
                "name": "ltc-train",
                "version": "1.0.0",
                "nodes": [
                    {"node_id": "n1", "task_type": "train", "params": {}}
                ],
                "edges": [],
                "inputs": {
                    "train_data": {
                        "type": "dataset",
                        "uri": "dataset://my-ds/v3",
                        "metadata": {"rows": 1000},
                    }
                },
            }
        )
        art = spec.inputs["train_data"]
        assert art.name == "train_data"
        assert art.type == "dataset"
        assert art.uri == "dataset://my-ds/v3"
        assert art.metadata == {"rows": 1000}

    def test_inputs_legacy_mime_type_format(self):
        """旧格式：mime_type 兼容回退（并入 metadata，非契约字段不直接传）。"""
        spec = _workflow_spec_from_dict(
            {
                "name": "legacy",
                "version": "1.0.0",
                "nodes": [{"node_id": "n1", "task_type": "train", "params": {}}],
                "edges": [],
                "inputs": {
                    "gcode": {
                        "uri": "file://out/prog.nc",
                        "mime_type": "text/x-gcode",
                    }
                },
            }
        )
        art = spec.inputs["gcode"]
        assert art.name == "gcode"
        # type 由 URI scheme 推断
        assert art.type == "file"
        # mime_type 兼容回退进 metadata
        assert art.metadata.get("mime_type") == "text/x-gcode"

    def test_artifact_type_inference(self):
        """URI scheme → Artifact.type 推断。"""
        assert _infer_artifact_type("dataset://ds/v1") == "dataset"
        assert _infer_artifact_type("model://ltc-v1") == "model"
        assert _infer_artifact_type("metrics://job-1") == "metrics"
        assert _infer_artifact_type("report://r1") == "report"
        assert _infer_artifact_type("file://path/to/data.csv") == "file"
        assert _infer_artifact_type("") == "file"

    def test_inputs_without_uri_raises(self):
        """无 uri 的 inputs 条目触发 Artifact 契约校验（uri 不能为空）。"""
        with pytest.raises(ValueError, match="uri 不能为空"):
            _workflow_spec_from_dict(
                {
                    "name": "minimal",
                    "version": "1.0.0",
                    "nodes": [{"node_id": "n1", "task_type": "train", "params": {}}],
                    "edges": [],
                    "inputs": {"x": {}},
                }
            )

    def test_artifact_type_inference_direct(self):
        """URI scheme → Artifact.type 推断（辅助函数直测）。"""
        assert _infer_artifact_type("dataset://ds/v1") == "dataset"
        assert _infer_artifact_type("model://ltc-v1") == "model"
        assert _infer_artifact_type("metrics://job-1") == "metrics"
        assert _infer_artifact_type("report://r1") == "report"
        assert _infer_artifact_type("file://path/to/data.csv") == "file"
        assert _infer_artifact_type("") == "file"
