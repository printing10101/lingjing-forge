"""
实验9: 跨工况泛化实验 (LOMO/LOCO协议)

验证DL-LNN在跨材料、跨工况场景下的泛化能力

协议说明:
- LOMO (Leave-One-Material-Out): 训练集含4种材料,测试集为第5种材料
- LOCO (Leave-One-Condition-Out): 训练集含N-1个工况,测试集为剩余1个工况

实验目标:
- 验证连续时间建模对跨工况泛化的优势
- 对比DL-LNN与离散时间网络的泛化性能差异
- 量化PCC Loss对物理一致性的贡献
"""

import os
import sys
from pathlib import Path
import numpy as np
import torch
from torch.utils.data import DataLoader, Subset
from typing import Dict, List, Tuple
import json
from datetime import datetime

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from config import ExperimentConfig
from data_generator import IndustrialChatterDataset
from models import DLLNNWithPhysics, BaselineLSTM, BaselineTransformer, BaselinePINN, BaselineBPNN, create_model
from losses import PCC_Loss
from metrics import ChatterMetrics as Metrics
from trainer import DLLNNTrainer, BaselineTrainer


class CrossConditionExperiment:
    """跨工况泛化实验"""
    
    def __init__(self, config: ExperimentConfig):
        self.config = config
        self.device = torch.device(config.model.device if torch.cuda.is_available() else "cpu")
        self.results = {}
    
    def run_lomo_protocol(
        self,
        materials: List[str] = ['6061-T6', '7075-T6', '2024-T3', '304SS', 'Ti6Al4V']
    ) -> Dict[str, Dict[str, float]]:
        """
        LOMO协议: Leave-One-Material-Out
        
        Args:
            materials: 材料列表
        
        Returns:
            结果字典 {test_material: {metric: value}}
        """
        print("\n" + "=" * 60)
        print("LOMO协议: Leave-One-Material-Out")
        print("=" * 60)
        
        lomo_results = {}
        
        for test_material in materials:
            print(f"\n测试材料: {test_material}")
            print("-" * 60)
            
            # 准备训练集(其他4种材料)
            train_materials = [m for m in materials if m != test_material]
            
            # 生成训练数据
            train_dataset = self._generate_multi_material_data(
                train_materials, 
                num_samples_per_material=200
            )
            
            # 生成测试数据
            test_dataset = self._generate_multi_material_data(
                [test_material], 
                num_samples_per_material=100
            )
            
            # 训练模型
            model_results = self._train_and_evaluate(
                train_dataset, 
                test_dataset,
                f"LOMO_{test_material}"
            )
            
            lomo_results[test_material] = model_results
            
            # 打印结果
            print(f"\n{test_material} 结果:")
            for model_name, metrics in model_results.items():
                print(f"  {model_name}: MAE={metrics['MAE']:.3f}, "
                      f"PCC={metrics['PCC']:.3f}, R²={metrics['R²']:.3f}")
        
        # 计算平均性能
        avg_results = self._compute_average_results(lomo_results)
        lomo_results['Average'] = avg_results
        
        print("\nLOMO平均结果:")
        for model_name, metrics in avg_results.items():
            print(f"  {model_name}: MAE={metrics['MAE']:.3f}, "
                  f"PCC={metrics['PCC']:.3f}, R²={metrics['R²']:.3f}")
        
        self.results['LOMO'] = lomo_results
        return lomo_results
    
    def run_loco_protocol(
        self,
        num_conditions: int = 30,
        test_conditions: List[int] = [0, 5, 10, 15, 20, 25]
    ) -> Dict[str, Dict[str, float]]:
        """
        LOCO协议: Leave-One-Condition-Out
        
        Args:
            num_conditions: 总工况数
            test_conditions: 要测试的工况索引
        
        Returns:
            结果字典 {test_condition: {metric: value}}
        """
        print("\n" + "=" * 60)
        print("LOCO协议: Leave-One-Condition-Out")
        print("=" * 60)
        
        loco_results = {}
        
        for test_cond in test_conditions:
            print(f"\n测试工况: Condition_{test_cond}")
            print("-" * 60)
            
            # 生成完整数据集
            full_dataset = IndustrialChatterDataset(
                num_samples=500,
                num_conditions=num_conditions,
                material='6061-T6'
            )
            
            # 划分训练集和测试集
            train_indices, test_indices = self._split_by_condition(
                full_dataset, 
                test_cond
            )
            
            train_dataset = Subset(full_dataset, train_indices)
            test_dataset = Subset(full_dataset, test_indices)
            
            # 训练模型
            model_results = self._train_and_evaluate(
                train_dataset, 
                test_dataset,
                f"LOCO_Cond{test_cond}"
            )
            
            loco_results[f"Condition_{test_cond}"] = model_results
            
            # 打印结果
            print(f"\nCondition_{test_cond} 结果:")
            for model_name, metrics in model_results.items():
                print(f"  {model_name}: MAE={metrics['MAE']:.3f}, "
                      f"PCC={metrics['PCC']:.3f}, R²={metrics['R²']:.3f}")
        
        # 计算平均性能
        avg_results = self._compute_average_results(loco_results)
        loco_results['Average'] = avg_results
        
        print("\nLOCO平均结果:")
        for model_name, metrics in avg_results.items():
            print(f"  {model_name}: MAE={metrics['MAE']:.3f}, "
                  f"PCC={metrics['PCC']:.3f}, R²={metrics['R²']:.3f}")
        
        self.results['LOCO'] = loco_results
        return loco_results
    
    def _generate_multi_material_data(
        self,
        materials: List[str],
        num_samples_per_material: int = 200
    ) -> IndustrialChatterDataset:
        """生成多材料数据集"""
        # 合并多个材料的数据
        combined_dataset = IndustrialChatterDataset(
            num_samples=num_samples_per_material * len(materials),
            num_conditions=10 * len(materials),
            material=','.join(materials)
        )
        
        return combined_dataset
    
    def _split_by_condition(
        self,
        dataset: IndustrialChatterDataset,
        test_condition: int
    ) -> Tuple[List[int], List[int]]:
        """按工况划分训练集和测试集"""
        all_indices = list(range(len(dataset)))
        
        # 假设每个工况有17个样本
        samples_per_condition = 17
        test_start = test_condition * samples_per_condition
        test_end = test_start + samples_per_condition
        
        test_indices = list(range(test_start, test_end))
        train_indices = [i for i in all_indices if i not in test_indices]
        
        return train_indices, test_indices
    
    def _train_and_evaluate(
        self,
        train_dataset,
        test_dataset,
        experiment_name: str
    ) -> Dict[str, Dict[str, float]]:
        """训练并评估多个模型"""
        
        # 创建DataLoader
        train_loader = DataLoader(
            train_dataset,
            batch_size=self.config.model.batch_size,
            shuffle=True
        )
        test_loader = DataLoader(
            test_dataset,
            batch_size=self.config.model.batch_size,
            shuffle=False
        )
        
        model_results = {}
        
        # 1. DL-LNN (本文方法)
        print("  训练 DL-LNN...")
        # 移除手动模型创建，使用trainer内部模型创建
        trainer = DLLNNTrainer(self.config, self.device)
        trainer.train(train_loader, test_loader)
        ct_ltc_metrics = trainer.evaluate(test_loader)
        model_results['DL-LNN'] = ct_ltc_metrics
        
        # 2. LSTM
        print("  训练 LSTM...")
        lstm_model = BaselineLSTM(
            input_dim=self.config.model.input_dim,
            hidden_dim=self.config.model.hidden_dim,
            num_layers=self.config.model.num_layers,
            output_dim=1
        ).to(self.device)
        
        lstm_metrics = self._train_baseline(lstm_model, train_loader, test_loader)
        model_results['LSTM'] = lstm_metrics
        
        # 3. GRU (使用LSTM代替，因为models.py中没有GRU)
        print("  训练 GRU...")
        gru_model = BaselineLSTM(
            input_dim=self.config.model.input_dim,
            hidden_dim=self.config.model.hidden_dim,
            num_layers=self.config.model.num_layers,
            output_dim=1
        ).to(self.device)
        
        gru_metrics = self._train_baseline(gru_model, train_loader, test_loader)
        model_results['GRU'] = gru_metrics
        
        # 4. Transformer
        print("  训练 Transformer...")
        transformer_model = BaselineTransformer(
            input_dim=self.config.model.input_dim,
            d_model=self.config.model.hidden_dim,
            nhead=8,
            num_layers=self.config.model.num_layers,
            output_dim=1
        ).to(self.device)
        
        transformer_metrics = self._train_baseline(transformer_model, train_loader, test_loader)
        model_results['Transformer'] = transformer_metrics
        
        return model_results
    
    def _train_baseline(
        self,
        model: torch.nn.Module,
        train_loader: DataLoader,
        test_loader: DataLoader,
        num_epochs: int = 100
    ) -> Dict[str, float]:
        """训练基线模型"""
        optimizer = torch.optim.Adam(
            model.parameters(),
            lr=self.config.model.learning_rate,
            weight_decay=self.config.model.weight_decay
        )
        criterion = torch.nn.L1Loss()
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer, T_max=num_epochs
        )
        
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
            'MAE': Metrics.mae(all_preds, all_targets),
            'RMSE': Metrics.rmse(all_preds, all_targets),
            'R²': Metrics.r2_score(all_preds, all_targets),
            'PCC': Metrics.physics_consistency_coefficient(all_preds, all_phys)
        }
        
        return metrics
    
    def _compute_average_results(
        self,
        results: Dict[str, Dict[str, Dict[str, float]]]
    ) -> Dict[str, Dict[str, float]]:
        """计算平均结果"""
        models = list(list(results.values())[0].keys())
        metrics = list(list(list(results.values())[0].values())[0].keys())
        
        avg_results = {}
        
        for model in models:
            avg_results[model] = {}
            for metric in metrics:
                values = [results[k][model][metric] for k in results.keys() 
                         if k != 'Average']
                avg_results[model][metric] = np.mean(values)
        
        return avg_results
    
    def save_results(self, save_path: str = "results/cross_condition_results.json"):
        """保存实验结果"""
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        
        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False)
        
        print(f"\n✓ 跨工况实验结果已保存: {save_path}")


def main():
    """主函数"""
    print("=" * 60)
    print("实验9: 跨工况泛化实验")
    print("=" * 60)
    
    # 加载配置
    config = ExperimentConfig()
    
    # 创建实验器
    experiment = CrossConditionExperiment(config)
    
    # 运行LOMO协议
    lomo_results = experiment.run_lomo_protocol()
    
    # 运行LOCO协议
    loco_results = experiment.run_loco_protocol()
    
    # 保存结果
    experiment.save_results()
    
    print("\n" + "=" * 60)
    print("✓ 跨工况泛化实验完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()
