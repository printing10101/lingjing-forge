# SLD-as-Prompt 模板对话样例

> **学术诚信声明**：
> 演示样例1/2/3 为人工设计的 prompt 模板示例，仅展示对话形式，不对应真实实验；真实查询行的数值从实验结果 JSON 自动派生。

**数据来源**：
- `results/stability_lobes_results.json`
- `results/main_comparison_results.json`
- `results/cross_condition_results.json`

## 表7 内容

| 行 | 角色 | 内容 | 数据来源 |
|----|------|------|----------|
| 1 | User | [模板示例] 用户描述：加工过程中出现明显振纹，当前主轴转速较高，希望诊断原因。 | synthetic_template_example |
| 1 | Assistant | [模板示例] SLD-as-Prompt 模板响应：根据输入工况，推断当前转速可能处于稳定性叶瓣峰值附近，建议调整至相邻叶瓣谷值转速并减小切深。 | synthetic_template_example |
| 2 | User | [模板示例] 用户描述：固定转速下逐步加大轴向切深，出现颤振后减小切深即消失。 | synthetic_template_example |
| 2 | Assistant | [模板示例] SLD-as-Prompt 模板响应：当前切深可能已超过 SLD 预测的极限切深 a_lim，建议降至安全裕量内。 | synthetic_template_example |
| 3 | User | [模板示例] 用户描述：缺少机床模态参数（k、m、ζ），需评估稳定性。 | synthetic_template_example |
| 3 | Assistant | [模板示例] SLD-as-Prompt 模板响应：缺少模态参数时模型退化为粗略 SLD；建议先通过锤击法测定模态参数后重新校核。 | synthetic_template_example |
| 真实查询 | User | n = 5,500 r/min（SLD 实验网格中点工况）；ap 待评估，请基于 DL-LNN 推理给出稳定性判定与极限切深。。 | real_experiment_results |
| 真实查询 | Assistant | 根据 DL-LNN 在 SLD 网格上的预测，参考极限切深 a_lim ≈ 19.50 mm （模型与 Tlusty 理论 a_lim 的 MAE = 0.496 mm） 在 PHM2010 真实数据集上 DL-LNN 的 MAE = 0.793 mm，RMSE = 0.987 mm 跨工况平均 PCC = 0.9966（LOCO 评估） 建议：将 ap 控制在 16.58 mm 以内（保留 15% 安全裕量）。 | real_experiment_results |

## 说明

- demo_samples 的 data_source = 'synthetic_template_example'，在论文中应明确标注为模板示例。
- 真实查询行的数值（a_lim、MAE、RMSE、PCC 等）均来自实验结果 JSON，未做任何手工编造。
- 本脚本不修改任何 .docx 论文文件；论文作者需人工审阅后决定是否采用。