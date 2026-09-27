"""
Uniwear 数据集一键导入与集成脚本

将 Uniwear 多材料刀具磨损数据集完整导入到灵境制造系统：
1. 数据复制到项目目录
2. ChromaDB 知识库构建
3. ML 模型训练
4. 验证阈值校准
5. 经验库导入
"""

import logging
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("import_uniwear")

UNIWEAR_DATA_DIR = str(project_root / "data" / "uniwear")


def step1_check_data() -> bool:
    logger.info("=" * 60)
    logger.info("第1步：检查Uniwear数据文件")
    logger.info("=" * 60)

    required = [
        "nuaa_orthogonal_bundle_high_resolution.csv",
        "phm2010_bundle_high_resolution.csv",
        "uniwear.csv",
    ]
    missing = []
    for fname in required:
        fpath = Path(UNIWEAR_DATA_DIR) / fname
        if fpath.exists():
            size_kb = fpath.stat().st_size / 1024
            logger.info("  ✓ %s (%.1f KB)", fname, size_kb)
        else:
            logger.error("  ✗ %s 不存在", fname)
            missing.append(fname)

    if missing:
        logger.error("  缺少文件: %s", missing)
        return False
    return True


def step2_verify_loader() -> bool:
    logger.info("=" * 60)
    logger.info("第2步：验证Uniwear数据加载器")
    logger.info("=" * 60)

    try:
        from app.data.uniwear_loader import UniwearDataLoader

        loader = UniwearDataLoader(data_dir=UNIWEAR_DATA_DIR)
        summary = loader.get_dataset_summary()

        for ds_key, ds_info in summary.get("datasets", {}).items():
            if "error" in ds_info:
                logger.error("  ✗ %s: %s", ds_key, ds_info["error"])
                return False
            logger.info(
                "  ✓ %s: %s 行, %d 实验",
                ds_key,
                ds_info.get("rows", 0),
                ds_info.get("experiment_count", 0),
            )

        material_comparison = loader.get_material_comparison()
        for mat_key, mat_info in material_comparison.get("materials", {}).items():
            logger.info(
                "  ✓ 材料 %s: %d 实验, %d 总样本",
                mat_key,
                mat_info.get("experiment_count", 0),
                mat_info.get("total_samples", 0),
            )

        return True
    except Exception as e:
        logger.error("  数据加载器验证失败: %s", e)
        return False


def step3_build_knowledge() -> bool:
    logger.info("=" * 60)
    logger.info("第3步：构建ChromaDB向量知识库")
    logger.info("=" * 60)

    try:
        from app.rag.knowledge_base import get_knowledge_base
        from app.rag.uniwear_knowledge_builder import UniwearKnowledgeBuilder

        kb = get_knowledge_base()
        before_count = kb.count()
        logger.info("  当前知识库条目数: %d", before_count)

        builder = UniwearKnowledgeBuilder(knowledge_base=kb, data_dir=UNIWEAR_DATA_DIR)
        result = builder.build_all()

        after_count = kb.count()
        added = after_count - before_count
        logger.info("  ✓ 新增条目: %d (总计: %d)", added, after_count)
        logger.info(
            "  分类: overview=%d, nuaa=%d, phm2010=%d, material=%d, vibration=%d, cross=%d",
            result["by_type"].get("overview", 0),
            result["by_type"].get("nuaa_experiments", 0),
            result["by_type"].get("phm2010_experiments", 0),
            result["by_type"].get("material_comparison", 0),
            result["by_type"].get("vibration_wear_correlation", 0),
            result["by_type"].get("cross_source", 0),
        )

        if added == 0:
            logger.warning("  ⚠ 未新增任何条目，可能已存在")
        return True
    except Exception as e:
        logger.error("  知识库构建失败: %s", e)
        return False


def step4_train_models() -> bool:
    logger.info("=" * 60)
    logger.info("第4步：训练Uniwear磨损预测模型")
    logger.info("=" * 60)

    try:
        from app.services.tool_wear_predictor import ToolWearPredictor

        predictor = ToolWearPredictor()
        result = predictor.train_with_uniwear_data(data_dir=UNIWEAR_DATA_DIR)

        for ds_key, ds_result in result.get("datasets", {}).items():
            if "error" in ds_result:
                logger.error("  ✗ %s: %s", ds_key, ds_result["error"])
                return False
            logger.info(
                "  ✓ %s (%s): MAE=%.6f, RMSE=%.6f, R²=%.4f (%d训练/%d测试样本)",
                ds_key,
                ds_result.get("material", "unknown"),
                ds_result.get("mae", 0),
                ds_result.get("rmse", 0),
                ds_result.get("r2", 0),
                ds_result.get("train_samples", 0),
                ds_result.get("test_samples", 0),
            )

        cross_analysis = predictor.cross_dataset_analysis()
        bosch_status = cross_analysis["bosch_cnc"]["status"]
        uniwear_status = cross_analysis["uniwear"]["status"]
        logger.info("  交叉分析: Bosch=%s, Uniwear=%s", bosch_status, uniwear_status)
        return True
    except Exception as e:
        logger.error("  模型训练失败: %s", e)
        return False


def step5_calibrate_validation() -> bool:
    logger.info("=" * 60)
    logger.info("第5步：多源验证阈值校准")
    logger.info("=" * 60)

    try:
        from app.services.validation_calibrator import ValidationCalibrator

        calibrator = ValidationCalibrator()

        result = calibrator.calibrate_with_uniwear(
            uniwear_data_dir=UNIWEAR_DATA_DIR,
        )

        sources = result.get("sources", {})
        for ds_name, ds_data in sources.items():
            if "error" in ds_data:
                logger.error("  ✗ %s: %s", ds_name, ds_data["error"])
                continue
            logger.info(
                "  ✓ %s (%s): %d 信号RMS值, %d 样本",
                ds_name,
                ds_data.get("material", "unknown"),
                len(ds_data.get("signal_rms", {})),
                ds_data.get("sample_count", 0),
            )

        joint = result.get("joint_thresholds", {})
        logger.info("  ✓ 联合阈值: %s", joint.get("description", "N/A"))

        merged = calibrator.merge_calibration_rules()
        sources_list = merged.get("multi_source_calibration", {}).get("sources", [])
        logger.info(
            "  ✓ 合并校准规则: %d 数据源 (%s)",
            len(sources_list),
            ", ".join(sources_list),
        )
        return True
    except Exception as e:
        logger.error("  验证校准失败: %s", e)
        return False


def step6_import_experiences() -> bool:
    logger.info("=" * 60)
    logger.info("第6步：导入Uniwear加工经验")
    logger.info("=" * 60)

    try:
        from app.services.experience_store import ExperienceStore

        store = ExperienceStore(storage_dir=str(project_root / "data" / "experiences"))
        result = store.import_from_uniwear(uniwear_data_dir=UNIWEAR_DATA_DIR)

        logger.info(
            "  ✓ 导入完成: 总计 %d 条 (NUAA=%d, PHM2010=%d)",
            result.get("imported_count", 0),
            result.get("nuaa_experiences", 0),
            result.get("phm2010_experiences", 0),
        )

        summary = store.get_material_wear_summary()
        for mat, mat_summary in summary.items():
            logger.info(
                "  ✓ 材料 %s: %d 实验, 平均磨损率 %.8f",
                mat,
                mat_summary.get("experiment_count", 0),
                mat_summary.get("avg_wear_rate", 0),
            )
        return True
    except Exception as e:
        logger.error("  经验导入失败: %s", e)
        return False


def step7_verify_retrieval() -> bool:
    logger.info("=" * 60)
    logger.info("第7步：验证RAG检索规则")
    logger.info("=" * 60)

    try:
        from app.rag.knowledge_base import get_knowledge_base
        from app.rag.rag_retrieval import RagRetrievalEngine

        kb = get_knowledge_base()
        engine = RagRetrievalEngine(knowledge_base=kb)

        test_queries = [
            ("钛合金TC4加工磨损特征分析", "TC4"),
            ("不锈钢HRC52切削参数推荐", "HRC52"),
            ("振动信号与刀具磨损的关联性", None),
            ("TC4和HRC52材料工艺对比", None),
            ("Bosch与Uniwear数据交叉验证", None),
        ]

        for query, expected_material in test_queries:
            result = engine.retrieve(query=query, n_results=3)
            intent = result.get("detected_intent", "unknown")
            count = result.get("results_returned", 0)
            status = "✓" if count > 0 else "⚠"
            logger.info(
                '  %s [%s] "%s" → 命中 %d 条',
                status,
                intent,
                query[:30],
                count,
            )

        return True
    except Exception as e:
        logger.error("  检索验证失败: %s", e)
        return False


def main():
    logger.info("Uniwear 数据集一键导入开始")
    logger.info("数据目录: %s", UNIWEAR_DATA_DIR)
    logger.info("项目根目录: %s", project_root)

    steps = [
        ("数据文件检查", step1_check_data),
        ("数据加载器验证", step2_verify_loader),
        ("ChromaDB知识库构建", step3_build_knowledge),
        ("ML模型训练", step4_train_models),
        ("验证阈值校准", step5_calibrate_validation),
        ("经验库导入", step6_import_experiences),
        ("RAG检索验证", step7_verify_retrieval),
    ]

    results = {}
    for step_name, step_func in steps:
        try:
            success = step_func()
            results[step_name] = "success" if success else "failed"
            if not success:
                logger.error("步骤 [%s] 失败，继续执行后续步骤...", step_name)
        except Exception as e:
            logger.exception("步骤 [%s] 异常: %s", step_name, e)
            results[step_name] = f"error: {e}"

    logger.info("=" * 60)
    logger.info("导入完成摘要:")
    for step_name, status in results.items():
        emoji = "✓" if status == "success" else "✗"
        logger.info("  %s %s: %s", emoji, step_name, status)

    all_ok = all(s == "success" for s in results.values())
    if all_ok:
        logger.info("所有步骤成功完成！Uniwear数据集已完全集成到灵境制造系统。")
    else:
        logger.warning("部分步骤未成功，请检查上述错误信息。")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
