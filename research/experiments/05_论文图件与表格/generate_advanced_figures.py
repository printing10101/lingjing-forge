"""
生成高级可视化图表（第二批）
包括：模型排名、3D散点图、相关性热力图、稳定性分析、跨条件泛化详细对比等
"""
import json
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from scipy import stats

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

# 结果目录
results_dir = Path(__file__).resolve().parent.parent / "results"
figures_dir = results_dir / "figures"
figures_dir.mkdir(exist_ok=True, parents=True)

print("=" * 80)
print("生成高级可视化图表（第二批）")
print("=" * 80)

# 加载数据
with open(results_dir / "main_comparison_results.json", 'r', encoding='utf-8') as f:
    main_results = json.load(f)

with open(results_dir / "cross_condition_results.json", 'r', encoding='utf-8') as f:
    cross_results = json.load(f)

with open(results_dir / "ablation_results.json", 'r', encoding='utf-8') as f:
    ablation_data = json.load(f)['ablation']

with open(results_dir / "time_constant_analysis.json", 'r', encoding='utf-8') as f:
    tc_data = json.load(f)

with open(results_dir / "active_learning_results.json", 'r', encoding='utf-8') as f:
    al_data = json.load(f)

# 图1: 模型综合性能排名图
print("\n[1/8] 生成模型综合性能排名图...")

datasets = [k for k in main_results.keys() if not k.startswith('_')]
models = list(main_results[datasets[0]].keys())

# 计算每个模型在所有数据集上的平均排名
model_rankings = {model: [] for model in models}

for dataset in datasets:
    # 按MAE排序（越小越好）
    mae_values = [(model, main_results[dataset][model]['MAE']) for model in models]
    mae_values.sort(key=lambda x: x[1])
    for rank, (model, _) in enumerate(mae_values, 1):
        model_rankings[model].append(rank)

avg_rankings = {model: np.mean(ranks) for model, ranks in model_rankings.items()}
sorted_models = sorted(avg_rankings.items(), key=lambda x: x[1])

fig, ax = plt.subplots(figsize=(12, 8))
models_sorted = [m[0] for m in sorted_models]
rankings_sorted = [m[1] for m in sorted_models]

colors = plt.cm.RdYlGn_r(np.linspace(0.2, 0.8, len(models_sorted)))
bars = ax.barh(range(len(models_sorted)), rankings_sorted, color=colors, alpha=0.7, edgecolor='black')

ax.set_yticks(range(len(models_sorted)))
ax.set_yticklabels(models_sorted, fontsize=11)
ax.set_xlabel('平均排名（越低越好）', fontsize=12)
ax.set_title('模型综合性能排名（基于所有数据集MAE）', fontsize=14, fontweight='bold')
ax.grid(True, alpha=0.3, axis='x')

# 添加数值标签
for i, (bar, ranking) in enumerate(zip(bars, rankings_sorted)):
    ax.text(ranking + 0.05, i, f'{ranking:.2f}', va='center', fontsize=10, fontweight='bold')

plt.tight_layout()
plt.savefig(figures_dir / "model_ranking.png", dpi=300, bbox_inches='tight')
plt.close()
print("  ✓ 已保存: model_ranking.png")

# 图2: 3D散点图（MAE vs RMSE vs PCC）
print("\n[2/8] 生成3D散点图...")

fig = plt.figure(figsize=(16, 7))

# 子图1: 所有模型在所有数据集上的3D分布
ax1 = fig.add_subplot(121, projection='3d')
mae_all = []
rmse_all = []
pcc_all = []
model_labels = []

for ds_name, ds_data in main_results.items():
    for model_name, model_data in ds_data.items():
        mae_all.append(model_data['MAE'])
        rmse_all.append(model_data['RMSE'])
        pcc_all.append(model_data['PCC'])
        model_labels.append(model_name)

# 为不同模型分配不同颜色
unique_models = list(set(model_labels))
colors_map = {m: plt.cm.tab10(i) for i, m in enumerate(unique_models)}
colors = [colors_map[m] for m in model_labels]

scatter = ax1.scatter(mae_all, rmse_all, pcc_all, c=colors, s=60, alpha=0.7, edgecolors='black')
ax1.set_xlabel('MAE', fontsize=11)
ax1.set_ylabel('RMSE', fontsize=11)
ax1.set_zlabel('PCC', fontsize=11)
ax1.set_title('模型性能3D分布\n(MAE vs RMSE vs PCC)', fontsize=13, fontweight='bold')
ax1.view_init(elev=20, azim=45)

# 添加图例
for model in unique_models:
    idx = model_labels.index(model)
    ax1.scatter([], [], [], c=[colors[idx]], label=model, s=60, edgecolors='black')
ax1.legend(loc='upper left', fontsize=8, bbox_to_anchor=(-0.1, 1.0))

# 子图2: 时间常数3D可视化
ax2 = fig.add_subplot(122, projection='3d')
layer_taus = []
layer_indices = []

for i, layer_info in enumerate(tc_data['layers']):
    for tau in layer_info['tau_values']:
        layer_taus.append(tau)
        layer_indices.append(i + 1)

# 创建3D散点图
taus_array = np.array(layer_taus)
indices_array = np.array(layer_indices)
# 添加第三个维度：tau的排名百分位
tau_percentiles = stats.rankdata(taus_array) / len(taus_array)

scatter2 = ax2.scatter(indices_array, taus_array, tau_percentiles, 
                       c=taus_array, cmap='viridis', s=50, alpha=0.7, edgecolors='black')
ax2.set_xlabel('层级', fontsize=11)
ax2.set_ylabel('τ值', fontsize=11)
ax2.set_zlabel('百分位', fontsize=11)
ax2.set_title('时间常数τ的3D分布\n(层级 vs τ值 vs 百分位)', fontsize=13, fontweight='bold')
ax2.view_init(elev=25, azim=135)

plt.colorbar(scatter2, ax=ax2, label='τ值', shrink=0.6)

plt.tight_layout()
plt.savefig(figures_dir / "3d_scatter_plots.png", dpi=300, bbox_inches='tight')
plt.close()
print("  ✓ 已保存: 3d_scatter_plots.png")

# 图3: 指标相关性热力图
print("\n[3/8] 生成指标相关性热力图...")

# 收集所有数据点
all_metrics = {'MAE': [], 'RMSE': [], 'R2': [], 'MAPE': [], 'PCC': []}
for ds_data in main_results.values():
    for model_data in ds_data.values():
        for metric in all_metrics.keys():
            all_metrics[metric].append(model_data[metric])

# 计算相关系数矩阵
metrics_names = list(all_metrics.keys())
corr_matrix = np.zeros((len(metrics_names), len(metrics_names)))

for i, m1 in enumerate(metrics_names):
    for j, m2 in enumerate(metrics_names):
        corr, _ = stats.pearsonr(all_metrics[m1], all_metrics[m2])
        corr_matrix[i, j] = corr

fig, ax = plt.subplots(figsize=(10, 8))
im = ax.imshow(corr_matrix, cmap='RdBu_r', vmin=-1, vmax=1)

# 添加数值标签
for i in range(len(metrics_names)):
    for j in range(len(metrics_names)):
        text = ax.text(j, i, f'{corr_matrix[i, j]:.2f}',
                      ha="center", va="center", color="black" if abs(corr_matrix[i, j]) < 0.5 else "white",
                      fontsize=11, fontweight='bold')

ax.set_xticks(range(len(metrics_names)))
ax.set_yticks(range(len(metrics_names)))
ax.set_xticklabels(metrics_names, fontsize=11)
ax.set_yticklabels(metrics_names, fontsize=11)
ax.set_title('性能指标相关性热力图', fontsize=14, fontweight='bold')
ax.set_xlabel('指标', fontsize=12)
ax.set_ylabel('指标', fontsize=12)

plt.colorbar(im, ax=ax, label='Pearson相关系数')
plt.tight_layout()
plt.savefig(figures_dir / "metrics_correlation_heatmap.png", dpi=300, bbox_inches='tight')
plt.close()
print("  ✓ 已保存: metrics_correlation_heatmap.png")

# 图4: 模型稳定性分析（性能波动）
print("\n[4/8] 生成模型稳定性分析图...")

# 计算每个模型在不同数据集上的MAE标准差
model_stability = {model: [] for model in models}
for dataset in datasets:
    for model in models:
        model_stability[model].append(main_results[dataset][model]['MAE'])

stability_metrics = {
    'MAE均值': {m: np.mean(vals) for m, vals in model_stability.items()},
    'MAE标准差': {m: np.std(vals) for m, vals in model_stability.items()},
    'MAE变异系数': {m: np.std(vals) / np.mean(vals) for m, vals in model_stability.items()}
}

fig, axes = plt.subplots(1, 3, figsize=(18, 6))

# 子图1: MAE均值
ax = axes[0]
means = [stability_metrics['MAE均值'][m] for m in models]
colors_stab = plt.cm.Set2(np.linspace(0, 1, len(models)))
bars = ax.bar(range(len(models)), means, color=colors_stab, alpha=0.7, edgecolor='black')
ax.set_xticks(range(len(models)))
ax.set_xticklabels(models, rotation=45, ha='right', fontsize=10)
ax.set_ylabel('MAE均值', fontsize=11)
ax.set_title('各模型平均MAE', fontsize=13, fontweight='bold')
ax.grid(True, alpha=0.3, axis='y')
for bar, mean in zip(bars, means):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.01, 
            f'{mean:.3f}', ha='center', va='bottom', fontsize=9)

# 子图2: MAE标准差
ax = axes[1]
stds = [stability_metrics['MAE标准差'][m] for m in models]
bars = ax.bar(range(len(models)), stds, color=colors_stab, alpha=0.7, edgecolor='black')
ax.set_xticks(range(len(models)))
ax.set_xticklabels(models, rotation=45, ha='right', fontsize=10)
ax.set_ylabel('MAE标准差', fontsize=11)
ax.set_title('各模型MAE波动（标准差）', fontsize=13, fontweight='bold')
ax.grid(True, alpha=0.3, axis='y')
for bar, std in zip(bars, stds):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.001, 
            f'{std:.3f}', ha='center', va='bottom', fontsize=9)

# 子图3: 变异系数
ax = axes[2]
cvs = [stability_metrics['MAE变异系数'][m] for m in models]
bars = ax.bar(range(len(models)), cvs, color=colors_stab, alpha=0.7, edgecolor='black')
ax.set_xticks(range(len(models)))
ax.set_xticklabels(models, rotation=45, ha='right', fontsize=10)
ax.set_ylabel('变异系数', fontsize=11)
ax.set_title('各模型稳定性（变异系数，越低越稳定）', fontsize=13, fontweight='bold')
ax.grid(True, alpha=0.3, axis='y')
for bar, cv in zip(bars, cvs):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.001, 
            f'{cv:.3f}', ha='center', va='bottom', fontsize=9)

plt.tight_layout()
plt.savefig(figures_dir / "model_stability_analysis.png", dpi=300, bbox_inches='tight')
plt.close()
print("  ✓ 已保存: model_stability_analysis.png")

# 图5: 跨条件泛化详细对比（LOMO vs LOCO）
print("\n[5/8] 生成跨条件泛化详细对比图...")

fig, axes = plt.subplots(2, 2, figsize=(14, 12))

# 提取LOMO和LOCO平均性能
lomo_avg = cross_results['LOMO']['Average']
loco_avg = cross_results['LOCO']['Average']

models_cross = list(lomo_avg.keys())
metrics = ['MAE', 'RMSE', 'PCC']

x = np.arange(len(models_cross))
width = 0.35

# 子图1: LOMO vs LOCO - MAE对比
ax = axes[0, 0]
lomo_maes = [lomo_avg[m]['MAE'] for m in models_cross]
loco_maes = [loco_avg[m]['MAE'] for m in models_cross]

bars1 = ax.bar(x - width/2, lomo_maes, width, label='LOMO (Leave-One-Material-Out)', color='#FF6B6B', alpha=0.7)
bars2 = ax.bar(x + width/2, loco_maes, width, label='LOCO (Leave-One-Condition-Out)', color='#4ECDC4', alpha=0.7)

ax.set_xlabel('模型', fontsize=12)
ax.set_ylabel('MAE', fontsize=12)
ax.set_title('跨条件泛化: LOMO vs LOCO (MAE)', fontsize=14, fontweight='bold')
ax.set_xticks(x)
ax.set_xticklabels(models_cross, rotation=15, ha='right')
ax.legend()
ax.grid(True, alpha=0.3, axis='y')

# 子图2: LOMO vs LOCO - PCC对比
ax = axes[0, 1]
lomo_pccs = [lomo_avg[m]['PCC'] for m in models_cross]
loco_pccs = [loco_avg[m]['PCC'] for m in models_cross]

bars1 = ax.bar(x - width/2, lomo_pccs, width, label='LOMO', color='#FF6B6B', alpha=0.7)
bars2 = ax.bar(x + width/2, loco_pccs, width, label='LOCO', color='#4ECDC4', alpha=0.7)

ax.set_xlabel('模型', fontsize=12)
ax.set_ylabel('PCC', fontsize=12)
ax.set_title('跨条件泛化: LOMO vs LOCO (PCC)', fontsize=14, fontweight='bold')
ax.set_xticks(x)
ax.set_xticklabels(models_cross, rotation=15, ha='right')
ax.legend()
ax.grid(True, alpha=0.3, axis='y')
ax.set_ylim([0.94, 1.0])

# 子图3: 各材料上的DL-LNN性能（LOMO）
ax = axes[1, 0]
materials = [k for k in cross_results['LOMO'].keys() if k != 'Average']
ct_ltc_material_maes = [cross_results['LOMO'][mat]['DL-LNN']['MAE'] for mat in materials]

bars = ax.bar(range(len(materials)), ct_ltc_material_maes, color='#45B7D1', alpha=0.7, edgecolor='black')
ax.set_xticks(range(len(materials)))
ax.set_xticklabels(materials, rotation=45, ha='right', fontsize=10)
ax.set_ylabel('MAE', fontsize=12)
ax.set_title('DL-LNN在各材料上的泛化性能 (LOMO)', fontsize=14, fontweight='bold')
ax.grid(True, alpha=0.3, axis='y')

for bar, mae in zip(bars, ct_ltc_material_maes):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.01, 
            f'{mae:.3f}', ha='center', va='bottom', fontsize=9)

# 子图4: 各条件上的DL-LNN性能（LOCO）
ax = axes[1, 1]
conditions = [k for k in cross_results['LOCO'].keys() if k != 'Average']
ct_ltc_condition_maes = [cross_results['LOCO'][cond]['DL-LNN']['MAE'] for cond in conditions]

bars = ax.bar(range(len(conditions)), ct_ltc_condition_maes, color='#96CEB4', alpha=0.7, edgecolor='black')
ax.set_xticks(range(len(conditions)))
ax.set_xticklabels(conditions, rotation=45, ha='right', fontsize=10)
ax.set_ylabel('MAE', fontsize=12)
ax.set_title('DL-LNN在各工况上的泛化性能 (LOCO)', fontsize=14, fontweight='bold')
ax.grid(True, alpha=0.3, axis='y')

for bar, mae in zip(bars, ct_ltc_condition_maes):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.01, 
            f'{mae:.3f}', ha='center', va='bottom', fontsize=9)

plt.tight_layout()
plt.savefig(figures_dir / "cross_condition_detailed.png", dpi=300, bbox_inches='tight')
plt.close()
print("  ✓ 已保存: cross_condition_detailed.png")

# 图6: 时间常数层级差异分析
print("\n[6/8] 生成时间常数层级差异分析图...")

fig, axes = plt.subplots(2, 2, figsize=(14, 10))

layers = tc_data['layers']
layer_means = [l['tau_mean'] for l in layers]
layer_stds = [l['tau_std'] for l in layers]
layer_medians = [l['tau_median'] for l in layers]

# 子图1: 各层τ分布（小提琴图）
ax = axes[0, 0]
tau_values_by_layer = [l['tau_values'] for l in layers]
positions = range(1, len(layers) + 1)

parts = ax.violinplot(tau_values_by_layer, positions=positions)
for pc, color in zip(parts['bodies'], ['#FF6B6B', '#4ECDC4', '#45B7D1']):
    pc.set_facecolor(color)
    pc.set_alpha(0.7)
    pc.set_edgecolor('black')

ax.scatter(positions, layer_means, marker='o', s=100, c='red', zorder=3, label='均值')
ax.scatter(positions, layer_medians, marker='s', s=100, c='blue', zorder=3, label='中位数')

ax.set_xlabel('层级', fontsize=12)
ax.set_ylabel('时间常数 τ', fontsize=12)
ax.set_title('各层τ分布小提琴图', fontsize=14, fontweight='bold')
ax.set_xticks(positions)
ax.set_xticklabels([f'第{i+1}层' for i in range(len(layers))])
ax.legend()
ax.grid(True, alpha=0.3, axis='y')

# 子图2: 层级间差异显著性分析
ax = axes[0, 1]
# 进行t检验
from scipy.stats import ttest_ind
p_values = []
for i in range(len(layers)):
    for j in range(i+1, len(layers)):
        t_stat, p_val = ttest_ind(layers[i]['tau_values'], layers[j]['tau_values'])
        p_values.append((f'L{i+1}-L{j+1}', p_val))

labels = [p[0] for p in p_values]
p_vals = [p[1] for p in p_values]

colors_sig = ['#FF6B6B' if p < 0.05 else '#95A5A6' for p in p_vals]
bars = ax.bar(range(len(p_vals)), p_vals, color=colors_sig, alpha=0.7, edgecolor='black')
ax.axhline(y=0.05, color='red', linestyle='--', linewidth=2, label='显著性阈值 (p=0.05)')

ax.set_xlabel('层级对比', fontsize=12)
ax.set_ylabel('p值', fontsize=12)
ax.set_title('层级间差异显著性检验', fontsize=14, fontweight='bold')
ax.set_xticks(range(len(p_vals)))
ax.set_xticklabels(labels)
ax.legend()
ax.grid(True, alpha=0.3, axis='y')

# 子图3: τ值分布的偏度和峰度
ax = axes[1, 0]
skewness = [stats.skew(l['tau_values']) for l in layers]
kurtosis = [stats.kurtosis(l['tau_values']) for l in layers]

x = np.arange(len(layers))
width = 0.35

bars1 = ax.bar(x - width/2, skewness, width, label='偏度', color='#FF6B6B', alpha=0.7)
bars2 = ax.bar(x + width/2, kurtosis, width, label='峰度', color='#4ECDC4', alpha=0.7)

ax.set_xlabel('层级', fontsize=12)
ax.set_ylabel('统计量', fontsize=12)
ax.set_title('各层τ分布形态统计', fontsize=14, fontweight='bold')
ax.set_xticks(x)
ax.set_xticklabels([f'第{i+1}层' for i in range(len(layers))])
ax.legend()
ax.grid(True, alpha=0.3, axis='y')

# 子图4: τ值范围对比
ax = axes[1, 1]
tau_mins = [l['tau_min'] for l in layers]
tau_maxs = [l['tau_max'] for l in layers]

ax.errorbar(range(len(layers)), layer_means, yerr=layer_stds, fmt='o-', 
            capsize=5, capthick=2, linewidth=2, markersize=8, color='#45B7D1', label='均值±标准差')

for i, (mean, std, tau_min, tau_max) in enumerate(zip(layer_means, layer_stds, tau_mins, tau_maxs)):
    ax.plot([i, i], [tau_min, tau_max], 'k-', linewidth=1, alpha=0.3)
    ax.plot([i-0.05, i+0.05], [tau_min, tau_min], 'k-', linewidth=1, alpha=0.3)
    ax.plot([i-0.05, i+0.05], [tau_max, tau_max], 'k-', linewidth=1, alpha=0.3)

ax.set_xlabel('层级', fontsize=12)
ax.set_ylabel('时间常数 τ', fontsize=12)
ax.set_title('各层τ值范围（均值±标准差，最小-最大）', fontsize=14, fontweight='bold')
ax.set_xticks(range(len(layers)))
ax.set_xticklabels([f'第{i+1}层' for i in range(len(layers))])
ax.legend()
ax.grid(True, alpha=0.3, axis='y')

plt.tight_layout()
plt.savefig(figures_dir / "time_constant_layer_detailed.png", dpi=300, bbox_inches='tight')
plt.close()
print("  ✓ 已保存: time_constant_layer_detailed.png")

# 图7: 主动学习详细分析
print("\n[7/8] 生成主动学习详细分析图...")

fig, axes = plt.subplots(2, 2, figsize=(14, 10))

al_results = al_data['active_learning']
random_baseline = al_data['random_baseline']

data_ratios = [x['data_ratio'] for x in al_results]
al_maes = [x['MAE'] for x in al_results]
al_r2s = [x['R2'] for x in al_results]
al_pccs = [x['PCC'] for x in al_results]

rand_maes = [x['MAE'] for x in random_baseline]
rand_maes_std = [x['MAE_std'] for x in random_baseline]
rand_r2s = [x['R2'] for x in random_baseline]
rand_r2s_std = [x['R2_std'] for x in random_baseline]

# 子图1: MAE对比（带误差带）
ax = axes[0, 0]
ax.plot(data_ratios, al_maes, 'o-', color='#FF6B6B', linewidth=2, markersize=8, label='DL-LNN 主动学习')
ax.fill_between(data_ratios, 
                [m - s for m, s in zip(rand_maes, rand_maes_std)],
                [m + s for m, s in zip(rand_maes, rand_maes_std)],
                alpha=0.2, color='#4ECDC4')
ax.plot(data_ratios, rand_maes, 's-', color='#4ECDC4', linewidth=2, markersize=8, label='随机采样 (均值±标准差)')

ax.set_xlabel('标注数据比例', fontsize=12)
ax.set_ylabel('MAE', fontsize=12)
ax.set_title('主动学习 vs 随机采样: MAE', fontsize=14, fontweight='bold')
ax.legend()
ax.grid(True, alpha=0.3)
ax.set_xticks(data_ratios)
ax.set_xticklabels([f'{int(r*100)}%' for r in data_ratios])

# 子图2: R²对比
ax = axes[0, 1]
ax.plot(data_ratios, al_r2s, 'o-', color='#FF6B6B', linewidth=2, markersize=8, label='DL-LNN 主动学习')
ax.fill_between(data_ratios, 
                [r - s for r, s in zip(rand_r2s, rand_r2s_std)],
                [r + s for r, s in zip(rand_r2s, rand_r2s_std)],
                alpha=0.2, color='#4ECDC4')
ax.plot(data_ratios, rand_r2s, 's-', color='#4ECDC4', linewidth=2, markersize=8, label='随机采样')
ax.axhline(y=0, color='gray', linestyle=':', linewidth=1, alpha=0.5)

ax.set_xlabel('标注数据比例', fontsize=12)
ax.set_ylabel('R²', fontsize=12)
ax.set_title('主动学习 vs 随机采样: R²', fontsize=14, fontweight='bold')
ax.legend()
ax.grid(True, alpha=0.3)
ax.set_xticks(data_ratios)
ax.set_xticklabels([f'{int(r*100)}%' for r in data_ratios])

# 子图3: 数据效率分析
ax = axes[1, 0]
target_mae = 1.0
al_efficiency = None
rand_efficiency = None

for i, mae in enumerate(al_maes):
    if mae < target_mae:
        al_efficiency = data_ratios[i]
        break

for i, mae in enumerate(rand_maes):
    if mae < target_mae:
        rand_efficiency = data_ratios[i]
        break

efficiency_data = []
labels = []
if al_efficiency:
    efficiency_data.append(al_efficiency * 100)
    labels.append('DL-LNN\n主动学习')
if rand_efficiency:
    efficiency_data.append(rand_efficiency * 100)
    labels.append('随机采样')

bars = ax.bar(range(len(efficiency_data)), efficiency_data, color=['#FF6B6B', '#4ECDC4'][:len(efficiency_data)], 
              alpha=0.7, edgecolor='black')

ax.set_xlabel('采样策略', fontsize=12)
ax.set_ylabel('达到MAE<1.0所需数据比例 (%)', fontsize=12)
ax.set_title('数据效率对比', fontsize=14, fontweight='bold')
ax.set_xticks(range(len(efficiency_data)))
ax.set_xticklabels(labels)
ax.grid(True, alpha=0.3, axis='y')

for bar, val in zip(bars, efficiency_data):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1, 
            f'{val:.0f}%', ha='center', va='bottom', fontsize=11, fontweight='bold')

# 子图4: 学习率分析
ax = axes[1, 1]
# 计算每个阶段的改进率
al_improvements = []
rand_improvements = []

for i in range(1, len(al_maes)):
    al_improvements.append((al_maes[i-1] - al_maes[i]) / al_maes[i-1] * 100)
    rand_improvements.append((rand_maes[i-1] - rand_maes[i]) / rand_maes[i-1] * 100)

x_imp = range(len(al_improvements))
width_imp = 0.35

bars1 = ax.bar([i - width_imp/2 for i in x_imp], al_improvements, width_imp, 
               label='DL-LNN 主动学习', color='#FF6B6B', alpha=0.7)
bars2 = ax.bar([i + width_imp/2 for i in x_imp], rand_improvements, width_imp, 
               label='随机采样', color='#4ECDC4', alpha=0.7)

ax.set_xlabel('学习阶段', fontsize=12)
ax.set_ylabel('改进率 (%)', fontsize=12)
ax.set_title('各阶段学习率对比', fontsize=14, fontweight='bold')
ax.set_xticks(x_imp)
ax.set_xticklabels([f'{int(data_ratios[i]*100)}→{int(data_ratios[i+1]*100)}%' 
                    for i in range(len(data_ratios)-1)], rotation=45, ha='right')
ax.legend()
ax.grid(True, alpha=0.3, axis='y')

plt.tight_layout()
plt.savefig(figures_dir / "active_learning_detailed.png", dpi=300, bbox_inches='tight')
plt.close()
print("  ✓ 已保存: active_learning_detailed.png")

# 图8: 综合性能对比总结图
print("\n[8/8] 生成综合性能对比总结图...")

fig = plt.figure(figsize=(16, 10))

# 创建GridSpec
gs = fig.add_gridspec(3, 3, hspace=0.3, wspace=0.3)

# 子图1: 主对比结果汇总（所有数据集的平均MAE）
ax1 = fig.add_subplot(gs[0, 0])
avg_maes = {model: np.mean([main_results[ds][model]['MAE'] for ds in datasets]) for model in models}
sorted_avg = sorted(avg_maes.items(), key=lambda x: x[1])
models_sorted = [m[0] for m in sorted_avg]
maes_sorted = [m[1] for m in sorted_avg]

colors_summary = plt.cm.Set2(np.linspace(0, 1, len(models_sorted)))
bars = ax1.barh(range(len(models_sorted)), maes_sorted, color=colors_summary, alpha=0.7, edgecolor='black')
ax1.set_yticks(range(len(models_sorted)))
ax1.set_yticklabels(models_sorted, fontsize=9)
ax1.set_xlabel('平均MAE', fontsize=10)
ax1.set_title('主对比实验\n(所有数据集平均)', fontsize=11, fontweight='bold')
ax1.grid(True, alpha=0.3, axis='x')

# 子图2: 消融实验结果
ax2 = fig.add_subplot(gs[0, 1])
ablation_models = list(ablation_data.keys())
ablation_maes = [ablation_data[m]['MAE'] for m in ablation_models]
colors_abl = plt.cm.Set2(np.linspace(0, 1, len(ablation_models)))

bars = ax2.barh(range(len(ablation_models)), ablation_maes, color=colors_abl, alpha=0.7, edgecolor='black')
ax2.set_yticks(range(len(ablation_models)))
ax2.set_yticklabels(ablation_models, fontsize=9)
ax2.set_xlabel('MAE', fontsize=10)
ax2.set_title('消融实验', fontsize=11, fontweight='bold')
ax2.grid(True, alpha=0.3, axis='x')

# 子图3: 跨条件泛化（LOMO vs LOCO）
ax3 = fig.add_subplot(gs[0, 2])
lomo_avg_mae = np.mean([lomo_avg[m]['MAE'] for m in models_cross])
loco_avg_mae = np.mean([loco_avg[m]['MAE'] for m in models_cross])

bars = ax3.bar(['LOMO', 'LOCO'], [lomo_avg_mae, loco_avg_mae], 
               color=['#FF6B6B', '#4ECDC4'], alpha=0.7, edgecolor='black')
ax3.set_ylabel('平均MAE', fontsize=10)
ax3.set_title('跨条件泛化对比', fontsize=11, fontweight='bold')
ax3.grid(True, alpha=0.3, axis='y')

# 子图4: 时间常数分布
ax4 = fig.add_subplot(gs[1, :2])
all_tau = []
for layer_info in tc_data['layers']:
    all_tau.extend(layer_info['tau_values'])

ax4.hist(all_tau, bins=30, color='steelblue', alpha=0.7, edgecolor='black')
ax4.axvline(np.mean(all_tau), color='red', linestyle='--', linewidth=2, label=f'均值={np.mean(all_tau):.4f}')
ax4.set_xlabel('时间常数 τ', fontsize=10)
ax4.set_ylabel('频数', fontsize=10)
ax4.set_title('时间常数τ全局分布', fontsize=11, fontweight='bold')
ax4.legend()
ax4.grid(True, alpha=0.3)

# 子图5: 主动学习曲线
ax5 = fig.add_subplot(gs[1, 2])
ax5.plot(data_ratios, al_maes, 'o-', color='#FF6B6B', linewidth=2, markersize=6, label='主动学习')
ax5.plot(data_ratios, rand_maes, 's-', color='#4ECDC4', linewidth=2, markersize=6, label='随机采样')
ax5.set_xlabel('数据比例', fontsize=10)
ax5.set_ylabel('MAE', fontsize=10)
ax5.set_title('主动学习曲线', fontsize=11, fontweight='bold')
ax5.legend(fontsize=8)
ax5.grid(True, alpha=0.3)

# 子图6: 模型性能雷达图
ax6 = fig.add_subplot(gs[2, 0], polar=True)
# 选择前3个模型
top_models = models_sorted[:3]
metrics_radar = ['MAE', 'RMSE', 'R2', 'MAPE', 'PCC']

# 归一化
def normalize_for_radar(model_name, metrics):
    values = []
    for metric in metrics:
        all_vals = [main_results[ds][model_name][metric] for ds in datasets]
        avg_val = np.mean(all_vals)
        values.append(avg_val)
    return values

radar_data = {m: normalize_for_radar(m, metrics_radar) for m in top_models}

# 归一化到0-1
min_vals = {m: min(v) for m, v in zip(metrics_radar, zip(*[radar_data[m] for m in top_models]))}
max_vals = {m: max(v) for m, v in zip(metrics_radar, zip(*[radar_data[m] for m in top_models]))}

angles = np.linspace(0, 2 * np.pi, len(metrics_radar), endpoint=False).tolist()
angles += angles[:1]

for i, model in enumerate(top_models):
    values = []
    for j, metric in enumerate(metrics_radar):
        val = radar_data[model][j]
        if metric in ['MAE', 'RMSE', 'MAPE']:  # 越小越好，反转
            norm = (max_vals[metric] - val) / (max_vals[metric] - min_vals[metric] + 1e-10)
        else:
            norm = (val - min_vals[metric]) / (max_vals[metric] - min_vals[metric] + 1e-10)
        values.append(norm)
    values += values[:1]
    
    ax6.plot(angles, values, 'o-', linewidth=2, label=model, color=colors_summary[i])
    ax6.fill(angles, values, alpha=0.15, color=colors_summary[i])

ax6.set_xticks(angles[:-1])
ax6.set_xticklabels(metrics_radar, fontsize=8)
ax6.set_ylim(0, 1)
ax6.set_title('Top 3模型\n多指标对比', fontsize=11, fontweight='bold', pad=20)
ax6.legend(loc='upper right', bbox_to_anchor=(1.3, 1.1), fontsize=7)

# 子图7: 各数据集性能分布
ax7 = fig.add_subplot(gs[2, 1])
dataset_maes = {ds: np.mean([main_results[ds][m]['MAE'] for m in models]) for ds in datasets}
colors_ds = plt.cm.Set2(np.linspace(0, 1, len(datasets)))

bars = ax7.barh(range(len(datasets)), list(dataset_maes.values()), color=colors_ds, alpha=0.7, edgecolor='black')
ax7.set_yticks(range(len(datasets)))
ax7.set_yticklabels(list(dataset_maes.keys()), fontsize=9)
ax7.set_xlabel('平均MAE', fontsize=10)
ax7.set_title('各数据集性能', fontsize=11, fontweight='bold')
ax7.grid(True, alpha=0.3, axis='x')

# 子图8: 性能指标分布箱线图
ax8 = fig.add_subplot(gs[2, 2])
pcc_all = [main_results[ds][m]['PCC'] for ds in datasets for m in models]
r2_all = [main_results[ds][m]['R2'] for ds in datasets for m in models]

bp = ax8.boxplot([pcc_all, r2_all], labels=['PCC', 'R²'], patch_artist=True)
colors_box = ['#FF6B6B', '#4ECDC4']
for patch, color in zip(bp['boxes'], colors_box):
    patch.set_facecolor(color)
    patch.set_alpha(0.7)

ax8.set_ylabel('值', fontsize=10)
ax8.set_title('性能指标分布', fontsize=11, fontweight='bold')
ax8.grid(True, alpha=0.3, axis='y')

plt.savefig(figures_dir / "comprehensive_summary.png", dpi=300, bbox_inches='tight')
plt.close()
print("  ✓ 已保存: comprehensive_summary.png")

print("\n" + "=" * 80)
print("所有高级可视化图表生成完成！")
print("=" * 80)
print(f"\n生成的图表保存在: {figures_dir}")
print("\n新增图表列表:")
print("  1. model_ranking.png - 模型综合性能排名图")
print("  2. 3d_scatter_plots.png - 3D散点图（模型性能 + 时间常数）")
print("  3. metrics_correlation_heatmap.png - 指标相关性热力图")
print("  4. model_stability_analysis.png - 模型稳定性分析图")
print("  5. cross_condition_detailed.png - 跨条件泛化详细对比图")
print("  6. time_constant_layer_detailed.png - 时间常数层级差异分析图")
print("  7. active_learning_detailed.png - 主动学习详细分析图")
print("  8. comprehensive_summary.png - 综合性能对比总结图")
