"""
生成更多高级可视化图表
包括：跨条件泛化对比、数据集性能分析、模型排名、误差分布等
"""
import json
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

# 目录
results_dir = Path(__file__).resolve().parent.parent / "results"
figures_dir = results_dir / "figures"
figures_dir.mkdir(exist_ok=True, parents=True)

print("=" * 80)
print("生成高级可视化图表")
print("=" * 80)

# 加载数据
with open(results_dir / "main_comparison_results.json", 'r', encoding='utf-8') as f:
    main_results = json.load(f)

with open(results_dir / "cross_condition_results.json", 'r', encoding='utf-8') as f:
    cross_results = json.load(f)

with open(results_dir / "ablation_results.json", 'r', encoding='utf-8') as f:
    ablation_data = json.load(f)['ablation']

with open(results_dir / "active_learning_results.json", 'r', encoding='utf-8') as f:
    al_data = json.load(f)

with open(results_dir / "time_constant_analysis.json", 'r', encoding='utf-8') as f:
    tc_data = json.load(f)

# 图1: 跨条件泛化性能对比（LOMO vs LOCO）
print("\n[1/7] 生成跨条件泛化性能对比图...")

fig, axes = plt.subplots(2, 2, figsize=(14, 12))

# 提取LOMO和LOCO平均性能
lomo_avg = cross_results['LOMO']['Average']
loco_avg = cross_results['LOCO']['Average']

models = list(lomo_avg.keys())
metrics = ['MAE', 'RMSE', 'PCC']

x = np.arange(len(models))
width = 0.35

# 子图1: LOMO vs LOCO - MAE对比
ax = axes[0, 0]
lomo_maes = [lomo_avg[m]['MAE'] for m in models]
loco_maes = [loco_avg[m]['MAE'] for m in models]
bars1 = ax.bar(x - width/2, lomo_maes, width, label='LOMO (Leave-One-Material-Out)', color='#FF6B6B', alpha=0.7)
bars2 = ax.bar(x + width/2, loco_maes, width, label='LOCO (Leave-One-Condition-Out)', color='#4ECDC4', alpha=0.7)
ax.set_xlabel('模型', fontsize=12)
ax.set_ylabel('MAE', fontsize=12)
ax.set_title('跨条件泛化: LOMO vs LOCO (MAE)', fontsize=14, fontweight='bold')
ax.set_xticks(x)
ax.set_xticklabels(models, rotation=15, ha='right')
ax.legend()
ax.grid(True, alpha=0.3, axis='y')

# 子图2: LOMO vs LOCO - PCC对比
ax = axes[0, 1]
lomo_pccs = [lomo_avg[m]['PCC'] for m in models]
loco_pccs = [loco_avg[m]['PCC'] for m in models]
bars1 = ax.bar(x - width/2, lomo_pccs, width, label='LOMO', color='#FF6B6B', alpha=0.7)
bars2 = ax.bar(x + width/2, loco_pccs, width, label='LOCO', color='#4ECDC4', alpha=0.7)
ax.set_xlabel('模型', fontsize=12)
ax.set_ylabel('PCC', fontsize=12)
ax.set_title('跨条件泛化: LOMO vs LOCO (PCC)', fontsize=14, fontweight='bold')
ax.set_xticks(x)
ax.set_xticklabels(models, rotation=15, ha='right')
ax.legend()
ax.grid(True, alpha=0.3, axis='y')
ax.set_ylim([0.94, 1.0])

# 子图3: LOMO各材料性能热力图
ax = axes[1, 0]
materials = [k for k in cross_results['LOMO'].keys() if k != 'Average']
heatmap_data = []
for material in materials:
    row = [cross_results['LOMO'][material][model]['MAE'] for model in models]
    heatmap_data.append(row)

im = ax.imshow(heatmap_data, cmap='YlOrRd', aspect='auto')
ax.set_xticks(np.arange(len(models)))
ax.set_yticks(np.arange(len(materials)))
ax.set_xticklabels(models, rotation=15, ha='right')
ax.set_yticklabels(materials)
ax.set_xlabel('模型', fontsize=12)
ax.set_ylabel('材料', fontsize=12)
ax.set_title('LOMO: 各材料×模型 MAE热力图', fontsize=14, fontweight='bold')
plt.colorbar(im, ax=ax, label='MAE')

# 子图4: LOCO各条件性能热力图
ax = axes[1, 1]
conditions = [k for k in cross_results['LOCO'].keys() if k != 'Average']
heatmap_data = []
for condition in conditions:
    row = [cross_results['LOCO'][condition][model]['MAE'] for model in models]
    heatmap_data.append(row)

im = ax.imshow(heatmap_data, cmap='YlOrRd', aspect='auto')
ax.set_xticks(np.arange(len(models)))
ax.set_yticks(np.arange(len(conditions)))
ax.set_xticklabels(models, rotation=15, ha='right')
ax.set_yticklabels([f'条件{i}' for i in range(len(conditions))])
ax.set_xlabel('模型', fontsize=12)
ax.set_ylabel('工况条件', fontsize=12)
ax.set_title('LOCO: 各工况×模型 MAE热力图', fontsize=14, fontweight='bold')
plt.colorbar(im, ax=ax, label='MAE')

plt.tight_layout()
plt.savefig(figures_dir / "cross_condition_comparison.png", dpi=300, bbox_inches='tight')
plt.close()
print("  ✓ 已保存: cross_condition_comparison.png")

# 图2: 多数据集性能对比图
print("\n[2/7] 生成多数据集性能对比图...")

fig, axes = plt.subplots(2, 2, figsize=(14, 12))

datasets = [k for k in main_results.keys() if not k.startswith('_')]
models_all = list(main_results[datasets[0]].keys())

# 子图1: 各数据集上DL-LNN的MAE
ax = axes[0, 0]
ct_ltc_maes = [main_results[ds]['DL-LNN']['MAE'] for ds in datasets]
colors_ds = plt.cm.Set2(np.linspace(0, 1, len(datasets)))
bars = ax.bar(range(len(datasets)), ct_ltc_maes, color=colors_ds, alpha=0.7, edgecolor='black')
ax.set_xlabel('数据集', fontsize=12)
ax.set_ylabel('MAE', fontsize=12)
ax.set_title('DL-LNN在各数据集上的MAE', fontsize=14, fontweight='bold')
ax.set_xticks(range(len(datasets)))
ax.set_xticklabels(datasets, rotation=15, ha='right')
ax.grid(True, alpha=0.3, axis='y')
for bar, val in zip(bars, ct_ltc_maes):
    ax.text(bar.get_x(), bar.get_height() + 0.02, f'{val:.3f}', 
            ha='center', va='bottom', fontsize=10)

# 子图2: 各数据集上模型排名（按MAE）
ax = axes[0, 1]
ranking_data = {m: [] for m in models_all}
for ds in datasets:
    # 按MAE排序
    sorted_models = sorted(models_all, key=lambda m: main_results[ds][m]['MAE'])
    for rank, model in enumerate(sorted_models, 1):
        ranking_data[model].append(rank)

# 计算平均排名
avg_ranks = {m: np.mean(ranks) for m, ranks in ranking_data.items()}
sorted_models_by_rank = sorted(avg_ranks.items(), key=lambda x: x[1])
model_names_sorted = [m[0] for m in sorted_models_by_rank]
avg_ranks_sorted = [m[1] for m in sorted_models_by_rank]

colors_rank = plt.cm.viridis(np.linspace(0, 1, len(model_names_sorted)))
bars = ax.barh(range(len(model_names_sorted)), avg_ranks_sorted, color=colors_rank, alpha=0.7, edgecolor='black')
ax.set_xlabel('平均排名', fontsize=12)
ax.set_ylabel('模型', fontsize=12)
ax.set_title('跨数据集模型平均排名 (MAE)', fontsize=14, fontweight='bold')
ax.set_yticks(range(len(model_names_sorted)))
ax.set_yticklabels(model_names_sorted)
ax.invert_yaxis()
ax.grid(True, alpha=0.3, axis='x')
for bar, val in zip(bars, avg_ranks_sorted):
    ax.text(bar.get_width() + 0.1, bar.get_y(), f'{val:.1f}', 
            ha='left', va='center', fontsize=10)

# 子图3: 各数据集PCC对比
ax = axes[1, 0]
x = np.arange(len(datasets))
width = 0.15
selected_models = ['DL-LNN', 'LSTM', 'GRU', 'Transformer', 'PINN']
colors_sel = plt.cm.Set1(np.linspace(0, 1, len(selected_models)))

for i, model in enumerate(selected_models):
    pccs = [main_results[ds][model]['PCC'] for ds in datasets]
    ax.bar(x + i*width, pccs, width, label=model, color=colors_sel[i], alpha=0.7)

ax.set_xlabel('数据集', fontsize=12)
ax.set_ylabel('PCC', fontsize=12)
ax.set_title('主要模型在各数据集上的PCC', fontsize=14, fontweight='bold')
ax.set_xticks(x + width*2)
ax.set_xticklabels(datasets, rotation=15, ha='right')
ax.legend(loc='lower right', fontsize=9)
ax.grid(True, alpha=0.3, axis='y')
ax.set_ylim([0.95, 1.0])

# 子图4: 数据集难度分析（所有模型平均MAE）
ax = axes[1, 1]
dataset_difficulty = []
for ds in datasets:
    avg_mae = np.mean([main_results[ds][m]['MAE'] for m in models_all])
    dataset_difficulty.append(avg_mae)

bars = ax.bar(range(len(datasets)), dataset_difficulty, color='coral', alpha=0.7, edgecolor='black')
ax.set_xlabel('数据集', fontsize=12)
ax.set_ylabel('平均MAE', fontsize=12)
ax.set_title('数据集难度分析 (所有模型平均MAE)', fontsize=14, fontweight='bold')
ax.set_xticks(range(len(datasets)))
ax.set_xticklabels(datasets, rotation=15, ha='right')
ax.grid(True, alpha=0.3, axis='y')
for bar, val in zip(bars, dataset_difficulty):
    ax.text(bar.get_x(), bar.get_height() + 0.02, f'{val:.3f}', 
            ha='center', va='bottom', fontsize=10)

plt.tight_layout()
plt.savefig(figures_dir / "multi_dataset_analysis.png", dpi=300, bbox_inches='tight')
plt.close()
print("  ✓ 已保存: multi_dataset_analysis.png")

# 图3: 模型综合性能评分图
print("\n[3/7] 生成模型综合性能评分图...")

fig, axes = plt.subplots(1, 2, figsize=(16, 7))

# 子图1: 综合评分（基于4个指标的加权平均）
ax = axes[0]
# 在自采6061-T6数据集上评估
dataset_eval = "自采6061-T6"
models_eval = list(main_results[dataset_eval].keys())

# 计算综合评分（归一化后加权平均）
scores = []
for model in models_eval:
    mae = main_results[dataset_eval][model]['MAE']
    rmse = main_results[dataset_eval][model]['RMSE']
    r2 = main_results[dataset_eval][model]['R2']
    pcc = main_results[dataset_eval][model]['PCC']
    
    # 归一化到0-100分
    mae_score = max(0, 100 - mae * 50)  # MAE越小越好
    rmse_score = max(0, 100 - rmse * 40)  # RMSE越小越好
    r2_score = max(0, (r2 + 1) * 100)  # R²越大越好
    pcc_score = pcc * 100  # PCC越大越好
    
    # 加权平均
    total_score = mae_score * 0.25 + rmse_score * 0.25 + r2_score * 0.25 + pcc_score * 0.25
    scores.append(total_score)

sorted_indices = np.argsort(scores)[::-1]
sorted_models = [models_eval[i] for i in sorted_indices]
sorted_scores = [scores[i] for i in sorted_indices]

colors_score = plt.cm.RdYlGn(np.linspace(0.2, 0.9, len(sorted_models)))
bars = ax.barh(range(len(sorted_models)), sorted_scores, color=colors_score, alpha=0.7, edgecolor='black')
ax.set_xlabel('综合评分', fontsize=12)
ax.set_ylabel('模型', fontsize=12)
ax.set_title(f'模型综合性能评分 ({dataset_eval})', fontsize=14, fontweight='bold')
ax.set_yticks(range(len(sorted_models)))
ax.set_yticklabels(sorted_models)
ax.invert_yaxis()
ax.grid(True, alpha=0.3, axis='x')
for bar, val in zip(bars, sorted_scores):
    ax.text(bar.get_width() + 0.5, bar.get_y(), f'{val:.1f}', 
            ha='left', va='center', fontsize=10, fontweight='bold')

# 子图2: 各指标最优模型统计
ax = axes[1]
best_counts = {m: 0 for m in models_eval}
for ds in datasets:
    # 每个指标找最优模型
    for metric in ['MAE', 'RMSE', 'PCC']:
        if metric in ['MAE', 'RMSE']:
            best_model = min(models_eval, key=lambda m: main_results[ds][m][metric])
        else:
            best_model = max(models_eval, key=lambda m: main_results[ds][m][metric])
        best_counts[best_model] += 1

sorted_counts = sorted(best_counts.items(), key=lambda x: x[1], reverse=True)
model_names_counts = [x[0] for x in sorted_counts]
counts = [x[1] for x in sorted_counts]

colors_count = plt.cm.Set3(np.linspace(0, 1, len(model_names_counts)))
bars = ax.bar(range(len(model_names_counts)), counts, color=colors_count, alpha=0.7, edgecolor='black')
ax.set_xlabel('模型', fontsize=12)
ax.set_ylabel('最优指标次数', fontsize=12)
ax.set_title('各模型在所有数据集上获得最优指标的次数', fontsize=14, fontweight='bold')
ax.set_xticks(range(len(model_names_counts)))
ax.set_xticklabels(model_names_counts, rotation=15, ha='right')
ax.grid(True, alpha=0.3, axis='y')
for bar, val in zip(bars, counts):
    ax.text(bar.get_x(), bar.get_height() + 0.1, f'{val}', 
            ha='center', va='bottom', fontsize=11, fontweight='bold')

plt.tight_layout()
plt.savefig(figures_dir / "model_scoring_analysis.png", dpi=300, bbox_inches='tight')
plt.close()
print("  ✓ 已保存: model_scoring_analysis.png")

# 图4: 误差分布箱线图
print("\n[4/7] 生成误差分布箱线图...")

fig, axes = plt.subplots(1, 2, figsize=(14, 6))

# 子图1: 各模型MAE分布（跨所有数据集）
ax = axes[0]
mae_by_model = []
for model in models_all:
    maes = [main_results[ds][model]['MAE'] for ds in datasets]
    mae_by_model.append(maes)

bp = ax.boxplot(mae_by_model, labels=models_all, patch_artist=True)
colors_box = plt.cm.Pastel1(np.linspace(0, 1, len(models_all)))
for patch, color in zip(bp['boxes'], colors_box):
    patch.set_facecolor(color)
    patch.set_alpha(0.7)

ax.set_xlabel('模型', fontsize=12)
ax.set_ylabel('MAE', fontsize=12)
ax.set_title('各模型MAE分布（跨5个数据集）', fontsize=14, fontweight='bold')
ax.set_xticklabels(models_all, rotation=15, ha='right')
ax.grid(True, alpha=0.3, axis='y')

# 子图2: 各模型PCC分布（跨所有数据集）
ax = axes[1]
pcc_by_model = []
for model in models_all:
    pccs = [main_results[ds][model]['PCC'] for ds in datasets]
    pcc_by_model.append(pccs)

bp = ax.boxplot(pcc_by_model, labels=models_all, patch_artist=True)
for patch, color in zip(bp['boxes'], colors_box):
    patch.set_facecolor(color)
    patch.set_alpha(0.7)

ax.set_xlabel('模型', fontsize=12)
ax.set_ylabel('PCC', fontsize=12)
ax.set_title('各模型PCC分布（跨5个数据集）', fontsize=14, fontweight='bold')
ax.set_xticklabels(models_all, rotation=15, ha='right')
ax.grid(True, alpha=0.3, axis='y')

plt.tight_layout()
plt.savefig(figures_dir / "error_distribution_boxplot.png", dpi=300, bbox_inches='tight')
plt.close()
print("  ✓ 已保存: error_distribution_boxplot.png")

# 图5: 时间常数层级差异分析
print("\n[5/7] 生成时间常数层级差异分析图...")

fig, axes = plt.subplots(2, 2, figsize=(14, 12))

# 子图1: 各层τ分布小提琴图
ax = axes[0, 0]
layer_taus = [layer['tau_values'] for layer in tc_data['layers']]
parts = ax.violinplot(layer_taus, showmeans=False, showmedians=False)
for pc, color in zip(parts['bodies'], ['#FF6B6B', '#4ECDC4', '#45B7D1']):
    pc.set_facecolor(color)
    pc.set_alpha(0.7)
    pc.set_edgecolor('black')

ax.set_xticks([1, 2, 3])
ax.set_xticklabels(['第1层', '第2层', '第3层'])
ax.set_xlabel('LTC网络层', fontsize=12)
ax.set_ylabel('时间常数 τ', fontsize=12)
ax.set_title('各层τ分布小提琴图', fontsize=14, fontweight='bold')
ax.grid(True, alpha=0.3, axis='y')

# 子图2: 各层τ随网络深度变化
ax = axes[0, 1]
layer_means = [layer['tau_mean'] for layer in tc_data['layers']]
layer_stds = [layer['tau_std'] for layer in tc_data['layers']]
layers = [1, 2, 3]

ax.plot(layers, layer_means, 'o-', color='#FF6B6B', linewidth=2, markersize=10, label='均值')
ax.fill_between(layers, 
                [m - s for m, s in zip(layer_means, layer_stds)],
                [m + s for m, s in zip(layer_means, layer_stds)],
                alpha=0.3, color='#FF6B6B', label='±1标准差')

ax.set_xlabel('网络层', fontsize=12)
ax.set_ylabel('时间常数 τ', fontsize=12)
ax.set_title('τ随网络深度变化趋势', fontsize=14, fontweight='bold')
ax.set_xticks(layers)
ax.legend()
ax.grid(True, alpha=0.3)

# 子图3: 各层τ范围对比
ax = axes[1, 0]
layer_mins = [layer['tau_min'] for layer in tc_data['layers']]
layer_maxs = [layer['tau_max'] for layer in tc_data['layers']]
layer_medians = [layer['tau_median'] for layer in tc_data['layers']]

x = np.arange(3)
width = 0.25
bars1 = ax.bar(x - width, layer_mins, width, label='最小值', color='#4ECDC4', alpha=0.7)
bars2 = ax.bar(x, layer_medians, width, label='中位数', color='#FFEAA7', alpha=0.7)
bars3 = ax.bar(x + width, layer_maxs, width, label='最大值', color='#FF6B6B', alpha=0.7)

ax.set_xlabel('LTC网络层', fontsize=12)
ax.set_ylabel('时间常数 τ', fontsize=12)
ax.set_title('各层τ统计特征对比', fontsize=14, fontweight='bold')
ax.set_xticks(x)
ax.set_xticklabels(['第1层', '第2层', '第3层'])
ax.legend()
ax.grid(True, alpha=0.3, axis='y')

# 子图4: τ变异系数分析
ax = axes[1, 1]
cv_values = [layer['tau_std'] / layer['tau_mean'] for layer in tc_data['layers']]
bars = ax.bar([1, 2, 3], cv_values, color=['#FF6B6B', '#4ECDC4', '#45B7D1'], alpha=0.7, edgecolor='black')
ax.set_xlabel('LTC网络层', fontsize=12)
ax.set_ylabel('变异系数 (σ/μ)', fontsize=12)
ax.set_title('各层τ变异系数分析', fontsize=14, fontweight='bold')
ax.set_xticks([1, 2, 3])
ax.set_xticklabels(['第1层', '第2层', '第3层'])
ax.grid(True, alpha=0.3, axis='y')
for bar, val in zip(bars, cv_values):
    ax.text(bar.get_x(), bar.get_height() + 0.005, f'{val:.3f}', 
            ha='center', va='bottom', fontsize=11, fontweight='bold')

plt.tight_layout()
plt.savefig(figures_dir / "time_constant_layer_analysis.png", dpi=300, bbox_inches='tight')
plt.close()
print("  ✓ 已保存: time_constant_layer_analysis.png")

# 图6: 主动学习数据效率分析
print("\n[6/7] 生成主动学习数据效率分析图...")

fig, axes = plt.subplots(2, 2, figsize=(14, 12))

al_results = al_data['active_learning']
random_baseline = al_data['random_baseline']

data_ratios = [x['data_ratio'] for x in al_results]
al_maes = [x['MAE'] for x in al_results]
al_r2s = [x['R2'] for x in al_results]
rand_maes = [x['MAE'] for x in random_baseline]
rand_r2s = [x['R2'] for x in random_baseline]

# 子图1: 学习曲线对比
ax = axes[0, 0]
ax.plot(data_ratios, al_maes, 'o-', color='#FF6B6B', linewidth=2.5, markersize=10, label='DL-LNN 主动学习')
ax.plot(data_ratios, rand_maes, 's--', color='#4ECDC4', linewidth=2.5, markersize=10, label='随机采样')
ax.set_xlabel('标注数据比例', fontsize=12)
ax.set_ylabel('MAE', fontsize=12)
ax.set_title('学习曲线: MAE vs 数据量', fontsize=14, fontweight='bold')
ax.legend(fontsize=11)
ax.grid(True, alpha=0.3)
ax.set_xticks(data_ratios)
ax.set_xticklabels([f'{int(r*100)}%' for r in data_ratios])

# 子图2: 相对改进率
ax = axes[0, 1]
improvement = [(rand - al) / rand * 100 for al, rand in zip(al_maes, rand_maes)]
bars = ax.bar(data_ratios, improvement, color='#96CEB4', alpha=0.7, edgecolor='black')
ax.axhline(y=0, color='gray', linestyle='--', linewidth=1)
ax.set_xlabel('标注数据比例', fontsize=12)
ax.set_ylabel('MAE改进率 (%)', fontsize=12)
ax.set_title('主动学习相对随机采样的改进率', fontsize=14, fontweight='bold')
ax.set_xticks(data_ratios)
ax.set_xticklabels([f'{int(r*100)}%' for r in data_ratios])
ax.grid(True, alpha=0.3, axis='y')
for bar, val in zip(bars, improvement):
    if abs(val) > 5:  # 只显示显著改进
        ax.text(bar.get_x(), bar.get_height() + 1, f'{val:.1f}%', 
                ha='center', va='bottom', fontsize=9)

# 子图3: R²对比
ax = axes[1, 0]
ax.plot(data_ratios, al_r2s, 'o-', color='#FF6B6B', linewidth=2.5, markersize=10, label='DL-LNN 主动学习')
ax.plot(data_ratios, rand_r2s, 's--', color='#4ECDC4', linewidth=2.5, markersize=10, label='随机采样')
ax.axhline(y=0, color='gray', linestyle=':', linewidth=1, alpha=0.5)
ax.set_xlabel('标注数据比例', fontsize=12)
ax.set_ylabel('R²', fontsize=12)
ax.set_title('学习曲线: R² vs 数据量', fontsize=14, fontweight='bold')
ax.legend(fontsize=11)
ax.grid(True, alpha=0.3)
ax.set_xticks(data_ratios)
ax.set_xticklabels([f'{int(r*100)}%' for r in data_ratios])

# 子图4: 数据效率柱状图
ax = axes[1, 1]
# 找到达到MAE<1.0所需的数据量
target_mae = 1.0
al_efficiency = None
rand_efficiency = None
for i, mae in enumerate(al_maes):
    if mae < target_mae:
        al_efficiency = data_ratios[i] * 100
        break
for i, mae in enumerate(rand_maes):
    if mae < target_mae:
        rand_efficiency = data_ratios[i] * 100
        break

efficiency_data = []
labels = []
if al_efficiency:
    efficiency_data.append(al_efficiency)
    labels.append(f'DL-LNN\n主动学习\n({al_efficiency:.0f}%)')
if rand_efficiency:
    efficiency_data.append(rand_efficiency)
    labels.append(f'随机采样\n({rand_efficiency:.0f}%)')

bars = ax.bar(range(len(efficiency_data)), efficiency_data, 
              color=['#FF6B6B', '#4ECDC4'], alpha=0.7, edgecolor='black')
ax.set_xlabel('采样策略', fontsize=12)
ax.set_ylabel('达到MAE<1.0所需数据比例 (%)', fontsize=12)
ax.set_title('数据效率对比', fontsize=14, fontweight='bold')
ax.set_xticks(range(len(efficiency_data)))
ax.set_xticklabels(labels)
ax.grid(True, alpha=0.3, axis='y')
for bar, val in zip(bars, efficiency_data):
    ax.text(bar.get_x(), bar.get_height() + 1, f'{val:.0f}%', 
            ha='center', va='bottom', fontsize=12, fontweight='bold')

plt.tight_layout()
plt.savefig(figures_dir / "active_learning_efficiency.png", dpi=300, bbox_inches='tight')
plt.close()
print("  ✓ 已保存: active_learning_efficiency.png")

# 图7: 消融实验详细分析
print("\n[7/7] 生成消融实验详细分析图...")

fig, axes = plt.subplots(2, 2, figsize=(14, 12))

variants = list(ablation_data.keys())
metrics_abl = ['MAE', 'RMSE', 'R²', 'PCC']

# 子图1: 各变体性能对比（多指标）
ax = axes[0, 0]
x = np.arange(len(variants))
width = 0.2

for i, metric in enumerate(['MAE', 'RMSE', 'PCC']):
    values = [ablation_data[v][metric] for v in variants]
    ax.bar(x + i*width, values, width, label=metric, alpha=0.7)

ax.set_xlabel('模型变体', fontsize=12)
ax.set_ylabel('指标值', fontsize=12)
ax.set_title('消融实验: 多指标对比', fontsize=14, fontweight='bold')
ax.set_xticks(x + width)
ax.set_xticklabels(variants, rotation=15, ha='right')
ax.legend()
ax.grid(True, alpha=0.3, axis='y')

# 子图2: 相对Full Model的性能变化
ax = axes[0, 1]
full_model_mae = ablation_data['Full Model']['MAE']
mae_changes = []
for v in variants:
    change = (ablation_data[v]['MAE'] - full_model_mae) / full_model_mae * 100
    mae_changes.append(change)

colors_change = ['#4CAF50' if c < 0 else '#FF5252' for c in mae_changes]
bars = ax.bar(range(len(variants)), mae_changes, color=colors_change, alpha=0.7, edgecolor='black')
ax.axhline(y=0, color='black', linestyle='-', linewidth=1)
ax.set_xlabel('模型变体', fontsize=12)
ax.set_ylabel('MAE变化 (%)', fontsize=12)
ax.set_title('消融实验: 相对Full Model的MAE变化', fontsize=14, fontweight='bold')
ax.set_xticks(range(len(variants)))
ax.set_xticklabels(variants, rotation=15, ha='right')
ax.grid(True, alpha=0.3, axis='y')
for bar, val in zip(bars, mae_changes):
    ax.text(bar.get_x(), bar.get_height() + 0.05 if val >= 0 else bar.get_height() - 0.15,
            f'{val:+.2f}%', ha='center', va='bottom' if val >= 0 else 'top', 
            fontsize=10, fontweight='bold')

# 子图3: 各变体PCC对比
ax = axes[1, 0]
pcc_values = [ablation_data[v]['PCC'] for v in variants]
bars = ax.bar(range(len(variants)), pcc_values, color='#45B7D1', alpha=0.7, edgecolor='black')
ax.set_xlabel('模型变体', fontsize=12)
ax.set_ylabel('PCC', fontsize=12)
ax.set_title('消融实验: PCC对比', fontsize=14, fontweight='bold')
ax.set_xticks(range(len(variants)))
ax.set_xticklabels(variants, rotation=15, ha='right')
ax.grid(True, alpha=0.3, axis='y')
ax.set_ylim([0.99, 1.0])
for bar, val in zip(bars, pcc_values):
    ax.text(bar.get_x(), bar.get_height() + 0.0005, f'{val:.4f}', 
            ha='center', va='bottom', fontsize=9)

# 子图4: 组件重要性排序
ax = axes[1, 1]
# 计算每个组件移除后的性能下降
component_impact = {}
for v in variants:
    if v != 'Full Model':
        impact = ablation_data[v]['MAE'] - full_model_mae
        component_name = v.replace('w/o ', '').replace('LTC → ', '')
        component_impact[component_name] = impact

sorted_components = sorted(component_impact.items(), key=lambda x: x[1], reverse=True)
comp_names = [x[0] for x in sorted_components]
comp_impacts = [x[1] for x in sorted_components]

colors_imp = plt.cm.RdYlGn_r(np.linspace(0.2, 0.8, len(comp_names)))
bars = ax.barh(range(len(comp_names)), comp_impacts, color=colors_imp, alpha=0.7, edgecolor='black')
ax.set_xlabel('MAE增加量', fontsize=12)
ax.set_ylabel('移除的组件', fontsize=12)
ax.set_title('组件重要性排序 (移除后MAE增加)', fontsize=14, fontweight='bold')
ax.set_yticks(range(len(comp_names)))
ax.set_yticklabels(comp_names)
ax.grid(True, alpha=0.3, axis='x')
for bar, val in zip(bars, comp_impacts):
    ax.text(bar.get_width() + 0.0005, bar.get_y(), f'{val:.4f}', 
            ha='left', va='center', fontsize=10)

plt.tight_layout()
plt.savefig(figures_dir / "ablation_detailed_analysis.png", dpi=300, bbox_inches='tight')
plt.close()
print("  ✓ 已保存: ablation_detailed_analysis.png")

print("\n" + "=" * 80)
print(f"所有图表已保存到: {figures_dir}")
print("=" * 80)
print("\n新生成的图表列表:")
for fig_file in sorted(figures_dir.glob("*.png")):
    if fig_file.name not in ['ablation_comparison.png', 'ablation_study.png', 
                              'active_learning_curves.png', 'loco_heatmap.png',
                              'lomo_heatmap.png', 'main_results_industrial.png',
                              'main_results_synthetic.png', 'model_radar_chart.png',
                              'prediction_scatter_residual.png', 'time_constant_distribution.png']:
        print(f"  {fig_file.name}")
