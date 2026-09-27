"""
实验10: 消融实验

验证DL-LNN各核心组件的贡献:
- PCC Loss (物理一致性损失)
- 解析预训练 (两阶段训练策略)
- LTC vs LSTM (连续时间 vs 离散时间)
- 门控融合机制

流式推理消融（lingbot-map 五大设计）:
- Paged Hidden State Cache (分页隐状态缓存)
- Keyframe Strategy (关键帧策略)
- Anchor Context (锚点漂移修正)
- Trajectory Memory (轨迹记忆)
- Windowed Inference (窗口化推理)

实验目标:
- 量化每个组件对最终性能的贡献
- 验证论文核心创新点的有效性
- 为论文消融实验章节提供数据支撑
"""

import os
import sys
import time
from pathlib import Path
import numpy as np
import torch
from torch.utils.data import DataLoader
from typing import Dict, List, Optional
import json

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))
# 添加项目根目录（python/）到 path，用于导入 app 模块
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from training.reproducibility import set_global_seed
from training.experiment_tracker import start_run, is_enabled

from config import ExperimentConfig
from data_generator import SyntheticChatterDataset, IndustrialChatterDataset
from models import DLLNNWithPhysics, DLLNNModel, BaselineLSTM, BaselineBPNN, create_model
from losses import PCC_Loss
from metrics import ChatterMetrics as Metrics
from trainer import DLLNNTrainer, BaselineTrainer


class AblationExperiment:
    """消融实验"""

    def __init__(self, config: ExperimentConfig):
        self.config = config
        self.device = torch.device(config.model.device if torch.cuda.is_available() else "cpu")
        self.results = {}

    def run_ablation_study(self, dataset_class, dataset_params: Dict) -> Dict[str, Dict[str, float]]:
        """
        运行消融实验

        实验变体:
        1. Full Model (完整DL-LNN)
        2. w/o PCC Loss (移除物理一致性损失)
        3. w/o Pre-train (移除解析预训练)
        4. LTC → LSTM (替换为离散时间网络)
        5. w/o Gate (移除门控融合)

        Args:
            dataset_class: 数据集类
            dataset_params: 数据集参数

        Returns:
            结果字典 {variant_name: {metric: value}}
        """
        print("\n" + "=" * 60)
        print("消融实验: 核心组件贡献分析")
        print("=" * 60)

        # 准备数据
        train_dataset = dataset_class(**dataset_params)
        test_dataset = dataset_class(**{**dataset_params, "seed": 123})

        train_loader = DataLoader(train_dataset, batch_size=self.config.model.batch_size, shuffle=True)
        test_loader = DataLoader(test_dataset, batch_size=self.config.model.batch_size, shuffle=False)

        ablation_results = {}

        # 1. Full Model (完整DL-LNN)
        print("\n[1/5] Full Model (DL-LNN + PCC Loss + Pre-train + Gate)")
        print("-" * 60)
        # 移除手动模型创建，使用trainer内部模型创建
        trainer = DLLNNTrainer(self.config, self.device)
        trainer.train(train_loader, test_loader)
        full_metrics = trainer.evaluate(test_loader)
        ablation_results["Full Model"] = full_metrics

        print(f"  MAE: {full_metrics['MAE']:.3f}, PCC: {full_metrics['PCC']:.3f}, R²: {full_metrics['R²']:.3f}")

        # 2. w/o PCC Loss (移除物理一致性损失)
        print("\n[2/5] w/o PCC Loss (仅使用数据损失)")
        print("-" * 60)
        # 修改配置,禁用PCC Loss
        config_no_pcc = ExperimentConfig()
        config_no_pcc.model.lambda_pcc = 0.0
        config_no_pcc.model.lambda_phys = 0.0

        trainer_no_pcc = DLLNNTrainer(config_no_pcc, self.device)
        trainer_no_pcc.train(train_loader, test_loader)
        no_pcc_metrics = trainer_no_pcc.evaluate(test_loader)
        ablation_results["w/o PCC Loss"] = no_pcc_metrics

        print(f"  MAE: {no_pcc_metrics['MAE']:.3f}, PCC: {no_pcc_metrics['PCC']:.3f}, R²: {no_pcc_metrics['R²']:.3f}")

        # 3. w/o Pre-train (移除解析预训练)
        print("\n[3/5] w/o Pre-train (直接从零训练)")
        print("-" * 60)
        model_no_pretrain = DLLNNWithPhysics(
            input_dim=self.config.model.input_dim,
            hidden_dim=self.config.model.hidden_dim,
            num_layers=self.config.model.num_layers,
            dropout=self.config.model.dropout,
        ).to(self.device)

        config_no_pretrain = ExperimentConfig()
        config_no_pretrain.model.num_epochs_stage1 = 0  # 跳过预训练

        trainer_no_pretrain = DLLNNTrainer(config_no_pretrain, self.device)
        trainer_no_pretrain.train(train_loader, test_loader)
        no_pretrain_metrics = trainer_no_pretrain.evaluate(test_loader)
        ablation_results["w/o Pre-train"] = no_pretrain_metrics

        print(
            f"  MAE: {no_pretrain_metrics['MAE']:.3f}, "
            f"PCC: {no_pretrain_metrics['PCC']:.3f}, "
            f"R²: {no_pretrain_metrics['R²']:.3f}"
        )

        # 4. LTC LSTM (替换为离散时间网络)
        print("\n[4/5] LTC → LSTM (离散时间网络)")
        print("-" * 60)
        lstm_model = BaselineLSTM(
            input_dim=self.config.model.input_dim,
            hidden_dim=self.config.model.hidden_dim,
            num_layers=self.config.model.num_layers,
            output_dim=1,
        ).to(self.device)

        lstm_metrics = self._train_baseline(lstm_model, train_loader, test_loader)
        ablation_results["LTC → LSTM"] = lstm_metrics

        print(f"  MAE: {lstm_metrics['MAE']:.3f}, PCC: {lstm_metrics['PCC']:.3f}, R²: {lstm_metrics['R²']:.3f}")

        # 5. w/o Gate (移除门控融合,直接相加)
        print("\n[5/5] w/o Gate (移除门控融合机制)")
        print("-" * 60)
        # 简化处理：使用标准LTC模型代替（无门控融合）
        model_no_gate = DLLNNModel(
            input_dim=self.config.model.input_dim,
            hidden_dim=self.config.model.hidden_dim,
            num_layers=self.config.model.num_layers,
            output_dim=1,
            dropout=self.config.model.dropout,
        ).to(self.device)

        no_gate_metrics = self._train_baseline(model_no_gate, train_loader, test_loader)
        ablation_results["w/o Gate"] = no_gate_metrics

        print(
            f"  MAE: {no_gate_metrics['MAE']:.3f}, PCC: {no_gate_metrics['PCC']:.3f}, R²: {no_gate_metrics['R²']:.3f}"
        )

        # 计算相对改进
        print("\n" + "=" * 60)
        print("消融实验结果汇总")
        print("=" * 60)

        baseline_mae = ablation_results["Full Model"]["MAE"]

        for variant, metrics in ablation_results.items():
            if variant != "Full Model":
                degradation = (metrics["MAE"] - baseline_mae) / baseline_mae * 100
                print(f"{variant:20s}: MAE={metrics['MAE']:.3f} (+{degradation:.1f}% vs Full)")

        self.results["ablation"] = ablation_results
        return ablation_results

    def _train_baseline(
        self, model: torch.nn.Module, train_loader: DataLoader, test_loader: DataLoader, num_epochs: int = 200
    ) -> Dict[str, float]:
        """训练基线模型"""
        optimizer = torch.optim.Adam(
            model.parameters(), lr=self.config.model.learning_rate, weight_decay=self.config.model.weight_decay
        )
        criterion = torch.nn.L1Loss()
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=num_epochs)

        # 训练
        model.train()
        for epoch in range(num_epochs):
            train_loss = 0.0
            for batch_x, batch_y, batch_y_phys in train_loader:
                batch_x = batch_x.to(self.device)
                batch_y = batch_y.to(self.device)

                optimizer.zero_grad()
                outputs = model(batch_x)
                loss = criterion(outputs, batch_y)
                loss.backward()
                optimizer.step()

                train_loss += loss.item()

            scheduler.step()

        # 评估
        model.eval()
        all_preds = []
        all_targets = []
        all_phys = []

        with torch.no_grad():
            for batch_x, batch_y, batch_y_phys in test_loader:
                batch_x = batch_x.to(self.device)
                outputs = model(batch_x)

                all_preds.extend(outputs.cpu().numpy())
                all_targets.extend(batch_y.numpy())
                all_phys.extend(batch_y_phys.numpy())

        all_preds = np.array(all_preds)
        all_targets = np.array(all_targets)
        all_phys = np.array(all_phys)

        # 计算指标
        metrics = {
            "MAE": Metrics.mae(all_preds, all_targets),
            "RMSE": Metrics.rmse(all_preds, all_targets),
            "R²": Metrics.r2_score(all_preds, all_targets),
            "PCC": Metrics.physics_consistency_coefficient(all_preds, all_phys),
        }

        return metrics

    # 流式推理消融实验（lingbot-map 五大设计）

    def run_streaming_ablation(
        self,
        dataset_class,
        dataset_params: Dict,
        stream_size: int = 500,
    ) -> Dict[str, Dict[str, float]]:
        """
        流式推理消融实验 —— 验证 lingbot-map 五大设计对长时序推理的贡献.

        实验变体:
        1. Full Streaming (五大设计全部启用)
        2. w/o Paged Cache (max_cache_pages=1，等价于无分页)
        3. w/o Keyframe Strategy (每帧都关键帧，无选择性记忆)
        4. w/o Anchor Context (禁用锚点漂移修正)
        5. w/o Trajectory Memory (禁用轨迹一致性约束)
        6. w/o Windowed Inference (禁用窗口化，单流处理)

        实验流程:
        1. 训练一个基础 DL-LNN 模型（复用 Full Model 训练流程）
        2. 构造长时序测试流（stream_size 帧，模拟连续加工）
        3. 对每个变体实例化五大组件，逐帧推理
        4. 收集预测质量指标 (MAE/RMSE/R²/PCC) + 运营指标 (keyframe_ratio 等)

        指标说明
        --------
        - Anchor/Trajectory 直接修正预测值 → 体现在 MAE/R²/PCC
        - Cache/Keyframe/Window 影响状态管理 → 体现在运营指标
          (keyframe_ratio, cache_evictions, avg_inference_ms)

        Args:
            dataset_class: 数据集类
            dataset_params: 数据集参数
            stream_size: 流式测试帧数（建议 >= 500 以体现长时序效应）

        Returns:
            结果字典 {variant_name: {metric: value}}
        """
        # 软依赖导入流式推理组件（避免 app 模块缺失时阻塞整个实验）
        try:
            from app.ai.lnn.inference.streaming import (
                StreamingConfig,
                PagedHiddenStateCache,
                KeyframeSelector,
                AnchorContext,
                TrajectoryMemory,
            )
        except ImportError as exc:
            print(f"[Stream] 跳过流式消融：无法导入流式组件 ({exc})")
            return {}

        print("\n" + "=" * 60)
        print("流式推理消融实验: lingbot-map 五大设计贡献分析")
        print("=" * 60)

        # 1. 训练基础模型（复用 Full Model 配置）
        print("\n[Stream] 训练基础 DL-LNN 模型...")
        train_dataset = dataset_class(**dataset_params)
        test_dataset = dataset_class(**{**dataset_params, "seed": 123})

        train_loader = DataLoader(
            train_dataset,
            batch_size=self.config.model.batch_size,
            shuffle=True,
        )
        test_loader = DataLoader(
            test_dataset,
            batch_size=self.config.model.batch_size,
            shuffle=False,
        )

        trainer = DLLNNTrainer(self.config, self.device)
        trainer.train(train_loader, test_loader)
        model = trainer.model
        model.eval()

        # 2. 构造长时序测试流
        stream_dataset = dataset_class(
            **{
                **dataset_params,
                "num_samples": stream_size,
                "seed": 2024,
            }
        )
        stream_loader = DataLoader(stream_dataset, batch_size=1, shuffle=False)

        stream_features: List[np.ndarray] = []
        stream_targets: List[np.ndarray] = []
        stream_phys: List[np.ndarray] = []
        for batch_x, batch_y, batch_y_phys in stream_loader:
            stream_features.append(batch_x.squeeze(0).numpy())
            stream_targets.append(batch_y.squeeze(0).numpy().ravel())
            stream_phys.append(batch_y_phys.squeeze(0).numpy().ravel())

        print(f"[Stream] 流式测试集: {len(stream_features)} 帧")

        # 3. 定义消融变体配置
        variants: Dict[str, StreamingConfig] = {
            "Full Streaming": StreamingConfig(
                keyframe_interval=2,
                keyframe_mode="hybrid",
                max_cache_pages=320,
                anchor_enabled=True,
                trajectory_memory_size=64,
                window_size=64,
                overlap_keyframes=8,
            ),
            "w/o Paged Cache": StreamingConfig(
                keyframe_interval=2,
                keyframe_mode="hybrid",
                max_cache_pages=1,  # 仅 1 页，立即淘汰，等价于无分页
                anchor_enabled=True,
                trajectory_memory_size=64,
                window_size=64,
                overlap_keyframes=8,
            ),
            "w/o Keyframe Strategy": StreamingConfig(
                keyframe_interval=1,  # 每帧都关键帧
                keyframe_mode="interval",
                max_cache_pages=320,
                anchor_enabled=True,
                trajectory_memory_size=64,
                window_size=64,
                overlap_keyframes=8,
            ),
            "w/o Anchor Context": StreamingConfig(
                keyframe_interval=2,
                keyframe_mode="hybrid",
                max_cache_pages=320,
                anchor_enabled=False,
                trajectory_memory_size=64,
                window_size=64,
                overlap_keyframes=8,
            ),
            "w/o Trajectory Memory": StreamingConfig(
                keyframe_interval=2,
                keyframe_mode="hybrid",
                max_cache_pages=320,
                anchor_enabled=True,
                trajectory_memory_size=1,  # 仅 1 帧，等价于无记忆
                trajectory_correction_strength=0.0,
                window_size=64,
                overlap_keyframes=8,
            ),
            "w/o Windowed Inference": StreamingConfig(
                keyframe_interval=2,
                keyframe_mode="hybrid",
                max_cache_pages=320,
                anchor_enabled=True,
                trajectory_memory_size=64,
                window_size=None,  # 禁用窗口化，单流处理
            ),
        }

        # 4. 逐变体运行流式推理
        ablation_results: Dict[str, Dict[str, float]] = {}
        total_variants = len(variants)
        for idx, (variant_name, cfg) in enumerate(variants.items(), 1):
            print(f"\n[Stream {idx}/{total_variants}] {variant_name}")
            print("-" * 60)
            metrics = self._run_streaming_variant(
                model=model,
                config=cfg,
                stream_features=stream_features,
                stream_targets=stream_targets,
                stream_phys=stream_phys,
            )
            ablation_results[variant_name] = metrics
            print(
                f"  MAE: {metrics['MAE']:.3f}, "
                f"PCC: {metrics['PCC']:.3f}, "
                f"R²: {metrics['R²']:.3f}, "
                f"keyframe_ratio: {metrics.get('keyframe_ratio', 0):.2%}"
            )

        # 5. 汇总
        print("\n" + "=" * 60)
        print("流式消融实验结果汇总")
        print("=" * 60)

        baseline_mae = ablation_results["Full Streaming"]["MAE"]
        for variant, metrics in ablation_results.items():
            if variant != "Full Streaming":
                degradation = (metrics["MAE"] - baseline_mae) / baseline_mae * 100 if baseline_mae > 0 else 0.0
                print(f"{variant:25s}: MAE={metrics['MAE']:.3f} (+{degradation:.1f}% vs Full)")

        self.results["streaming_ablation"] = ablation_results
        return ablation_results

    def _run_streaming_variant(
        self,
        model: torch.nn.Module,
        config,
        stream_features: List[np.ndarray],
        stream_targets: List[np.ndarray],
        stream_phys: List[np.ndarray],
    ) -> Dict[str, float]:
        """
        运行单个流式消融变体.

        实例化五大流式组件，逐帧执行推理 + 修正 + 状态管理。
        窗口化推理通过周期性重置流式状态实现（模拟窗口边界）。

        Args:
            model: 已训练的 DL-LNN 模型
            config: StreamingConfig 实例
            stream_features: 流式特征列表 [N, input_dim]
            stream_targets: 流式目标列表 [N]
            stream_phys: 流式物理目标列表 [N]

        Returns:
            包含预测质量指标和运营指标的字典
        """
        from app.ai.lnn.inference.streaming import (
            PagedHiddenStateCache,
            KeyframeSelector,
            AnchorContext,
            TrajectoryMemory,
        )

        # 初始化五大组件
        cache = PagedHiddenStateCache(
            max_pages=config.max_cache_pages,
            device="cpu",
            predictor_device=self.device,
        )
        keyframe_selector = KeyframeSelector(
            interval=config.keyframe_interval,
            mode=config.keyframe_mode,
            energy_threshold=config.energy_threshold,
            seed=42,
        )
        anchor = AnchorContext(
            update_rate=config.anchor_update_rate,
            correction_strength=config.anchor_correction_strength,
            enabled=config.anchor_enabled,
        )
        trajectory = TrajectoryMemory(
            window_size=config.trajectory_memory_size,
            correction_strength=config.trajectory_correction_strength,
        )

        all_preds: List[np.ndarray] = []
        all_targets: List[np.ndarray] = []
        all_phys: List[np.ndarray] = []

        # 运营统计
        n_keyframes = 0
        n_anchor_corrections = 0
        n_trajectory_corrections = 0
        total_inference_ms = 0.0
        total_frames = 0

        ws = config.window_size
        okf = config.overlap_keyframes

        for i, feat in enumerate(stream_features):
            # 窗口边界：重置流式状态（模拟窗口化推理的边界效应）
            if ws is not None and i > 0 and i % ws == 0:
                cache.clear()
                anchor.reset()
                trajectory.reset()
                keyframe_selector.reset()

            frame_id = i + 1
            total_frames += 1

            # 基础模型前向传播
            feat_tensor = torch.from_numpy(feat).float().unsqueeze(0).to(self.device)
            t_start = time.perf_counter()
            with torch.inference_mode():
                output = model(feat_tensor)
            inference_ms = (time.perf_counter() - t_start) * 1000.0
            total_inference_ms += inference_ms

            pred = output.squeeze().cpu().numpy()
            pred_arr = np.asarray(pred, dtype=np.float64).ravel()

            # 关键帧判定
            kf_decision = keyframe_selector.decide(feat)

            # 轨迹记忆修正（直接修改预测值）
            try:
                corrected_pred, traj_dev = trajectory.correct(pred_arr)
                if traj_dev > 0:
                    n_trajectory_corrections += 1
                pred_arr = np.asarray(corrected_pred, dtype=np.float64).ravel()
            except (ValueError, TypeError):
                pass

            # 锚点修正（直接修改预测值）
            if config.anchor_enabled:
                try:
                    proxy_hidden = pred_arr.copy()
                    corrected_proxy, anchor_drift = anchor.correct(proxy_hidden)
                    if anchor_drift > 0:
                        n_anchor_corrections += 1
                    # 稳态判定：非关键帧或稳态关键帧更新锚点
                    is_stable = (not kf_decision.is_keyframe) or (kf_decision.reason in ("interval", "energy_stable"))
                    anchor.update(proxy_hidden, is_stable=is_stable)
                except (ValueError, TypeError):
                    pass

            # 关键帧写入分页缓存
            if kf_decision.is_keyframe:
                n_keyframes += 1
                try:
                    cache.put(frame_id, pred_arr.copy())
                except (ValueError, TypeError):
                    pass

            # 轨迹记录
            trajectory.push(pred_arr)

            all_preds.append(pred_arr)
            all_targets.append(stream_targets[i])
            all_phys.append(stream_phys[i])

        # 计算指标
        preds_array = np.array(all_preds)
        targets_array = np.array(all_targets)
        phys_array = np.array(all_phys)

        metrics: Dict[str, float] = {
            "MAE": float(Metrics.mae(preds_array, targets_array)),
            "RMSE": float(Metrics.rmse(preds_array, targets_array)),
            "R²": float(Metrics.r2_score(preds_array, targets_array)),
            "PCC": float(Metrics.physics_consistency_coefficient(preds_array, phys_array)),
            # 运营指标
            "keyframe_ratio": n_keyframes / total_frames if total_frames > 0 else 0.0,
            "keyframes": float(n_keyframes),
            "anchor_corrections": float(n_anchor_corrections),
            "trajectory_corrections": float(n_trajectory_corrections),
            "cache_evictions": float(cache.stats()["eviction_count"]),
            "cache_pages": float(cache.stats()["page_count"]),
            "avg_inference_ms": total_inference_ms / total_frames if total_frames > 0 else 0.0,
        }

        return metrics

    def save_results(self, save_path: str = "results/ablation_results.json"):
        """保存实验结果"""
        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False)

        print(f"\n✓ 消融实验结果已保存: {save_path}")


def main():
    """主函数"""
    print("=" * 60)
    print("实验10: 消融实验")
    print("=" * 60)

    # 加载配置
    config = ExperimentConfig()

    # 创建实验器
    experiment = AblationExperiment(config)

    # 在工业数据集上运行消融实验
    dataset_params = {"num_samples": 500, "num_conditions": 30, "material": "6061-T6"}

    # 1. 核心组件消融（PCC Loss / Pre-train / LTC / Gate）
    ablation_results = experiment.run_ablation_study(IndustrialChatterDataset, dataset_params)

    # 2. 流式推理消融（lingbot-map 五大设计）
    streaming_results = experiment.run_streaming_ablation(
        IndustrialChatterDataset,
        dataset_params,
        stream_size=500,
    )

    # 保存结果
    experiment.save_results()

    print("\n" + "=" * 60)
    print("✓ 消融实验完成!")
    print("=" * 60)


if __name__ == "__main__":
    set_global_seed(42)
    with start_run(experiment_name="exp10_ablation"):
        main()
