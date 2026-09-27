// run_review_v8.js — 第 8 轮 Hermes 评审（v7 修复验证）
import { runAgentConversation } from './hermes_bridge.js';
import { writeFileSync, existsSync, statSync } from 'node:fs';

const prompt = "你是一名资深审稿人（IJMTM / MSSP / JMP 一区制造工程期刊标准），评审一篇中文论文的 v7 版。\n任务：先读取项目文件 docs/LAM_chatter_paper_draft_v2_zh.md 的完整全文（这是 v7 版，在上一轮 v7 评审（79.3 分）后按评审意见修复了 F1/F2/F3 致命缺陷与若干小修，请逐一核实修复是否到位、是否引入新问题），然后完成严格评审。\n\n上一轮评审（v7，79.3 分）提出的修复项，本轮需逐条核实：\n1. F1（§5.3 表标签-数值错配）：表已改为\"行1 谷 2.0×（κ_eff 上缘 0.0008）｜625 °C｜0.57–0.85 kW\"\"行2 谷 1.30×（κ_eff 中值 0.00046）｜500 °C｜0.45–0.68 kW\"；§11.2/§13 同步改为\"1.30×（ΔT=500 °C）；651 W 在 ξ=920 口径下 1.42×\"。请手算验证两行增益-温升-功率三者自洽。\n2. F2（§8 Ti-6Al-4V 行 κ 区间口径）：表注已补充说明\"Ti-6Al-4V 行 κ 取 9 组 J-C 参数中 Ti-6Al-4V 锚点的均值±25% 子区间 [0.000552, 0.000920]，故 κ_eff=[0.000397, 0.000765]；若按 §4.1 全区间则 κ_eff=[0.000372, 0.001112]、增益 [1.29, 2.99×]，两口径均给出\"。请验证披露完整性。\n3. F3（§12 E3 判据温升口径）：已改为\"651 W 即 ΔT≈599 °C 时 1.31–1.85×；ΔT=500 °C 时对应 1.25–1.62×，§8 表注口径\"。请验证与 §8 表一致。\n4. M2（§7.3 钳位性能）：已补\"722 W 时 ΔT_max=800 °C，最保守参数下软化上限 g=1.21×，仍 >1.1×；25/25 结论在 ξ 中位工况成立\"。\n5. M3（随文发布兑现）：§10.2 已补 A–G 逐点数据表（转速/切深/悬伸进向/实测状态/三模型逐点判定）+ CSV 引用。\n6. M4 小修：L391\"随 r 减小\"→\"随 r 增大\"；\"κ_eff=0.00046（r=0.3 假设）\"→\"（§4.3 实测中值，r=0.3 计算值 0.000581）\"；[22] 补 DOI 10.1115/1.4025393；§4.5\"7/7 落入\"→\"落入或与预测带相交\"。\n7. M5 安全窗口径：§11.2 已明确\"651 W 常态工况满足平均窗，r 鲁棒律峰值 768 °C 属峰值口径、需 §12 E5 表面完整性验证\"。\n\n请使用你的文件读取工具完整读取该论文全文，然后输出以下七个部分：\n一、12 维分项评分（每维 10 分 + 论文内证据 + 扣分理由）：1创新性与新颖性 2方法严谨性 3实验设计 4统计分析 5写作质量 6可复现性 7文献综述 8数据质量 9泛化性证据 10工程价值 11诚实性/负面结果处理 12图表质量。\n二、总分（百分制），并说明与上一轮（v7，79.3 分）相比的变化及原因。\n三、一区可发表判定（是/否 + 理由）。\n四、致命缺陷清单（最多 3 条）。\n五、小修建议清单（最多 5 条）。\n六、学术诚信专项审计（逐项给出结论）：\n  A. §10 实测数据与合成数据是否严格区分；\n  B. §10.2 的\"7 个实测点\"结果是否如实报告、逐点表是否兑现；\n  C. §10.3 PHM2010 PCC=0.982 是否被正确标注为物理代理标签；\n  D. num_lobes 局限（含局限 9）是否诚实披露；\n  E. 章节与图号编号是否一致；\n  F. 摘要、贡献、局限、结论四处表述是否互相一致；\n  G. 上表 7 项修复是否到位、是否引入新问题。\n七、对\"如何进一步提升学术价值\"给出不超过 3 条具体可操作建议。\n\n要求：用中文，不要客气，以顶刊标准严格评审，负面问题直接指出。\n完成后，将完整评审报告写入文件 docs/review_outputs/hermes_review_v8_polished.md（使用你的文件写入能力，必须实际写入）。\n最后在回复中说明评审报告文件是否已写入成功。";
const outFile = 'C:/Users/Lenovo/Desktop/灵境制造（上线版）/docs/review_outputs/hermes_review_v8_polished.md';

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
  writeFileSync('C:/Users/Lenovo/AppData/Local/Temp/hermes_review_v8_conversation.txt', r.output, 'utf8');
  console.log('对话输出已存 hermes_review_v8_conversation.txt, 长度:', r.output.length);
  console.log('--- 输出末尾 ---');
  console.log(r.output.slice(-1200));
} else {
  console.log('错误:', r.error);
  if (r.stderr) console.log('stderr:', r.stderr.slice(-800));
}