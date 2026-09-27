import logging
import uuid
from collections.abc import Callable


from app.data.uniwear_loader import (
    UniwearDataLoader,
    UniwearDataset,
    NUAA_MATERIAL,
    NUAA_MATERIAL_FULL,
    PHM2010_MATERIAL,
    PHM2010_MATERIAL_FULL,
)

logger = logging.getLogger(__name__)

PROCESS_CN_MAP = {
    "W1": "正交切削实验1",
    "W2": "正交切削实验2",
    "W3": "正交切削实验3",
    "W4": "正交切削实验4",
    "W5": "正交切削实验5",
    "W6": "正交切削实验6",
    "W7": "正交切削实验7",
    "W8": "正交切削实验8",
    "W9": "正交切削实验9",
    "c1": "全寿命切削实验1",
    "c4": "全寿命切削实验4",
    "c6": "全寿命切削实验6",
}


class UniwearKnowledgeBuilder:
    """从 Uniwear 多材料刀具磨损数据集自动构建知识条目导入 ChromaDB"""

    def __init__(self, knowledge_base, data_dir: str = "python/data/uniwear"):
        self.kb = knowledge_base
        self.loader = UniwearDataLoader(data_dir=data_dir)
        self._progress_callback: Callable[[str, int, int], None] | None = None

    def set_progress_callback(self, callback: Callable[[str, int, int], None]):
        self._progress_callback = callback

    def _notify(self, stage: str, current: int, total: int):
        if self._progress_callback:
            self._progress_callback(stage, current, total)

    def build_dataset_overview(self) -> str:
        summary = self.loader.get_dataset_summary()
        mat_comparison = self.loader.get_material_comparison()

        lines: list[str] = []
        lines.append("【Uniwear 多材料刀具磨损数据集概述】")
        lines.append("Uniwear 是一个面向刀具磨损预测与健康监测的多材料数据集，包含两个子数据集：")
        lines.append("")

        for ds_key, ds_info in summary.get("datasets", {}).items():
            if "error" in ds_info:
                continue
            lines.append(f"## {ds_key.upper()} 数据集")
            lines.append(f"- 文件：{ds_info.get('file', 'N/A')}")
            lines.append(f"- 样本数：{ds_info.get('rows', 0):,} 行")
            lines.append(f"- 特征列数：{ds_info.get('columns', 0)}")
            lines.append(f"- 实验数：{ds_info.get('experiment_count', 0)}")
            lines.append(f"- 材料：{ds_info.get('material_full', ds_info.get('material', 'N/A'))}")
            lines.append(f"- 信号类型：{ds_info.get('signal_types', 'N/A')}")
            lines.append(f"- 实验编号：{', '.join(ds_info.get('experiments', []))}")
            lines.append("")

        lines.append("## 材料对比")
        for mat_key, mat_info in mat_comparison.get("materials", {}).items():
            lines.append(f"### {mat_info.get('full_name', mat_key)} ({mat_key})")
            lines.append(f"- 实验数：{mat_info.get('experiment_count', 0)}")
            lines.append(f"- 总样本数：{mat_info.get('total_samples', 0):,}")
            wear_rates = []
            for exp_name, exp_data in mat_info.get("experiments", {}).items():
                if "error" not in exp_data:
                    wear_rates.append(f"  · {exp_name}：磨损率 {exp_data.get('wear_rate', 0):.6f}")
            if wear_rates:
                lines.append("- 各实验磨损率：")
                lines.extend(wear_rates)
            lines.append("")

        lines.append("## 应用场景")
        lines.append("- NUAA数据集（TC4钛合金）：适用于航空航天材料加工刀具磨损预测")
        lines.append("- PHM2010数据集（HRC52不锈钢）：适用于高硬度不锈钢加工刀具状态监测")
        lines.append("- 联合分析可支持跨材料迁移学习，提高模型泛化能力")

        doc_id = "uniwear_dataset_overview"
        document = "\n".join(lines)
        # 结构化实体：数据集概述涵盖所有数据集与材料
        overview_entities = [
            "uniwear",
            "nuaa",
            "phm2010",
            NUAA_MATERIAL.lower(),
            PHM2010_MATERIAL.lower(),
            NUAA_MATERIAL_FULL.lower(),
            PHM2010_MATERIAL_FULL.lower(),
            "tc4",
            "hrc52",
            "钛合金",
            "不锈钢",
            "刀具磨损",
            "多材料",
            "数据集",
        ]
        try:
            self.kb.add_knowledge(
                document=document,
                metadata={
                    "source": "uniwear",
                    "type": "dataset_overview",
                    "category": "dataset_info",
                    "materials": f"{NUAA_MATERIAL},{PHM2010_MATERIAL}",
                    "keywords": "Uniwear,NUAA,PHM2010,刀具磨损,多材料,TC4,HRC52,数据集概述",
                },
                doc_id=doc_id,
                entities=overview_entities,
            )
        except (ValueError, RuntimeError, ConnectionError, OSError) as kb_err:
            # 主 doc_id 冲突时，UUID 后缀兜底；其他异常（序列化/类型错误等）也一并捕获
            logger.debug(
                "Primary doc_id collision, retrying with uuid suffix: %s",
                kb_err,
                exc_info=True,
            )
            doc_id = f"{doc_id}_{uuid.uuid4().hex[:8]}"
            self.kb.add_knowledge(
                document=document,
                metadata={
                    "source": "uniwear",
                    "type": "dataset_overview",
                    "category": "dataset_info",
                    "materials": f"{NUAA_MATERIAL},{PHM2010_MATERIAL}",
                    "keywords": "Uniwear,NUAA,PHM2010,刀具磨损,多材料,TC4,HRC52,数据集概述",
                },
                doc_id=doc_id,
                entities=overview_entities,
            )

        logger.info("Built uniwear dataset overview: %s", doc_id)
        return doc_id

    def build_nuaa_experiment_knowledge(self) -> list[str]:
        doc_ids: list[str] = []
        experiments = self.loader.get_experiment_tags(UniwearDataset.NUAA)

        total = len(experiments)
        for idx, exp in enumerate(experiments):
            self._notify("nuaa_experiments", idx + 1, total)

            df = self.loader.load_dataset(UniwearDataset.NUAA)
            exp_df = df[df["experiment_tag"] == exp]

            if exp_df.empty:
                continue

            stats = self.loader.compute_statistics(UniwearDataset.NUAA, experiment_tag=exp)
            self.loader.get_wear_data(UniwearDataset.NUAA, experiment_tag=exp)

            lines: list[str] = []
            lines.append(f"【NUAA 钛合金TC4 实验报告】{exp}")
            lines.append(f"材料：{NUAA_MATERIAL_FULL}")
            lines.append("数据集来源：NUAA 正交切削高分辨率束")
            lines.append(f"实验编号：{exp}（共9组：W1-W9）")
            lines.append(f"样本数量：{len(exp_df):,} 行")
            lines.append("")
            lines.append("## 加工参数")
            for meta_col in ["feed_per_tooth", "spindle_speed", "axial_cutting_depth"]:
                if meta_col in exp_df.columns:
                    vals = exp_df[meta_col].dropna().values
                    if len(vals) > 0:
                        lines.append(f"- {meta_col}：{float(vals[0]):.4f}")
            lines.append("")
            lines.append("## 信号特征统计")
            for col_name, col_stats in stats.get("signal_stats", {}).items():
                lines.append(
                    f"- {col_name}：RMS={col_stats['rms']:.4f}，"
                    f"范围 [{col_stats['min']:.4f}, {col_stats['max']:.4f}]，"
                    f"均值 {col_stats['mean']:.4f}，标准差 {col_stats['std']:.4f}"
                )
            lines.append("")
            lines.append("## 刀具磨损数据")
            ws = stats.get("wear_stats", {})
            if ws:
                lines.append(f"- 初始磨损量：{ws.get('initial_wear', 'N/A')} mm")
                lines.append(f"- 最终磨损量：{ws.get('final_wear', 'N/A')} mm")
                lines.append(f"- 最大磨损量：{ws.get('max_wear', 'N/A')} mm")
                lines.append(f"- 磨损增量：{ws.get('total_wear_increment', 'N/A')} mm")
                lines.append(f"- 平均磨损率：{ws.get('mean_wear_rate', 'N/A')} mm/样本")
                lines.append(f"- 数据点数：{ws.get('sample_count', 'N/A')}")
            lines.append("")
            lines.append("## 工艺建议（TC4钛合金）")
            lines.append("- 钛合金TC4导热性差，建议使用充足冷却液降低切削温度")
            lines.append("- 推荐使用硬质合金涂层刀具（TiAlN涂层），切削速度80-120 m/min")
            lines.append("- 进给量建议0.05-0.15 mm/tooth，背吃刀量1-3 mm")
            lines.append("- 钛合金易产生加工硬化，需保持稳定切削避免刀具振动")
            lines.append(f"- 基于本实验数据，平均磨损率为 {ws.get('mean_wear_rate', 'N/A')} mm/样本")

            doc_id = f"uniwear_nuaa_{exp}"
            document = "\n".join(lines)
            # 结构化实体：实验编号 + 材料 + 数据集 + 信号类型 + 工艺
            nuaa_entities = [
                "nuaa",
                "uniwear",
                NUAA_MATERIAL.lower(),
                NUAA_MATERIAL_FULL.lower(),
                "tc4",
                "钛合金",
                "ti-6al-4v",
                exp.lower(),
                PROCESS_CN_MAP.get(exp, "").lower(),
                "正交切削",
                "刀具磨损",
                "振动",
                "切削力",
                "主轴功率",
                "force",
                "vibration",
                "power",
            ]
            for attempt in range(3):
                try:
                    self.kb.add_knowledge(
                        document=document,
                        metadata={
                            "source": "uniwear-nuaa",
                            "type": "experiment_report",
                            "category": "tool_wear",
                            "material": NUAA_MATERIAL,
                            "material_full": NUAA_MATERIAL_FULL,
                            "experiment": exp,
                            "dataset": "nuaa",
                            "signal_type": "force/vibration/power",
                            "has_vibration": True,
                            "keywords": f"NUAA,TC4,钛合金,{exp},正交切削,刀具磨损,振动,切削力,主轴功率",
                        },
                        doc_id=doc_id if attempt == 0 else f"{doc_id}_{attempt}",
                        entities=nuaa_entities,
                    )
                    doc_ids.append(doc_id if attempt == 0 else f"{doc_id}_{attempt}")
                    break
                except (ValueError, RuntimeError, ConnectionError, OSError) as kb_err:
                    # 知识库单次写入失败，最多重试 2 次后跳过该实验
                    if attempt == 2:
                        logger.warning(
                            "Failed to add NUAA knowledge entry for %s after retries: %s",
                            exp,
                            kb_err,
                            exc_info=True,
                        )
                    continue

        logger.info("Built NUAA experiment knowledge: %d entries", len(doc_ids))
        return doc_ids

    def build_phm2010_experiment_knowledge(self) -> list[str]:
        doc_ids: list[str] = []
        experiments = self.loader.get_experiment_tags(UniwearDataset.PHM2010)

        total = len(experiments)
        for idx, exp in enumerate(experiments):
            self._notify("phm2010_experiments", idx + 1, total)

            df = self.loader.load_dataset(UniwearDataset.PHM2010)
            exp_df = df[df["experiment_tag"] == exp]

            if exp_df.empty:
                continue

            stats = self.loader.compute_statistics(UniwearDataset.PHM2010, experiment_tag=exp)

            lines: list[str] = []
            lines.append(f"【PHM2010 不锈钢HRC52 实验报告】{exp}")
            lines.append(f"材料：{PHM2010_MATERIAL_FULL}")
            lines.append("数据集来源：PHM2010 刀具磨损竞赛束数据")
            lines.append(f"实验编号：{exp}（共3组：c1, c4, c6）")
            lines.append(f"样本数量：{len(exp_df):,} 行")
            lines.append("")
            lines.append("## 信号特征统计")
            for col_name, col_stats in stats.get("signal_stats", {}).items():
                lines.append(
                    f"- {col_name}：RMS={col_stats['rms']:.4f}，"
                    f"范围 [{col_stats['min']:.4f}, {col_stats['max']:.4f}]，"
                    f"均值 {col_stats['mean']:.4f}，标准差 {col_stats['std']:.4f}"
                )
            lines.append("")
            lines.append("## 刀具磨损数据")
            ws = stats.get("wear_stats", {})
            if ws:
                lines.append(f"- 初始磨损量：{ws.get('initial_wear', 'N/A')} mm")
                lines.append(f"- 最终磨损量：{ws.get('final_wear', 'N/A')} mm")
                lines.append(f"- 最大磨损量：{ws.get('max_wear', 'N/A')} mm")
                lines.append(f"- 磨损增量：{ws.get('total_wear_increment', 'N/A')} mm")
                lines.append(f"- 平均磨损率：{ws.get('mean_wear_rate', 'N/A')} mm/样本")
                lines.append(f"- 数据点数：{ws.get('sample_count', 'N/A')}")
            lines.append("")
            lines.append("## 工艺建议（不锈钢HRC52）")
            lines.append("- HRC52高硬度不锈钢加工难度大，推荐使用CBN或涂层硬质合金刀具")
            lines.append("- 切削速度建议60-100 m/min，进给量0.05-0.12 mm/r")
            lines.append("- 不锈钢加工易产生积屑瘤，需保持充分冷却和润滑")
            lines.append("- 注意监控切削力和振动信号，及时发现异常磨损")
            lines.append(f"- 基于本实验数据，平均磨损率为 {ws.get('mean_wear_rate', 'N/A')} mm/样本")

            doc_id = f"uniwear_phm2010_{exp}"
            document = "\n".join(lines)
            # 结构化实体：实验编号 + 材料 + 数据集 + 信号类型 + 工艺
            phm_entities = [
                "phm2010",
                "uniwear",
                PHM2010_MATERIAL.lower(),
                PHM2010_MATERIAL_FULL.lower(),
                "hrc52",
                "不锈钢",
                exp.lower(),
                PROCESS_CN_MAP.get(exp, "").lower(),
                "全寿命切削",
                "刀具磨损",
                "振动",
                "切削力",
                "声发射",
                "force",
                "vibration",
                "acoustic",
            ]
            for attempt in range(3):
                try:
                    self.kb.add_knowledge(
                        document=document,
                        metadata={
                            "source": "uniwear-phm2010",
                            "type": "experiment_report",
                            "category": "tool_wear",
                            "material": PHM2010_MATERIAL,
                            "material_full": PHM2010_MATERIAL_FULL,
                            "experiment": exp,
                            "dataset": "phm2010",
                            "signal_type": "force/vibration/acoustic_emission",
                            "has_vibration": True,
                            "has_acoustic_emission": True,
                            "keywords": f"PHM2010,HRC52,不锈钢,{exp},刀具磨损,振动,切削力,声发射",
                        },
                        doc_id=doc_id if attempt == 0 else f"{doc_id}_{attempt}",
                        entities=phm_entities,
                    )
                    doc_ids.append(doc_id if attempt == 0 else f"{doc_id}_{attempt}")
                    break
                except (ValueError, RuntimeError, ConnectionError, OSError) as kb_err:
                    # 知识库单次写入失败，最多重试 2 次后跳过该实验
                    if attempt == 2:
                        logger.warning(
                            "Failed to add PHM2010 knowledge entry for %s after retries: %s",
                            exp,
                            kb_err,
                            exc_info=True,
                        )
                    continue

        logger.info("Built PHM2010 experiment knowledge: %d entries", len(doc_ids))
        return doc_ids

    def build_material_comparison(self) -> str:
        self.loader.get_material_comparison()

        lines: list[str] = []
        lines.append("【TC4钛合金 vs HRC52不锈钢 刀具磨损对比分析】")
        lines.append("")
        lines.append("## TC4（钛合金 Ti-6Al-4V）")
        lines.append("来源：NUAA 正交切削数据集")
        lines.append("典型特征：")
        lines.append("- 钛合金导热系数低（约7 W/m·K），切削热集中在刀尖区域")
        lines.append("- 高温化学活性强，易与刀具材料发生反应")
        lines.append("- 弹性模量低（110 GPa），易产生回弹和振动")
        lines.append("- 切屑呈锯齿状，切削力波动大")
        lines.append("")

        lines.append("## HRC52（硬化不锈钢）")
        lines.append("来源：PHM2010 刀具磨损数据集")
        lines.append("典型特征：")
        lines.append("- 高硬度（HRC52）导致切削力大、刀具磨损快")
        lines.append("- 加工硬化倾向强，切削层金属变形大")
        lines.append("- 切屑不易折断，需有效断屑措施")
        lines.append("- 对刀具涂层要求高，推荐TiAlN或AlCrN涂层")
        lines.append("")

        lines.append("## 振动-磨损关联分析")
        lines.append("- TC4钛合金：振动RMS值对刀具磨损的灵敏度高于不锈钢")
        lines.append("  （因钛合金低弹性模量导致振动更易传导）")
        lines.append("- HRC52不锈钢：切削力信号对磨损的响应更直接")
        lines.append("  （因高硬度导致切削力基数大、变化明显）")
        lines.append("- 声发射信号（仅PHM2010提供）对初期磨损更敏感")
        lines.append("")
        lines.append("## 跨材料迁移建议")
        lines.append("- TC4模型可用于预测类似钛合金（TC6、TC11等）的刀具磨损")
        lines.append("- HRC52模型可用于淬硬钢（HRC45-55）加工场景的参数推荐")
        lines.append("- 两材料物理特性差异大，直接迁移需谨慎，建议用少量目标数据微调")

        doc_id = "uniwear_material_comparison"
        document = "\n".join(lines)
        # 结构化实体：两材料 + 信号类型 + 对比维度
        comparison_entities = [
            "tc4",
            "hrc52",
            "钛合金",
            "不锈钢",
            "ti-6al-4v",
            NUAA_MATERIAL.lower(),
            PHM2010_MATERIAL.lower(),
            "nuaa",
            "phm2010",
            "uniwear",
            "材料对比",
            "磨损特征",
            "振动分析",
            "跨材料迁移",
            "振动",
            "切削力",
            "声发射",
            "弹性模量",
            "导热系数",
        ]
        for attempt in range(3):
            try:
                self.kb.add_knowledge(
                    document=document,
                    metadata={
                        "source": "uniwear",
                        "type": "material_comparison",
                        "category": "tool_wear",
                        "materials": f"{NUAA_MATERIAL},{PHM2010_MATERIAL}",
                        "materials_full": f"{NUAA_MATERIAL_FULL},{PHM2010_MATERIAL_FULL}",
                        "keywords": "TC4,HRC52,钛合金,不锈钢,材料对比,磨损特征,振动分析,跨材料迁移",
                    },
                    doc_id=doc_id,
                    entities=comparison_entities,
                )
                break
            except (ValueError, RuntimeError, ConnectionError, OSError) as kb_err:
                # 知识库单条写入失败时，使用 UUID 后缀重试以避免 doc_id 冲突
                logger.debug(
                    "Failed to add material comparison (attempt=%d), retrying with uuid suffix: %s",
                    attempt,
                    kb_err,
                    exc_info=True,
                )
                doc_id = f"{doc_id}_{uuid.uuid4().hex[:8]}"

        logger.info("Built material comparison: %s", doc_id)
        return doc_id

    def build_vibration_wear_correlation(self) -> str:
        lines: list[str] = []
        lines.append("【振动信号与刀具磨损关联性分析】")
        lines.append("基于 Uniwear 数据集多材料实验数据的综合分析：")
        lines.append("")
        lines.append("## 振动信号特征与磨损状态映射")
        lines.append("")
        lines.append("### 初期磨损阶段（VB < 0.05mm）")
        lines.append("- 振动RMS轻微波动，各轴能量分布接近正常基线")
        lines.append("- 频域无明显偏移，主频保持稳定")
        lines.append("- 此阶段磨损缓慢，振动监测灵敏度较低")
        lines.append("")
        lines.append("### 稳态磨损阶段（0.05mm ≤ VB < 0.2mm）")
        lines.append("- 振动RMS线性缓慢增长（增速约 5-15%/周期）")
        lines.append("- 高频成分（1000Hz+）能量占比开始增加")
        lines.append("- 切削力信号RMS同步增长")
        lines.append("- 主轴功率/电流信号出现小幅上升趋势")
        lines.append("")
        lines.append("### 加速磨损阶段（VB ≥ 0.2mm）")
        lines.append("- 振动RMS快速上升（增速超过 30%/周期）")
        lines.append("- 频域主频发生显著偏移（>20%）")
        lines.append("- 各轴能量分布出现明显不均衡")
        lines.append("- 声发射RMS急剧增大（如有AE传感器）")
        lines.append("- 此时建议立即更换刀具")
        lines.append("")
        lines.append("## 信号优先级排序（按灵敏度）")
        lines.append("1. 声发射RMS（如有）—— 对微裂纹和初期磨损最敏感")
        lines.append("2. 高频振动能量占比 —— 反映颤振和异常切削状态")
        lines.append("3. 振动RMS —— 通用性强，适用于连续在线监测")
        lines.append("4. 切削力 —— 直接反映切削条件变化")
        lines.append("5. 主轴功率/电流 —— 灵敏度较低，但数据获取最方便")
        lines.append("")
        lines.append("## 多源信号融合建议")
        lines.append("- 推荐使用振动RMS + 切削力RMS 双通道融合监测")
        lines.append("- 若有AE传感器，可增加声发射RMS作为早期预警通道")
        lines.append("- 主轴功率适合作为辅助验证通道")

        doc_id = "uniwear_vibration_wear_correlation"
        document = "\n".join(lines)
        # 结构化实体：信号类型 + 磨损阶段 + 监测策略
        vib_entities = [
            "振动",
            "vibration",
            "rms",
            "声发射",
            "acoustic",
            "频域",
            "频谱",
            "切削力",
            "主轴功率",
            "振动磨损",
            "磨损关联",
            "信号分析",
            "监测策略",
            "初期磨损",
            "稳态磨损",
            "加速磨损",
            "多源融合",
            "uniwear",
            "刀具磨损",
        ]
        for attempt in range(3):
            try:
                self.kb.add_knowledge(
                    document=document,
                    metadata={
                        "source": "uniwear",
                        "type": "analysis_report",
                        "category": "tool_wear",
                        "subcategory": "signal_analysis",
                        "signal_type": "vibration",
                        "has_vibration": True,
                        "has_acoustic_emission": True,
                        "keywords": "振动,磨损关联,信号分析,RMS,频域,声发射,多源融合,监测策略",
                    },
                    doc_id=doc_id,
                    entities=vib_entities,
                )
                break
            except (ValueError, RuntimeError, ConnectionError, OSError) as kb_err:
                # 知识库单条写入失败时，使用 UUID 后缀重试以避免 doc_id 冲突
                logger.debug(
                    "Failed to add vibration-wear correlation (attempt=%d), retrying: %s",
                    attempt,
                    kb_err,
                    exc_info=True,
                )
                doc_id = f"{doc_id}_{uuid.uuid4().hex[:8]}"

        logger.info("Built vibration-wear correlation: %s", doc_id)
        return doc_id

    def build_cross_source_comparison(self) -> str:
        lines: list[str] = []
        lines.append("【Bosch CNC 与 Uniwear 多源数据对比分析】")
        lines.append("")
        lines.append("## 数据源对比")
        lines.append("| 维度 | Bosch CNC | Uniwear-NUAA | Uniwear-PHM2010 |")
        lines.append("|------|-----------|--------------|-----------------|")
        lines.append("| 数据类型 | 工业现场振动 | 实验室切削实验 | 竞赛标准数据 |")
        lines.append("| 工件材料 | 多种（铸铁/钢） | TC4钛合金 | HRC52不锈钢 |")
        lines.append("| 工艺类型 | 铣削多工序 | 正交切削 | 全寿命切削 |")
        lines.append("| 采样频率 | 2000 Hz | 高分辨率 | 高分辨率 |")
        lines.append("| 标签 | good/bad | 连续磨损量 | 连续磨损量 |")
        lines.append("| 信号通道 | 3轴振动 | 力/振动/功率 | 力/振动/声发射 |")
        lines.append("")
        lines.append("## 互补性分析")
        lines.append("- Bosch提供真实工业生产条件下的振动模式参考")
        lines.append("- Uniwear提供实验室可控条件下的精确磨损量化数据")
        lines.append("- 联合标定可建立「振动特征 → 磨损量」的映射关系")
        lines.append("")
        lines.append("## 验证策略")
        lines.append("1. 使用Uniwear数据训练磨损量预测模型")
        lines.append("2. 将模型应用于Bosch振动特征，输出预测磨损状态")
        lines.append("3. 与Bosch的good/bad标签进行交叉验证")
        lines.append("4. 调整阈值使预测结果与现场数据一致")

        doc_id = "cross_source_bosch_uniwear"
        document = "\n".join(lines)
        # 结构化实体：所有数据源 + 材料 + 信号类型 + 验证策略
        cross_entities = [
            "bosch",
            "bosch_cnc",
            "nuaa",
            "phm2010",
            "uniwear",
            "tc4",
            "hrc52",
            "钛合金",
            "不锈钢",
            "铸铁",
            "振动",
            "切削力",
            "声发射",
            "主轴功率",
            "多源对比",
            "交叉验证",
            "振动分析",
            "磨损预测",
            "联合标定",
            "铣削",
            "正交切削",
            "全寿命切削",
        ]
        for attempt in range(3):
            try:
                self.kb.add_knowledge(
                    document=document,
                    metadata={
                        "source": "cross_source",
                        "type": "comparison_analysis",
                        "category": "cross_validation",
                        "sources": "bosch_cnc,uniwear-nuaa,uniwear-phm2010",
                        "keywords": "Bosch,Uniwear,多源对比,交叉验证,振动分析,磨损预测,联合标定",
                    },
                    doc_id=doc_id,
                    entities=cross_entities,
                )
                break
            except (ValueError, RuntimeError, ConnectionError, OSError) as kb_err:
                # 知识库单条写入失败时，使用 UUID 后缀重试以避免 doc_id 冲突
                logger.debug(
                    "Failed to add cross-source comparison (attempt=%d), retrying: %s",
                    attempt,
                    kb_err,
                    exc_info=True,
                )
                doc_id = f"{doc_id}_{uuid.uuid4().hex[:8]}"

        logger.info("Built cross-source comparison: %s", doc_id)
        return doc_id

    def build_all(self) -> dict:
        counts: dict[str, int] = {}

        overview_id = self.build_dataset_overview()
        counts["overview"] = 1 if overview_id else 0

        nuaa_ids = self.build_nuaa_experiment_knowledge()
        counts["nuaa_experiments"] = len(nuaa_ids)

        phm2010_ids = self.build_phm2010_experiment_knowledge()
        counts["phm2010_experiments"] = len(phm2010_ids)

        mat_id = self.build_material_comparison()
        counts["material_comparison"] = 1 if mat_id else 0

        vib_id = self.build_vibration_wear_correlation()
        counts["vibration_wear_correlation"] = 1 if vib_id else 0

        cross_id = self.build_cross_source_comparison()
        counts["cross_source"] = 1 if cross_id else 0

        total = sum(counts.values())
        logger.info(
            "Uniwear knowledge build complete: %d total entries (%s)",
            total,
            counts,
        )
        return {"total_entries": total, "by_type": counts}
