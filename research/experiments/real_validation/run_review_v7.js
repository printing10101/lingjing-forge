// run_review_v7.js — 第 7 轮 Hermes 评审（v6 修复验证）
import { runAgentConversation } from './hermes_bridge.js';
import { writeFileSync, existsSync, statSync } from 'node:fs';

const prompt = "你是一名资深审稿人（IJMTM / MSSP / JMP 一区制造工程期刊标准），评审一篇中文论文的 v6 版。\n任务：先读取项目文件 docs/LAM_chatter_paper_draft_v2_zh.md 的完整全文（这是 v6 版，在上一轮 v6 评审（77.5 分）后按评审意见修复了 F1/F2/F3 致命缺陷与若干小修，请逐一核实修复是否到位、是否引入新问题），然后完成严格评审。\n\n上一轮评审（v6，77.5 分）提出的修复项，本轮需逐条核实：\n1. F1（工程结论口径）：摘要/§5.3/§11.2/§13 的\"2.0× 需 0.45–0.68 kW\"已改为\"2.0×（κ_eff 上缘 0.0008 假设）约需 0.81 kW；κ_eff 中值下 651 W → 1.42×（P5 ≥1.14×）\"；§12 E3 判定标准改为\"实测增益落入预测区间 1.31–1.85×\"。请手算验证 2.0× 与 κ_eff=0.001 的关系、以及带内参数的保守数字。\n2. F2（§8 多材料表口径）：表格加了表注说明口径——Ti 系 ξ=920（ΔT≈599 °C）、Inconel 因镍基热导率较高 ξ=920×0.9=828（ΔT≈539 °C）。请用表注口径复算 Ti/Inconel 行的 κ_eff 区间与增益，确认可复现。\n3. F3（§7.3 安全钳位）：已补\"ξ 上缘 1107 时 1000 W → ΔT≈1107 °C 超出 800 °C 硬约束，控制律钳位须联动 P_max,eff = 800/ξ_max ≈ 722 W\"。请验证钳位逻辑。\n4. 新增参考文献 [28] Insperger & Stépán 2002 半离散法原始文献，§2.1 已补引。\n5. §10.1 Zheng 2023 行已标注\"降级为引擎叶瓣数限制边界案例\"。\n6. 格式清理：双分隔线删除、loss 统一 0.00298、\"（§4.3 表 3）\"失效引用删除、\"叙事\"改\"认识\"、FLIR 级改具体表述、摘要\"如实\"收敛、摘要节引用删除、r=0.5 改\"保守中值\"。\n\n请使用你的文件读取工具完整读取该论文全文，然后输出以下七个部分：\n一、12 维分项评分（每维 10 分 + 论文内证据 + 扣分理由）：1创新性与新颖性 2方法严谨性 3实验设计 4统计分析 5写作质量 6可复现性 7文献综述 8数据质量 9泛化性证据 10工程价值 11诚实性/负面结果处理 12图表质量。\n二、总分（百分制），并说明与上一轮（v6，77.5 分）相比的变化及原因。\n三、一区可发表判定（是/否 + 理由）。\n四、致命缺陷清单（最多 3 条）。\n五、小修建议清单（最多 5 条）。\n六、学术诚信专项审计（逐项给出结论）：\n  A. §10 实测数据与合成数据是否严格区分；\n  B. §10.2 的\"7 个实测点\"结果是否如实报告；\n  C. §10.3 PHM2010 PCC=0.982 是否被正确标注为物理代理标签；\n  D. num_lobes 局限（含新增局限 9）是否诚实披露；\n  E. 章节与图号编号是否一致；\n  F. 摘要、贡献、局限、结论四处表述是否互相一致；\n  G. 上表 6 项修复是否到位、是否引入新问题。\n七、对\"如何进一步提升学术价值\"给出不超过 3 条具体可操作建议。\n\n要求：用中文，不要客气，以顶刊标准严格评审，负面问题直接指出。\n完成后，将完整评审报告写入文件 docs/review_outputs/hermes_review_v7_consolidated.md（使用你的文件写入能力，必须实际写入）。\n最后在回复中说明评审报告文件是否已写入成功。";
const outFile = 'C:/Users/Lenovo/Desktop/灵境制造（上线版）/docs/review_outputs/hermes_review_v7_consolidated.md';

console.log('[1/2] 提交评审任务给 Hermes AIAgent...');
const t0 = Date.now();
const r = await runAgentConversation(prompt, { timeoutMs: 35 * 60 * 1000 });
console.log('[2/2] 完成，耗时:', Math.round((Date.now() - t0) / 1000) + 's');
console.log('ok:', r.ok);
if (existsSync(outFile)) {
  console.log('评审报告文件已生成:', statSync(outFile).size + 'B');
} else {
  console.log('评审报告文件未生成');
}
if (r.ok) {
  writeFileSync('C:/Users/Lenovo/AppData/Local/Temp/hermes_review_v7_conversation.txt', r.output, 'utf8');
  console.log('对话输出已存 hermes_review_v7_conversation.txt, 长度:', r.output.length);
  console.log('--- 输出末尾 ---');
  console.log(r.output.slice(-1200));
} else {
  console.log('错误:', r.error);
  if (r.stderr) console.log('stderr:', r.stderr.slice(-800));
}