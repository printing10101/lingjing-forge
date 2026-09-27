"""模型迭代管线 Workflow 模板（plugins/data_flywheel/templates）单元测试.

对应 core-contracts-design.md 阶段 4 p4-3。

覆盖：
    - YAML 模板文件存在性与可加载性
    - 模板结构完整性（name/version/nodes/edges/inputs/outputs/metadata）
    - DAG 一致性校验（通过 template_to_spec 执行完整校验）
    - 节点拓扑正确性（6 节点线性 DAG：collect_feedback → ... → canary_deploy）
    - task_type 声明（6 种 task_type，handler 在阶段 6 注册）
    - 参数与 plugin.yaml config_schema 对齐（canary_ratio/observation_hours 等）
    - Plugin._handle_workflow_template_request 行为（ready/not_found/error 状态）
    - spec_dict 可往返转换为 WorkflowSpec（dict → WorkflowSpec → validate 通过）
    - 缓存：_load_model_iteration_spec 首次加载后缓存，后续调用不重新读文件

本测试不依赖网络、数据库、torch。依赖 PyYAML（requirements.txt 已声明）。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

from app.contracts.plugin import (
    BUILTIN_EXTENSION_POINTS,
    PluginContext,
)
from app.plugins.entrypoint_loader import (
    EntryPointFormat,
    load_plugin_class,
)
from app.plugins.extension_registry import (
    ExtensionRegistry,
    get_extension_registry,
    reset_extension_registry,
)
from app.workflow.templates.loader import (
    TemplateNotFoundError,
    load_template_from_file,
    template_to_spec,
)
from app.workflow.validator import WorkflowValidationError

# 数据飞轮插件目录（python/plugins/data_flywheel）
_PLUGIN_DIR = Path(__file__).resolve().parent.parent.parent / "plugins" / "data_flywheel"
_TEMPLATE_PATH = _PLUGIN_DIR / "templates" / "model_iteration_pipeline.yaml"


# Fixtures


@pytest.fixture
def fresh_registry() -> ExtensionRegistry:
    """每个测试使用独立的 ExtensionRegistry 实例（避免单例污染）."""
    reset_extension_registry()
    registry = ExtensionRegistry()
    import app.plugins.extension_registry as er_mod

    er_mod._registry_singleton = registry
    yield registry
    reset_extension_registry()


@pytest.fixture
def plugin_instance(fresh_registry: ExtensionRegistry):
    """加载并返回 Plugin 类实例（未调用 on_load）."""
    cls = load_plugin_class(
        "plugins.data_flywheel.main:Plugin",
        fmt=EntryPointFormat.MODULE_CLASS,
    )
    instance = cls()
    return instance


@pytest.fixture
def loaded_plugin(plugin_instance, fresh_registry: ExtensionRegistry):
    """已 on_load 的 Plugin 实例（用于扩展点调用测试）."""
    ctx = PluginContext(
        plugin_id="data_flywheel",
        config={},
        task_registry=object(),
        dataset_store=object(),
        observability=object(),
        logger=logging.getLogger("test.p43"),
        data_dir=str(_PLUGIN_DIR / "_test_data"),
    )
    asyncio.run(plugin_instance.on_load(ctx))
    return plugin_instance


def _run(coro):
    """同步运行异步协程（测试用）.

    Python 3.11+ 移除了 get_event_loop() 在主线程无 loop 时的隐式创建，
    统一用 asyncio.run（每次新建并关闭 loop，对独立测试安全）。
    """
    return asyncio.run(coro)


# YAML 模板文件存在性与可加载性


@pytest.mark.unit
@pytest.mark.contracts
class TestModelIterationTemplateFile:
    """YAML 模板文件存在性与可加载性."""

    def test_template_file_exists(self):
        """模板文件存在于预期路径."""
        assert _TEMPLATE_PATH.exists(), f"模板文件不存在: {_TEMPLATE_PATH}"
        assert _TEMPLATE_PATH.is_file()

    def test_template_loads_without_error(self):
        """模板能被 load_template_from_file 正确加载."""
        template = load_template_from_file(_TEMPLATE_PATH)
        assert template is not None
        assert template.template_id == "model_iteration_pipeline"

    def test_template_core_fields(self):
        """模板核心字段值正确."""
        template = load_template_from_file(_TEMPLATE_PATH)
        assert template.name == "模型迭代管线"
        assert template.version == "0.1.0"
        assert "反馈采集" in template.description
        assert "灰度" in template.description


# 模板结构完整性


@pytest.mark.unit
@pytest.mark.contracts
class TestModelIterationTemplateStructure:
    """模板结构完整性（顶层字段齐全）."""

    def test_template_has_required_top_level_fields(self):
        """模板包含必要的顶层字段."""
        template = load_template_from_file(_TEMPLATE_PATH)
        spec = template.spec_dict
        for field in ["name", "version", "nodes", "edges", "inputs", "outputs", "metadata"]:
            assert field in spec, f"模板缺少顶层字段: {field}"

    def test_template_inputs_artifacts(self):
        """模板 inputs 包含 3 个 Artifact（base_model_uri/feedback_dataset_uri/fallback_dataset_uri）."""
        template = load_template_from_file(_TEMPLATE_PATH)
        inputs = template.spec_dict.get("inputs", {})
        assert len(inputs) == 3
        assert "base_model_uri" in inputs
        assert "feedback_dataset_uri" in inputs
        assert "fallback_dataset_uri" in inputs
        # base_model_uri 类型为 model
        assert inputs["base_model_uri"]["type"] == "model"
        # feedback_dataset_uri 类型为 dataset
        assert inputs["feedback_dataset_uri"]["type"] == "dataset"
        # fallback_dataset_uri 类型为 dataset
        assert inputs["fallback_dataset_uri"]["type"] == "dataset"

    def test_template_outputs_reference_final_nodes(self):
        """模板 outputs 引用 register_model 与 canary_deploy 节点的产物."""
        template = load_template_from_file(_TEMPLATE_PATH)
        outputs = template.spec_dict.get("outputs", {})
        assert len(outputs) == 3
        assert outputs["new_model_uri"] == "${register_model.model_artifact}"
        assert outputs["eval_metrics"] == "${evaluate_model.metrics_artifact}"
        assert outputs["canary_status"] == "${canary_deploy.deployment_artifact}"

    def test_template_metadata_contains_plugin_id(self):
        """模板 metadata 包含 plugin_id 与 acceptance_test 标记."""
        template = load_template_from_file(_TEMPLATE_PATH)
        meta = template.spec_dict.get("metadata", {})
        assert meta.get("plugin_id") == "data_flywheel"
        assert meta.get("acceptance_test") is True
        assert "flywheel" in meta.get("tags", [])
        assert meta.get("max_concurrent") == 1


# DAG 一致性校验（通过 template_to_spec 执行完整校验）


@pytest.mark.unit
@pytest.mark.contracts
class TestModelIterationDagValidation:
    """DAG 一致性校验（无环、节点引用合法、inputs/outputs 引用合法）."""

    def test_template_to_spec_passes_validation(self):
        """template_to_spec 执行完整 DAG 校验通过（不抛异常）."""
        template = load_template_from_file(_TEMPLATE_PATH)
        spec = template_to_spec(template)  # 不抛异常即通过
        assert spec is not None
        assert spec.name == "模型迭代管线"

    def test_dag_has_no_cycle(self):
        """DAG 无环（validate 返回空错误列表）."""
        template = load_template_from_file(_TEMPLATE_PATH)
        spec = template_to_spec(template)
        errors = spec.validate()
        assert errors == [], f"DAG 校验发现错误: {errors}"

    def test_dag_node_ids_unique(self):
        """DAG 节点 id 唯一."""
        template = load_template_from_file(_TEMPLATE_PATH)
        spec = template_to_spec(template)
        node_ids = [n.node_id for n in spec.nodes]
        assert len(node_ids) == len(set(node_ids)), "节点 id 不唯一"

    def test_dag_edge_references_valid(self):
        """DAG 边引用的节点均存在."""
        template = load_template_from_file(_TEMPLATE_PATH)
        spec = template_to_spec(template)
        node_id_set = {n.node_id for n in spec.nodes}
        for edge in spec.edges:
            assert edge.upstream in node_id_set, f"边引用了不存在的上游节点: {edge.upstream}"
            assert edge.downstream in node_id_set, f"边引用了不存在的下游节点: {edge.downstream}"


# 节点拓扑正确性（6 节点线性 DAG）


@pytest.mark.unit
@pytest.mark.contracts
class TestModelIterationNodeTopology:
    """节点拓扑正确性（6 节点线性 DAG）."""

    def test_six_nodes_present(self):
        """DAG 包含 6 个节点."""
        template = load_template_from_file(_TEMPLATE_PATH)
        spec = template_to_spec(template)
        assert len(spec.nodes) == 6

    def test_five_edges_present(self):
        """DAG 包含 5 条边（6 节点线性 DAG）."""
        template = load_template_from_file(_TEMPLATE_PATH)
        spec = template_to_spec(template)
        assert len(spec.edges) == 5

    def test_expected_node_ids(self):
        """6 个节点 id 符合预期（collect_feedback → ... → canary_deploy）."""
        template = load_template_from_file(_TEMPLATE_PATH)
        spec = template_to_spec(template)
        expected_ids = [
            "collect_feedback",
            "prepare_training_data",
            "train_model",
            "evaluate_model",
            "register_model",
            "canary_deploy",
        ]
        actual_ids = [n.node_id for n in spec.nodes]
        assert sorted(actual_ids) == sorted(expected_ids), f"节点 id 不匹配: {actual_ids} vs {expected_ids}"

    def test_linear_topology_edges(self):
        """5 条边构成线性拓扑（collect_feedback → prepare → train → eval → register → canary）."""
        template = load_template_from_file(_TEMPLATE_PATH)
        spec = template_to_spec(template)
        expected_edges = {
            ("collect_feedback", "prepare_training_data"),
            ("prepare_training_data", "train_model"),
            ("train_model", "evaluate_model"),
            ("evaluate_model", "register_model"),
            ("register_model", "canary_deploy"),
        }
        actual_edges = {(e.upstream, e.downstream) for e in spec.edges}
        assert actual_edges == expected_edges, f"边拓扑不匹配: {actual_edges} vs {expected_edges}"

    def test_canary_deploy_is_terminal_node(self):
        """canary_deploy 是终止节点（无下游边）."""
        template = load_template_from_file(_TEMPLATE_PATH)
        spec = template_to_spec(template)
        upstreams = {e.upstream for e in spec.edges}
        # collect_feedback 不是终止节点
        assert "collect_feedback" in upstreams
        # canary_deploy 不在 upstreams 中（无下游）
        assert "canary_deploy" not in upstreams

    def test_collect_feedback_is_root_node(self):
        """collect_feedback 是根节点（无上游边）."""
        template = load_template_from_file(_TEMPLATE_PATH)
        spec = template_to_spec(template)
        downstreams = {e.downstream for e in spec.edges}
        # canary_deploy 不是根节点
        assert "canary_deploy" in downstreams
        # collect_feedback 不在 downstreams 中（无上游）
        assert "collect_feedback" not in downstreams


# task_type 声明（6 种 task_type，handler 在阶段 6 注册）


@pytest.mark.unit
@pytest.mark.contracts
class TestModelIterationTaskTypes:
    """task_type 声明正确性."""

    def test_expected_task_types(self):
        """6 个节点的 task_type 符合预期."""
        template = load_template_from_file(_TEMPLATE_PATH)
        spec = template_to_spec(template)
        expected = {
            "collect_feedback": "feedback_reader",
            "prepare_training_data": "data_processor",
            "train_model": "ltc_trainer",
            "evaluate_model": "model_evaluator",
            "register_model": "model_registry",
            "canary_deploy": "hot_update_manager",
        }
        for node in spec.nodes:
            assert node.task_type == expected[node.node_id], (
                f"节点 {node.node_id} task_type 不匹配: {node.task_type} vs {expected[node.node_id]}"
            )

    def test_task_types_are_distinct(self):
        """6 个节点的 task_type 互不相同（每个节点承担不同职责）."""
        template = load_template_from_file(_TEMPLATE_PATH)
        spec = template_to_spec(template)
        task_types = [n.task_type for n in spec.nodes]
        assert len(task_types) == len(set(task_types)), f"task_type 存在重复: {task_types}"


# 参数与 plugin.yaml config_schema 对齐


@pytest.mark.unit
@pytest.mark.contracts
class TestModelIterationParamsAlignment:
    """节点参数与 plugin.yaml config_schema 默认值对齐."""

    def _get_node_params(self) -> dict[str, dict[str, Any]]:
        """加载模板并返回 {node_id: params} 映射."""
        template = load_template_from_file(_TEMPLATE_PATH)
        spec = template_to_spec(template)
        return {n.node_id: n.params for n in spec.nodes}

    def test_collect_feedback_params_match_config(self):
        """collect_feedback 参数与 feedback_collection config 对齐."""
        params = self._get_node_params()["collect_feedback"]
        assert params["window_hours"] == 24  # feedback_collection.window_hours
        assert params["min_samples"] == 50  # feedback_collection.min_samples_for_training
        assert set(params["feedback_types"]) == {"annotation", "adoption", "correction"}

    def test_evaluate_model_params_match_config(self):
        """evaluate_model 参数与 model_iteration config 对齐."""
        params = self._get_node_params()["evaluate_model"]
        assert params["eval_metric"] == "f1"  # model_iteration.eval_metric
        assert params["min_improvement"] == 0.02  # model_iteration.min_improvement
        assert "f1" in params["metrics"]
        assert params["compare_against_baseline"] is True

    def test_canary_deploy_params_match_config(self):
        """canary_deploy 参数与 hot_update config 对齐."""
        params = self._get_node_params()["canary_deploy"]
        assert params["canary_ratio"] == 0.1  # hot_update.canary_ratio
        assert params["observation_hours"] == 24  # hot_update.canary_observation_hours
        assert params["rollback_on_failure"] is True  # hot_update.rollback_on_failure
        assert params["rollback_metric_drop"] == 0.05  # hot_update.rollback_metric_drop
        assert params["action"] == "canary_deploy"
        assert params["promote_on_success"] is True

    def test_register_model_stage_is_canary(self):
        """register_model 注册阶段为 canary（灰度阶段，非 production）."""
        params = self._get_node_params()["register_model"]
        assert params["stage"] == "canary"
        assert params["action"] == "register"
        assert params["model_type"] == "ltc"
        assert "flywheel" in params["tags"]

    def test_train_model_params_align_with_lnn_trainer(self):
        """train_model 参数与 LNNTrainer 构造函数对齐."""
        params = self._get_node_params()["train_model"]
        assert params["model_type"] == "ltc"
        assert params["hidden_size"] == 64
        assert params["epochs"] == 50
        assert params["batch_size"] == 32
        assert params["learning_rate"] == 0.001
        assert params["seed"] == 42  # 可复现性
        assert params["use_pinn_loss"] is True
        assert params["track_experiment"] is True  # MLflow 追踪


# 节点 inputs 引用合法性


@pytest.mark.unit
@pytest.mark.contracts
class TestModelIterationNodeInputs:
    """节点 inputs 引用合法性（${node_id.output_name} 格式）."""

    def test_prepare_training_data_inputs_reference_collect_feedback(self):
        """prepare_training_data 引用 collect_feedback 的产物 + 工作流级 fallback_dataset_uri."""
        template = load_template_from_file(_TEMPLATE_PATH)
        spec = template_to_spec(template)
        node = next(n for n in spec.nodes if n.node_id == "prepare_training_data")
        assert node.inputs["feedback_records"] == "${collect_feedback.feedback_artifact}"
        assert node.inputs["fallback_dataset"] == "${fallback_dataset_uri}"

    def test_train_model_inputs_reference_prepare_training_data(self):
        """train_model 引用 prepare_training_data 的训练/验证集."""
        template = load_template_from_file(_TEMPLATE_PATH)
        spec = template_to_spec(template)
        node = next(n for n in spec.nodes if n.node_id == "train_model")
        assert node.inputs["train_split"] == "${prepare_training_data.train_split}"
        assert node.inputs["val_split"] == "${prepare_training_data.val_split}"

    def test_evaluate_model_inputs_reference_train_and_baseline(self):
        """evaluate_model 引用 train_model 产物 + 工作流级 base_model_uri."""
        template = load_template_from_file(_TEMPLATE_PATH)
        spec = template_to_spec(template)
        node = next(n for n in spec.nodes if n.node_id == "evaluate_model")
        assert node.inputs["trained_model"] == "${train_model.model_artifact}"
        assert node.inputs["baseline_model"] == "${base_model_uri}"

    def test_canary_deploy_inputs_reference_register_and_evaluate(self):
        """canary_deploy 引用 register_model + evaluate_model + base_model_uri."""
        template = load_template_from_file(_TEMPLATE_PATH)
        spec = template_to_spec(template)
        node = next(n for n in spec.nodes if n.node_id == "canary_deploy")
        assert node.inputs["new_model"] == "${register_model.model_artifact}"
        assert node.inputs["baseline_model"] == "${base_model_uri}"
        assert node.inputs["eval_metrics"] == "${evaluate_model.metrics_artifact}"


# spec_dict 可往返转换为 WorkflowSpec


@pytest.mark.unit
@pytest.mark.contracts
class TestModelIterationSpecRoundTrip:
    """spec_dict 可往返转换为 WorkflowSpec（dict → WorkflowSpec → validate 通过）."""

    def test_spec_dict_can_be_converted_to_workflow_spec(self):
        """spec_dict 可被 _template_dict_to_spec 重新构造为 WorkflowSpec."""
        from app.workflow.templates.loader import _template_dict_to_spec

        template = load_template_from_file(_TEMPLATE_PATH)
        spec = _template_dict_to_spec(template.spec_dict)
        assert spec is not None
        assert spec.name == "模型迭代管线"
        assert len(spec.nodes) == 6

    def test_spec_dict_validate_passes(self):
        """从 spec_dict 重新构造的 WorkflowSpec 能通过 validate()."""
        from app.workflow.templates.loader import _template_dict_to_spec

        template = load_template_from_file(_TEMPLATE_PATH)
        spec = _template_dict_to_spec(template.spec_dict)
        errors = spec.validate()
        assert errors == [], f"spec_dict 重新构造后校验失败: {errors}"


# Plugin._handle_workflow_template_request 行为


@pytest.mark.unit
@pytest.mark.contracts
class TestPluginWorkflowTemplateHandler:
    """Plugin._handle_workflow_template_request 行为测试."""

    def test_handler_returns_ready_with_spec(self, loaded_plugin):
        """无 payload 时返回 status=ready + 非 None spec."""
        result = loaded_plugin._handle_workflow_template_request({})
        assert result["template_name"] == "model_iteration_pipeline"
        assert result["status"] == "ready"
        assert result["spec"] is not None
        assert result["spec"]["name"] == "模型迭代管线"
        assert len(result["spec"]["nodes"]) == 6

    def test_handler_with_matching_template_name(self, loaded_plugin):
        """template_name 匹配时返回 status=ready."""
        result = loaded_plugin._handle_workflow_template_request({"template_name": "model_iteration_pipeline"})
        assert result["status"] == "ready"
        assert result["spec"] is not None

    def test_handler_with_non_matching_template_name(self, loaded_plugin):
        """template_name 不匹配时返回 status=not_found + spec=None."""
        result = loaded_plugin._handle_workflow_template_request({"template_name": "other_template"})
        assert result["status"] == "not_found"
        assert result["spec"] is None
        assert result["template_name"] == "other_template"

    def test_handler_with_none_payload(self, loaded_plugin):
        """payload 为 None 时返回 status=ready（无过滤）."""
        result = loaded_plugin._handle_workflow_template_request(None)  # type: ignore[arg-type]
        assert result["status"] == "ready"
        assert result["spec"] is not None

    def test_handler_spec_dict_validates(self, loaded_plugin):
        """handler 返回的 spec_dict 可通过完整 DAG 校验."""
        from app.workflow.templates.loader import _template_dict_to_spec

        result = loaded_plugin._handle_workflow_template_request({})
        spec = _template_dict_to_spec(result["spec"])
        errors = spec.validate()
        assert errors == [], f"handler 返回的 spec_dict 校验失败: {errors}"

    def test_handler_caches_spec(self, loaded_plugin):
        """_load_model_iteration_spec 首次加载后缓存，第二次返回同一对象."""
        spec1 = loaded_plugin._load_model_iteration_spec()
        spec2 = loaded_plugin._load_model_iteration_spec()
        assert spec1 is spec2, "缓存失效：第二次调用返回了不同对象"
        assert loaded_plugin._model_iteration_spec_loaded is True


# 通过 ExtensionRegistry.invoke 调用 WORKFLOW_TEMPLATE 扩展点


@pytest.mark.unit
@pytest.mark.contracts
class TestModelIterationExtensionPointInvoke:
    """通过 ExtensionRegistry.invoke 调用 WORKFLOW_TEMPLATE 扩展点."""

    def test_invoke_returns_ready_spec(self, loaded_plugin, fresh_registry):
        """invoke WORKFLOW_TEMPLATE 返回 status=ready + 完整 spec."""
        results = _run(
            fresh_registry.invoke(
                BUILTIN_EXTENSION_POINTS.WORKFLOW_TEMPLATE,
                {"template_name": "model_iteration_pipeline"},
            )
        )
        assert len(results) == 1
        result = results[0]
        assert result["status"] == "ready"
        assert result["spec"] is not None
        assert result["spec"]["name"] == "模型迭代管线"
        assert len(result["spec"]["nodes"]) == 6
        assert len(result["spec"]["edges"]) == 5

    def test_invoke_with_non_matching_name_returns_not_found(self, loaded_plugin, fresh_registry):
        """invoke 传入不匹配的 template_name 返回 not_found."""
        results = _run(
            fresh_registry.invoke(
                BUILTIN_EXTENSION_POINTS.WORKFLOW_TEMPLATE,
                {"template_name": "nonexistent"},
            )
        )
        assert len(results) == 1
        assert results[0]["status"] == "not_found"
        assert results[0]["spec"] is None

    def test_invoke_first_returns_ready(self, loaded_plugin, fresh_registry):
        """invoke_first 返回 ready 状态的模板."""
        result = _run(
            fresh_registry.invoke_first(
                BUILTIN_EXTENSION_POINTS.WORKFLOW_TEMPLATE,
                {},
                default={"fallback": True},
            )
        )
        assert result["status"] == "ready"
        assert "fallback" not in result
        assert result["spec"] is not None
