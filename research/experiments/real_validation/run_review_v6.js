// run_review_v6.js — 第 6 轮 Hermes 评审（v5.1 修复验证）
import { runAgentConversation } from './hermes_bridge.js';
import { writeFileSync, existsSync, statSync } from 'node:fs';

const prompt = "你是一名资深审稿人（IJMTM / MSSP / JMP 一区制造工程期刊标准），评审一篇中文论文的 v5.1 版。\n任务：先读取项目文件 docs/LAM_chatter_paper_draft_v2_zh.md 的完整全文（这是 v5.1 版，在上一轮 v5 评审（76 分）后按评审意见修复了以下问题，请逐一核实修复是否到位、是否引入新问题），然后完成严格评审。\n\n上一轮评审（v5，76 分）提出的修复项，本轮需逐条核实：\n1. F1-①：§7.1 κ 三角分布描述改为\"峰值（mode）0.000736（分布均值 0.000843）\"（原误写\"均值 0.000736\"，三角分布实际均值不可能为 0.000736）——请复核分布描述与数字。\n2. F1-②：§7.2 确定性交叉校验注明 ΔT=599 °C（ξ=920 中位）口径（原写 1.40× 但未注明 ΔT，ΔT=500 时实为 1.31×）。\n3. M2：§4.5 与摘要的\"5/7 点中值偏差 ≤2pp\"改为\"4/7 点 ≤2pp，1 点 3.6pp，2 点大偏差（15.3/12.9pp）\"——请用论文表格数据手算验证 4/7 是否正确。\n4. F3：摘要\"在工艺阻尼区域识别出实测模态输入的有效证据\"已降级为\"推测性解释，无工艺阻尼数值证据\"——请确认摘要与正文口径一致。\n5. M4：参考文献 [15] Zatarain 2008 已在 §2.2 补引；新增 §11.3 局限 9（引擎 num_lobes=10 叶瓣数限制）。\n6. 图号已全链重排为按出现顺序 Fig.1–Fig.11。\n7. M3：§10.2 表 C（DL-LNN）与表 A（默认 Tlusty）数字相同已加注说明（独立模型真实重合非复制错误）；表 B 措辞\"（本文方法）\"已删。\n8. M5：§9.4 与 §6.3 同工况 RMS 差异（355.6 vs 999.3 μm）已量化解释（t_end=1.5 s vs 2.0 s 发散时长）；多种子段\"±12–60\"已补单位 μm。\n9. R1：元语言清理（\"回应…批评\"\"技术债\"\"集体失败= \"\"非本研究的逻辑缺口\"等已删除/改写）。\n\n请使用你的文件读取工具完整读取该论文全文，然后输出以下七个部分：\n一、12 维分项评分（每维 10 分 + 论文内证据 + 扣分理由）：1创新性与新颖性 2方法严谨性 3实验设计 4统计分析 5写作质量 6可复现性 7文献综述 8数据质量 9泛化性证据 10工程价值 11诚实性/负面结果处理 12图表质量。\n二、总分（百分制），并说明与上一轮（v5，76 分）相比的变化及原因。\n三、一区可发表判定（是/否 + 理由）。\n四、致命缺陷清单（最多 3 条）。\n五、小修建议清单（最多 5 条）。\n六、学术诚信专项审计（逐项给出结论）：\n  A. §10 实测数据与合成数据是否严格区分；\n  B. §10.2 的\"7 个实测点\"结果（acc 0.43-0.57、MCC 为负）是否如实报告；\n  C. §10.3 PHM2010 PCC=0.982 是否被正确标注为物理代理标签；\n  D. Inconel 718 num_lobes 局限是否诚实披露（含新增局限 9）；\n  E. 章节与图号编号是否一致；\n  F. 摘要、贡献、局限、结论四处表述是否互相一致；\n  G. 上表 9 项修复是否到位、是否引入新问题。\n七、对\"如何进一步提升学术价值\"给出不超过 3 条具体可操作建议。\n\n要求：用中文，不要客气，以顶刊标准严格评审，负面问题直接指出。\n完成后，将完整评审报告写入文件 docs/review_outputs/hermes_review_v6_fixed.md（使用你的文件写入能力，必须实际写入）。\n最后在回复中说明评审报告文件是否已写入成功。";
const outFile = 'C:/Users/Lenovo/Desktop/灵境制造（上线版）/docs/review_outputs/hermes_review_v6_fixed.md';

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
  writeFileSync('C:/Users/Lenovo/AppData/Local/Temp/hermes_review_v6_conversation.txt', r.output, 'utf8');
  console.log('对话输出已存 hermes_review_v6_conversation.txt, 长度:', r.output.length);
  console.log('--- 输出末尾 ---');
  console.log(r.output.slice(-1200));
} else {
  console.log('错误:', r.error);
  if (r.stderr) console.log('stderr:', r.stderr.slice(-800));
}