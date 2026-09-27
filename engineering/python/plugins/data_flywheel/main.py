"""数据飞轮插件入口点.

对应 core-contracts-design.md 阶段 4 p4-1。

实现 IPlugin 契约，作为飞轮反馈闭环的入口。具体子模块在后续任务中填充：
    - p4-2: feedback_collector（反馈采集层，写入 IDatasetStore）
    - p4-3: model_iteration_pipeline（模型迭代 Workflow 模板）
    - p4-4: flywheel_metrics 改造（从 IDatasetStore/ISnapshotStore 取真实数据）
    - p4-5: hot_update_manager（模型热更新与灰度切换）
    - p4-6: 前端飞轮看板接入真实数据

本文件只提供插件骨架：manifest 加载、扩展点注册、健康检查。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from app.contracts.plugin import (
    BUILTIN_EXTENSION_POINTS,
    IPlugin,
    PluginContext,
    PluginManifest,
)
from app.metrics.flywheel_metrics import (
    configure_flywheel_collector,
    get_flywheel_collector,
)
from app.plugins.extension_registry import get_extension_registry
from app.plugins.manifest_loader import load_manifest_from_dir
from plugins.data_flywheel.feedback_collector import (
    FEEDBACK_DATASET_NAME,
    VALID_FEEDBACK_TYPES,
    FeedbackCollector,
)
from plugins.data_flywheel.hot_update_manager import (
    DeploymentStatus,
    HotUpdateManager,
    configure_hot_update_manager,
    get_hot_update_manager,
    reset_hot_update_manager,
)

logger = logging.getLogger(__name__)

# 模型迭代管线模板文件路径（本插件自带）
_MODEL_ITERATION_TEMPLATE_PATH: Path = Path(__file__).parent / "templates" / "model_iteration_pipeline.yaml"
# 模板名（与 _handle_workflow_template_request 返回的 template_name 一致）
_MODEL_ITERATION_TEMPLATE_NAME: str = "model_iteration_pipeline"


class Plugin(IPlugin):
    """数据飞轮插件主类.

    生命周期：
        on_load:  加载 manifest、缓存 context、注册扩展点贡献
        on_unload: 取消所有扩展点贡献、清理资源
        health_check: 返回飞轮各子系统健康状态

    注册的扩展点贡献：
        - core.ui.workspace_panel: 飞轮看板（前端组件，p4-6 实现渲染）
        - core.workflow_template: 模型迭代管线模板（p4-3 实现 WorkflowSpec）
    """

    def __init__(self) -> None:
        self._manifest: Optional[PluginManifest] = None
        self._context: Optional[PluginContext] = None
        self._registered_points: list[str] = []  # 已注册的扩展点（on_unload 时清理）
        # p4-2: 反馈采集器（on_load 时构造，on_unload 时 flush 落盘）
        self._feedback_collector: Optional[FeedbackCollector] = None
        # p4-3: 模型迭代管线 WorkflowSpec 缓存（首次请求时加载 + 校验）
        self._model_iteration_spec_cache: Optional[dict[str, Any]] = None
        self._model_iteration_spec_loaded: bool = False
        # p4-5: HotUpdateManager 是否已在 on_load 中配置（on_unload 时据此清理）
        self._hot_update_configured: bool = False

    # IPlugin 契约实现

    def get_feedback_collector(self) -> Optional[FeedbackCollector]:
        """返回反馈采集器实例（自进化 M0 反馈提交入口使用）.

        未加载 / 降级模式（dataset_store 不可用）时可能为 None，
        调用方（如 ``POST /api/v1/flywheel/feedback``）需自行处理。
        """
        return self._feedback_collector

    def manifest(self) -> PluginManifest:
        """返回插件清单（从 plugin.yaml 加载，带缓存）."""
        if self._manifest is None:
            plugin_dir = Path(__file__).parent
            self._manifest = load_manifest_from_dir(plugin_dir)
        return self._manifest

    async def on_load(self, context: PluginContext) -> None:
        """插件加载时调用.

        - 缓存 PluginContext
        - 加载插件清单（manifest）
        - 构造 FeedbackCollector（p4-2）
        - 向 ExtensionRegistry 注册飞轮看板、工作流模板、反馈提交扩展点
        """
        self._context = context
        registry = get_extension_registry()
        plugin_id = context.plugin_id

        # 0. 主动加载插件清单：on_load 后 health_check 的 manifest_loaded 必须为 True
        # 清单缺失属降级模式（保留 None，health_check 如实上报 unhealthy）
        try:
            self.manifest()
        except Exception:
            logger.exception("数据飞轮插件清单加载失败，进入降级模式")
            self._manifest = None

        # 1. 构造反馈采集器（p4-2）
        # dataset_store 可能为 None（降级模式），FeedbackCollector 内部处理
        feedback_config = context.config.get("feedback_collection", {}) if context.config else {}
        self._feedback_collector = FeedbackCollector(
            dataset_store=context.dataset_store,
            owner_id=f"plugin:{plugin_id}",
            config=feedback_config,
        )

        # 1. 注册工作区面板扩展点：飞轮看板
        # p4-6 中前端 FlywheelDashboard.vue 通过此贡献渲染
        registry.register_component(
            extension_point=BUILTIN_EXTENSION_POINTS.UI_WORKSPACE_PANEL,
            plugin_id=plugin_id,
            component_url="FlywheelDashboard.vue",
            props={
                "title": "数据飞轮",
                "layout": "tabs",
                "tabs": [
                    {"id": "overview", "title": "概览"},
                    {"id": "feedback", "title": "反馈采集"},
                    {"id": "models", "title": "模型迭代"},
                    {"id": "metrics", "title": "飞轮指标"},
                ],
            },
            metadata={
                "title": "数据飞轮",
                "icon": "flywheel",
                "order": 100,
            },
        )
        self._registered_points.append(BUILTIN_EXTENSION_POINTS.UI_WORKSPACE_PANEL)

        # 2. 注册工作流模板扩展点：模型迭代管线
        # p4-3 中实现完整的 WorkflowSpec（采集训练评估热更新）
        registry.register(
            extension_point=BUILTIN_EXTENSION_POINTS.WORKFLOW_TEMPLATE,
            plugin_id=plugin_id,
            handler=self._handle_workflow_template_request,
            metadata={
                "template_name": "model_iteration_pipeline",
                "description": "反馈采集 → 训练 → 评估 → 灰度热更新",
                "version": "0.1.0",
            },
        )
        self._registered_points.append(BUILTIN_EXTENSION_POINTS.WORKFLOW_TEMPLATE)

        # 3. 注册任务处理器扩展点：反馈提交（p4-2）
        # 前端/其他模块通过 TASK_HANDLER 提交用户反馈到 IDatasetStore
        registry.register(
            extension_point=BUILTIN_EXTENSION_POINTS.TASK_HANDLER,
            plugin_id=plugin_id,
            handler=self._handle_feedback_submission,
            metadata={
                "task_type": "submit_feedback",
                "description": "提交用户反馈（标注/采纳/修正）到反馈数据集",
                "feedback_types": sorted(VALID_FEEDBACK_TYPES),
                "dataset_name": FEEDBACK_DATASET_NAME,
            },
        )
        self._registered_points.append(BUILTIN_EXTENSION_POINTS.TASK_HANDLER)

        # 4. p4-5: 注册任务处理器扩展点：模型热更新（canary_deploy/observe/promote/rollback）
        # model_iteration_pipeline.yaml 的 canary_deploy 节点 task_type=hot_update_manager
        # 通过此 handler 调用 HotUpdateManager 完成灰度部署生命周期
        registry.register(
            extension_point=BUILTIN_EXTENSION_POINTS.TASK_HANDLER,
            plugin_id=plugin_id,
            handler=self._handle_hot_update_request,
            metadata={
                "task_type": "hot_update_manager",
                "description": "模型热更新：灰度部署 / 观察 / 晋升 / 回滚",
                "actions": [
                    "canary_deploy",
                    "observe",
                    "promote",
                    "rollback",
                    "list_deployments",
                    "get_deployment",
                    "select_model",
                ],
            },
        )
        # TASK_HANDLER 已在上一步 append 过，此处不重复 append（同一扩展点允许多个 handler）
        # 但 _registered_points 用于 on_unload 的 unregister(plugin_id) 批量清理，
        # 重复 append 会导致清理计数偏高，因此保持只 append 一次

        # 5. p4-4c: 把 dataset_store / snapshot_store 注入到全局飞轮采集器
        # FlywheelMetricsCollector 通过 IDatasetStore 读取 feedback_records，
        # 通过 ISnapshotStore 读取模型质量/不确定性。
        # snapshot_store 从 observability 模块获取（全局单例，懒初始化）。
        snapshot_store = self._resolve_snapshot_store()
        configure_flywheel_collector(
            dataset_store=context.dataset_store,
            snapshot_store=snapshot_store,
        )
        # 若 FeedbackCollector 已有缓存的 dataset_id（极少见：插件重加载场景），
        # 立即注入到 FlywheelMetricsCollector
        if self._feedback_collector is not None and self._feedback_collector.dataset_id is not None:
            try:
                get_flywheel_collector().set_feedback_dataset_id(self._feedback_collector.dataset_id)
            except ValueError:
                logger.warning(
                    "注入缓存的 feedback_dataset_id 失败: %s",
                    self._feedback_collector.dataset_id,
                    exc_info=True,
                )

        # 6. p4-5: 配置全局 HotUpdateManager 单例
        # 注入 ModelRegistryService（来自 app.services.model_registry_service）
        # 与 hot_update 配置（来自 plugin.yaml config_schema.hot_update section）。
        # ModelRegistryService 不可用时降级运行（仅记录 DeploymentRecord）。
        hot_update_config = context.config.get("hot_update", {}) if context.config else {}
        model_registry_service = self._resolve_model_registry_service()
        configure_hot_update_manager(
            model_registry_service=model_registry_service,
            snapshot_store=snapshot_store,
            config=hot_update_config,
        )
        self._hot_update_configured = True

        logger.info(
            "数据飞轮插件已加载 (plugin_id=%s, data_dir=%s, 注册扩展点=%d, "
            "feedback_collector=%s, flywheel_collector_configured=%s, "
            "hot_update_degraded=%s)",
            plugin_id,
            context.data_dir,
            len(self._registered_points),
            "ready" if context.dataset_store is not None else "degraded",
            "yes" if snapshot_store is not None else "no",
            "yes" if model_registry_service is None else "no",
        )

    async def on_unload(self) -> None:
        """插件卸载时调用.

        - flush 反馈采集器剩余缓冲区到 IDatasetStore（p4-2）
        - 取消所有已注册的扩展点贡献，释放资源
        """
        if self._context is None:
            return

        # p4-2: 卸载前 flush 剩余反馈，避免数据丢失
        if self._feedback_collector is not None:
            try:
                if self._feedback_collector.buffer_size > 0:
                    await self._feedback_collector.flush()
                    logger.info(
                        "反馈采集器卸载前 flush 完成 (plugin_id=%s)",
                        self._context.plugin_id,
                    )
                # p4-4c: flush 后把 dataset_id 注入到全局飞轮采集器，
                # 保证后续 API 调用能读到刚落盘的反馈数据
                self._maybe_inject_feedback_dataset_id()
            except Exception:  # noqa: BLE001
                # flush 失败不阻塞卸载（缓冲区记录已丢失，记录错误即可）
                logger.error(
                    "反馈采集器卸载前 flush 失败 (plugin_id=%s, 缓冲区=%d 条)",
                    self._context.plugin_id,
                    self._feedback_collector.buffer_size,
                    exc_info=True,
                )
            finally:
                self._feedback_collector = None

        registry = get_extension_registry()
        count = registry.unregister(self._context.plugin_id)
        logger.info(
            "数据飞轮插件卸载 (plugin_id=%s, 取消扩展点贡献=%d)",
            self._context.plugin_id,
            count,
        )

        # p4-5: 清理全局 HotUpdateManager 单例
        # 注意：仅清理本插件配置的实例。若其他插件也配置了 HotUpdateManager
        # （目前不存在此场景），需改为引用计数模式。
        if self._hot_update_configured:
            reset_hot_update_manager()
            self._hot_update_configured = False

        self._registered_points.clear()
        self._context = None

    def health_check(self) -> dict[str, Any]:
        """健康检查.

        检查飞轮插件各子系统是否就绪。

        Returns:
            {
                "healthy": bool,
                "checks": {
                    "manifest_loaded": bool,
                    "context_injected": bool,
                    "dataset_store_available": bool,
                    "task_registry_available": bool,
                    "observability_available": bool,
                    "feedback_collector_available": bool,  # p4-2
                    "hot_update_manager_available": bool,  # p4-5
                },
                "feedback_stats": dict,  # p4-2: 反馈采集器统计
                "hot_update_stats": dict,  # p4-5: 热更新管理器统计
                "message": str
            }
        """
        ctx = self._context
        checks: dict[str, bool] = {
            "manifest_loaded": self._manifest is not None,
            "context_injected": ctx is not None,
            "dataset_store_available": ctx is not None and ctx.dataset_store is not None,
            "task_registry_available": ctx is not None and ctx.task_registry is not None,
            "observability_available": ctx is not None and ctx.observability is not None,
            "feedback_collector_available": self._feedback_collector is not None,
            "hot_update_manager_available": self._hot_update_configured,
        }
        healthy = all(checks.values())

        # p4-2: 附带反馈采集器统计（同步属性，不触发 IO）
        feedback_stats: dict[str, Any] = {}
        if self._feedback_collector is not None:
            feedback_stats = {
                "buffer_size": self._feedback_collector.buffer_size,
                "total_recorded": self._feedback_collector.total_recorded,
                "total_flushed": self._feedback_collector.total_flushed,
                "dataset_id": self._feedback_collector.dataset_id,
                "dataset_store_available": self._feedback_collector.config.get("batch_size") is not None,
            }

        # p4-5: 附带热更新管理器统计（同步属性，不触发 IO）
        hot_update_stats: dict[str, Any] = {}
        if self._hot_update_configured:
            try:
                manager = get_hot_update_manager()
                hot_update_stats = {
                    "degraded": manager.is_degraded,
                    "total_deployments": len(manager._deployments),  # noqa: SLF001
                    "observing_count": sum(
                        1
                        for r in manager._deployments.values()  # noqa: SLF001
                        if r.status == DeploymentStatus.OBSERVING
                    ),
                    "promoted_count": sum(
                        1
                        for r in manager._deployments.values()  # noqa: SLF001
                        if r.status == DeploymentStatus.PROMOTED
                    ),
                    "rolled_back_count": sum(
                        1
                        for r in manager._deployments.values()  # noqa: SLF001
                        if r.status == DeploymentStatus.ROLLED_BACK
                    ),
                    "model_stages_tracked": len(manager._model_stages),  # noqa: SLF001
                }
            except Exception:  # noqa: BLE001
                logger.warning("采集 hot_update_stats 失败", exc_info=True)
                hot_update_stats = {"error": "stats_unavailable"}

        return {
            "healthy": healthy,
            "checks": checks,
            "feedback_stats": feedback_stats,
            "hot_update_stats": hot_update_stats,
            "message": "数据飞轮插件运行正常" if healthy else "部分核心依赖不可用",
        }

    # 扩展点处理器（p4-2 ~ p4-6 中实现具体逻辑）

    def _resolve_snapshot_store(self) -> Any:
        """解析 ISnapshotStore 实例（p4-4c）.

        优先使用 ``app.observability.get_snapshot_store()`` 全局单例。
        若 observability 模块不可用（极少数环境），返回 None，
        ``FlywheelMetricsCollector`` 会以 model_quality/uncertainty_mean=0 降级。

        Returns:
            ``ISnapshotStore`` 实例或 None
        """
        try:
            from app.observability import get_snapshot_store

            return get_snapshot_store()
        except ImportError:  # pragma: no cover
            logger.warning(
                "observability 模块不可用，flywheel_metrics 将无法采集 model_quality 与 uncertainty_mean（降级为 0）",
                exc_info=True,
            )
            return None
        except Exception:  # noqa: BLE001
            # get_snapshot_store 不应抛错（懒初始化），但兜底保护
            logger.warning(
                "获取 snapshot_store 失败，flywheel_metrics 降级运行",
                exc_info=True,
            )
            return None

    def _resolve_model_registry_service(self) -> Any:
        """解析 ModelRegistryService 实例（p4-5）.

        优先使用 ``app.services.model_registry_service.get_model_registry_service()``
        全局单例。若模块不可用（极少数环境），返回 None，
        ``HotUpdateManager`` 会以降级模式运行（仅记录 DeploymentRecord）。

        Returns:
            ``ModelRegistryService`` 实例或 None
        """
        try:
            from app.services.model_registry_service import (
                get_model_registry_service,
            )

            return get_model_registry_service()
        except ImportError:  # pragma: no cover
            logger.warning(
                "model_registry_service 模块不可用，hot_update_manager 将以降级模式 "
                "运行（仅记录 DeploymentRecord，不实际注册模型）",
                exc_info=True,
            )
            return None
        except Exception:  # noqa: BLE001
            # get_model_registry_service 不应抛错（懒初始化），但兜底保护
            logger.warning(
                "获取 model_registry_service 失败，hot_update_manager 降级运行",
                exc_info=True,
            )
            return None

    def _handle_workflow_template_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """工作流模板扩展点处理器.

        当核心层请求 ``core.workflow_template`` 时被调用，返回模型迭代管线
        的 WorkflowSpec 描述。p4-3 已实现完整的 WorkflowSpec（含 6 节点线性 DAG）。

        模板文件：``templates/model_iteration_pipeline.yaml``
        DAG 拓扑：collect_feedback → prepare_training_data → train_model
                  → evaluate_model → register_model → canary_deploy

        Args:
            payload: 调用载荷，可含 ``template_name`` 过滤。当 template_name
                不匹配本插件提供的模板时，返回 ``status="not_found"``。

        Returns:
            包含模板名、描述、spec、status 的字典。status 取值：
                - ``ready``: spec 加载并校验成功
                - ``not_found``: template_name 不匹配
                - ``error``: 模板加载或 DAG 校验失败（spec 为 None）
        """
        # template_name 过滤：核心层可能枚举多个插件的工作流模板
        requested_name = payload.get("template_name") if payload else None
        if requested_name is not None and requested_name != _MODEL_ITERATION_TEMPLATE_NAME:
            return {
                "template_name": requested_name,
                "description": "",
                "version": "0.1.0",
                "spec": None,
                "status": "not_found",
            }

        spec_dict = self._load_model_iteration_spec()
        return {
            "template_name": _MODEL_ITERATION_TEMPLATE_NAME,
            "description": "反馈采集 → 训练 → 评估 → 模型注册 → 灰度热更新",
            "version": "0.1.0",
            "spec": spec_dict,
            "status": "ready" if spec_dict is not None else "error",
        }

    def _load_model_iteration_spec(self) -> Optional[dict[str, Any]]:
        """加载模型迭代管线模板并校验 DAG.

        首次调用时读取 ``templates/model_iteration_pipeline.yaml``，
        通过 ``app.workflow.templates.loader.load_template_from_file`` 进行结构校验，
        再通过 ``template_to_spec`` 进行 DAG 一致性校验（无环、引用合法）。
        校验通过后缓存 spec_dict，后续调用直接返回缓存。

        Returns:
            WorkflowSpec 的 dict 形式（与 ``/api/v1/workflows/run`` 的 body.spec 格式一致）。
            加载或校验失败时返回 None，并记录错误日志。
        """
        if self._model_iteration_spec_loaded:
            return self._model_iteration_spec_cache

        # 懒导入：避免在模块加载期强依赖 workflow 模块（如循环导入或环境缺失）
        try:
            from app.workflow.templates.loader import (
                TemplateNotFoundError,
                load_template_from_file,
                template_to_spec,
            )
            from app.workflow.validator import WorkflowValidationError
        except ImportError:  # pragma: no cover
            logger.error(
                "加载模型迭代管线模板失败：workflow 模块不可用 (path=%s)",
                _MODEL_ITERATION_TEMPLATE_PATH,
                exc_info=True,
            )
            self._model_iteration_spec_loaded = True
            return None

        try:
            template = load_template_from_file(_MODEL_ITERATION_TEMPLATE_PATH)
        except TemplateNotFoundError:
            logger.error("模型迭代管线模板文件不存在: %s", _MODEL_ITERATION_TEMPLATE_PATH)
            self._model_iteration_spec_loaded = True
            return None
        except ValueError:
            logger.error(
                "模型迭代管线模板结构非法 (path=%s)",
                _MODEL_ITERATION_TEMPLATE_PATH,
                exc_info=True,
            )
            self._model_iteration_spec_loaded = True
            return None

        try:
            # 执行完整 DAG 校验（节点 id 唯一性、边引用合法性、无环、inputs/outputs 引用合法性）
            template_to_spec(template)
        except WorkflowValidationError as e:
            logger.error(
                "模型迭代管线 DAG 校验失败 (template_id=%s): %s",
                template.template_id,
                e,
            )
            self._model_iteration_spec_loaded = True
            return None

        self._model_iteration_spec_cache = template.spec_dict
        self._model_iteration_spec_loaded = True
        logger.info(
            "模型迭代管线模板加载成功 (template_id=%s, nodes=%d, edges=%d)",
            template.template_id,
            len(template.spec_dict.get("nodes") or []),
            len(template.spec_dict.get("edges") or []),
        )
        return self._model_iteration_spec_cache

    # p4-2: 反馈提交处理器

    async def _handle_feedback_submission(self, payload: dict[str, Any]) -> dict[str, Any]:
        """反馈提交任务处理器.

        前端/其他模块通过 TASK_HANDLER 扩展点提交用户反馈。payload 格式：

            {
                "feedback_type": "annotation" | "adoption" | "correction",
                "user_id": "u-xxx",
                # adoption 必填
                "accepted": true | false,
                # correction 必填
                "original_output": {...},
                "corrected_output": {...},
                # 通用可选
                "prediction_id": "pred-xxx",
                "model_version": "v1.2.0",
                "notes": "...",
                "metadata": {...}
            }

        Args:
            payload: 反馈载荷（见上）

        Returns:
            {"success": bool, "feedback_id": str, "buffer_size": int}

        Raises:
            ValueError: feedback_type 不合法或必填字段缺失
            RuntimeError: 反馈采集器未初始化
        """
        if self._feedback_collector is None:
            raise RuntimeError("反馈采集器未初始化（插件未 on_load）")

        feedback_type = payload.get("feedback_type")
        if feedback_type not in VALID_FEEDBACK_TYPES:
            raise ValueError(f"feedback_type 不合法: {feedback_type}，合法值: {sorted(VALID_FEEDBACK_TYPES)}")

        common_kwargs = {
            "user_id": payload.get("user_id", "anonymous"),
            "prediction_id": payload.get("prediction_id"),
            "model_version": payload.get("model_version"),
            "original_output": payload.get("original_output"),
            "notes": payload.get("notes", ""),
            "metadata": payload.get("metadata"),
        }

        if feedback_type == "annotation":
            feedback_id = await self._feedback_collector.record_annotation(**common_kwargs)
        elif feedback_type == "adoption":
            accepted = payload.get("accepted")
            if not isinstance(accepted, bool):
                raise ValueError(f"adoption 类型必须提供 bool 类型的 accepted，收到: {type(accepted).__name__}")
            feedback_id = await self._feedback_collector.record_adoption(accepted=accepted, **common_kwargs)
        else:  # correction
            original_output = payload.get("original_output")
            corrected_output = payload.get("corrected_output")
            if not original_output or not corrected_output:
                raise ValueError("correction 类型必须提供 original_output 与 corrected_output")
            feedback_id = await self._feedback_collector.record_correction(
                original_output=original_output,
                corrected_output=corrected_output,
                user_id=common_kwargs["user_id"],
                prediction_id=common_kwargs["prediction_id"],
                model_version=common_kwargs["model_version"],
                notes=common_kwargs["notes"],
                metadata=common_kwargs["metadata"],
            )

        # p4-4c: 反馈提交后，若 FeedbackCollector 因 batch_size 自动 flush
        # 或其他路径解析出了 dataset_id，立即注入到全局 FlywheelMetricsCollector。
        # 这样飞轮指标 API 才能读到真实数据。
        self._maybe_inject_feedback_dataset_id()

        return {
            "success": True,
            "feedback_id": feedback_id,
            "buffer_size": self._feedback_collector.buffer_size,
        }

    def _maybe_inject_feedback_dataset_id(self) -> None:
        """把 FeedbackCollector.dataset_id 懒注入到 FlywheelMetricsCollector（p4-4c）.

        - 若 FeedbackCollector 未初始化或尚未 flush（dataset_id=None），跳过
        - 若 FlywheelMetricsCollector 已注入同一 dataset_id，跳过（幂等）
        - 否则调用 ``set_feedback_dataset_id()`` 注入

        此方法在每次反馈提交后调用，保证 dataset_id 在首次 flush 后立即可用，
        无需用户手动调用 ``flush()``。
        """
        if self._feedback_collector is None:
            return
        dataset_id = self._feedback_collector.dataset_id
        if dataset_id is None:
            return
        try:
            collector = get_flywheel_collector()
            if collector.feedback_dataset_id == dataset_id:
                return  # 幂等
            collector.set_feedback_dataset_id(dataset_id)
        except ValueError:
            logger.warning(
                "注入 feedback_dataset_id 到 FlywheelMetricsCollector 失败: %s",
                dataset_id,
                exc_info=True,
            )
        except Exception:  # noqa: BLE001
            # 全局采集器获取失败等异常不阻塞反馈提交主流程
            logger.warning(
                "访问 FlywheelMetricsCollector 失败，跳过 dataset_id 注入",
                exc_info=True,
            )

    # p4-2: 公开 API（供 p4-4 飞轮指标与 p4-6 前端看板使用）

    @property
    def feedback_collector(self) -> Optional[FeedbackCollector]:
        """反馈采集器实例（供 p4-4 飞轮指标读取反馈数据）."""
        return self._feedback_collector

    # p4-5: 模型热更新处理器

    async def _handle_hot_update_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """模型热更新任务处理器（p4-5）.

        通过 TASK_HANDLER 扩展点接收热更新请求。payload 格式：

            {
                "action": "canary_deploy" | "observe" | "promote" | "rollback"
                          | "list_deployments" | "get_deployment" | "select_model",
                # canary_deploy 必填
                "model_name": "ltc-chatter",
                "new_model_uri": "model://ltc-chatter-v3",
                "baseline_model_uri": "model://ltc-chatter-v2",
                "eval_metrics": {"f1": 0.92, "mae": 0.05},
                "baseline_metrics": {"f1": 0.88, "mae": 0.07},
                "eval_metric": "f1",
                "canary_ratio": 0.1,
                "observation_hours": 24,
                "rollback_on_failure": true,
                "rollback_metric_drop": 0.05,
                "promote_on_success": true,
                # observe 必填
                "deployment_id": "dep-xxx",
                "current_canary_metrics": {"f1": 0.91, "mae": 0.06},
                # promote / rollback / get_deployment 必填
                # "deployment_id": "dep-xxx",
                # rollback 可选
                "reason": "指标退化",
                # list_deployments 可选
                "filter_model_name": "ltc-chatter",
                "filter_status": "observing",
                # select_model 必填
                # "model_name": "ltc-chatter",
            }

        Args:
            payload: 热更新请求载荷（见上）

        Returns:
            按 action 不同返回不同结构：
                - canary_deploy: {"action": ..., "deployment": DeploymentRecord.to_dict()}
                - observe: {"action": ..., "decision": ObservationDecision.to_dict()}
                - promote/rollback: {"action": ..., "deployment": DeploymentRecord.to_dict()}
                - list_deployments: {"action": ..., "deployments": [DeploymentRecord.to_dict()]}
                - get_deployment: {"action": ..., "deployment": DeploymentRecord.to_dict()}
                - select_model: {"action": ..., "model_uri": str}

        Raises:
            ValueError: action 不合法或必填字段缺失
            KeyError: deployment_id / model_name 不存在
            RuntimeError: HotUpdateManager 未配置
        """
        if not self._hot_update_configured:
            raise RuntimeError("HotUpdateManager 未配置（插件未 on_load 或配置失败）")

        action = payload.get("action")
        if not action:
            raise ValueError("payload.action 不能为空")

        manager = get_hot_update_manager()

        if action == "canary_deploy":
            return await self._hot_update_canary_deploy(manager, payload)
        if action == "observe":
            return await self._hot_update_observe(manager, payload)
        if action == "promote":
            return await self._hot_update_promote(manager, payload)
        if action == "rollback":
            return await self._hot_update_rollback(manager, payload)
        if action == "list_deployments":
            return await self._hot_update_list_deployments(manager, payload)
        if action == "get_deployment":
            return await self._hot_update_get_deployment(manager, payload)
        if action == "select_model":
            return self._hot_update_select_model(manager, payload)

        raise ValueError(
            f"不支持的 action: {action}，合法值: canary_deploy / observe / promote / "
            "rollback / list_deployments / get_deployment / select_model"
        )

    async def _hot_update_canary_deploy(self, manager: HotUpdateManager, payload: dict[str, Any]) -> dict[str, Any]:
        """处理 canary_deploy 动作."""
        model_name = payload.get("model_name")
        new_model_uri = payload.get("new_model_uri")
        baseline_model_uri = payload.get("baseline_model_uri")
        eval_metrics = payload.get("eval_metrics")

        if not model_name:
            raise ValueError("canary_deploy 缺少必填字段: model_name")
        if not new_model_uri:
            raise ValueError("canary_deploy 缺少必填字段: new_model_uri")
        if not baseline_model_uri:
            raise ValueError("canary_deploy 缺少必填字段: baseline_model_uri")
        if not eval_metrics or not isinstance(eval_metrics, dict):
            raise ValueError("canary_deploy 缺少必填字段: eval_metrics (dict)")

        record = await manager.canary_deploy(
            model_name=model_name,
            new_model_uri=new_model_uri,
            baseline_model_uri=baseline_model_uri,
            eval_metrics=eval_metrics,
            baseline_metrics=payload.get("baseline_metrics"),
            eval_metric=payload.get("eval_metric", "f1"),
            canary_ratio=payload.get("canary_ratio"),
            observation_hours=payload.get("observation_hours"),
            rollback_on_failure=payload.get("rollback_on_failure"),
            rollback_metric_drop=payload.get("rollback_metric_drop"),
            promote_on_success=payload.get("promote_on_success", True),
            metadata=payload.get("metadata"),
        )
        return {"action": "canary_deploy", "deployment": record.to_dict()}

    async def _hot_update_observe(self, manager: HotUpdateManager, payload: dict[str, Any]) -> dict[str, Any]:
        """处理 observe 动作."""
        deployment_id = payload.get("deployment_id")
        current_canary_metrics = payload.get("current_canary_metrics")
        if not deployment_id:
            raise ValueError("observe 缺少必填字段: deployment_id")
        if not current_canary_metrics or not isinstance(current_canary_metrics, dict):
            raise ValueError("observe 缺少必填字段: current_canary_metrics (dict)")

        decision = await manager.observe_deployment(
            deployment_id=deployment_id,
            current_canary_metrics=current_canary_metrics,
        )
        return {"action": "observe", "decision": decision.to_dict()}

    async def _hot_update_promote(self, manager: HotUpdateManager, payload: dict[str, Any]) -> dict[str, Any]:
        """处理 promote 动作."""
        deployment_id = payload.get("deployment_id")
        if not deployment_id:
            raise ValueError("promote 缺少必填字段: deployment_id")
        record = await manager.promote(deployment_id)
        return {"action": "promote", "deployment": record.to_dict()}

    async def _hot_update_rollback(self, manager: HotUpdateManager, payload: dict[str, Any]) -> dict[str, Any]:
        """处理 rollback 动作."""
        deployment_id = payload.get("deployment_id")
        if not deployment_id:
            raise ValueError("rollback 缺少必填字段: deployment_id")
        record = await manager.rollback(deployment_id, reason=payload.get("reason", ""))
        return {"action": "rollback", "deployment": record.to_dict()}

    async def _hot_update_list_deployments(self, manager: HotUpdateManager, payload: dict[str, Any]) -> dict[str, Any]:
        """处理 list_deployments 动作."""
        model_name = payload.get("filter_model_name")
        status_str = payload.get("filter_status")
        status: Optional[DeploymentStatus] = None
        if status_str is not None:
            try:
                status = DeploymentStatus(status_str)
            except ValueError:
                raise ValueError(
                    f"filter_status 不合法: {status_str}，合法值: {[s.value for s in DeploymentStatus]}"
                ) from None

        records = await manager.list_deployments(model_name=model_name, status=status)
        return {
            "action": "list_deployments",
            "deployments": [r.to_dict() for r in records],
            "count": len(records),
        }

    async def _hot_update_get_deployment(self, manager: HotUpdateManager, payload: dict[str, Any]) -> dict[str, Any]:
        """处理 get_deployment 动作."""
        deployment_id = payload.get("deployment_id")
        if not deployment_id:
            raise ValueError("get_deployment 缺少必填字段: deployment_id")
        record = await manager.get_deployment(deployment_id)
        return {"action": "get_deployment", "deployment": record.to_dict()}

    def _hot_update_select_model(self, manager: HotUpdateManager, payload: dict[str, Any]) -> dict[str, Any]:
        """处理 select_model 动作（同步）."""
        model_name = payload.get("model_name")
        if not model_name:
            raise ValueError("select_model 缺少必填字段: model_name")
        model_uri = manager.select_model_for_request(model_name)
        return {"action": "select_model", "model_uri": model_uri}

    # p4-5: 公开 API（供 p4-6 前端看板与推理路径使用）

    @property
    def hot_update_manager(self) -> Optional[HotUpdateManager]:
        """HotUpdateManager 实例（供 p4-6 前端看板读取部署状态）.

        未配置时返回 None（插件未 on_load 或配置失败）。
        """
        if not self._hot_update_configured:
            return None
        try:
            return get_hot_update_manager()
        except Exception:  # noqa: BLE001
            return None


__all__ = ["Plugin"]
