// run_review_hermes_v2revised.js — 用 Hermes AIAgent 评审 v2 论文
import { runAgentConversation } from './hermes_bridge.js';
import { writeFileSync, existsSync, statSync } from 'node:fs';

const prompt = "你是一名资深审稿人（IJMTM / MSSP / JMP 一区制造工程期刊标准）。\n任务：读取项目文件 docs/LAM_chatter_paper_draft_v2_zh.md 的全文（中文初稿 v2，661 行，含新增 §10 实测数据交叉验证与公开数据缺口分析），完成严格评审。\n请使用你的文件读取工具完整读取该论文，然后完成以下七个部分的评审输出：\n一、12 维分项评分（每维 10 分 + 论文内证据 + 扣分理由）：1创新性与新颖性 2方法严谨性 3实验设计 4统计分析 5写作质量 6可复现性 7文献综述 8数据质量 9泛化性证据 10工程价值 11诚实性/负面结果处理 12图表质量。\n二、总分（百分制）。\n三、一区可发表判定（是/否 + 理由）。\n四、致命缺陷清单（最多 3 条）。\n五、小修建议清单（最多 5 条）。\n六、学术诚信专项审计（最高优先级，逐项给出结论）：\n  A. §10 新增的实测数据验证是否与合成数据严格区分、无混淆；\n  B. §10.2 的\"7 个实测点\"结果（acc 0.43-0.57、MCC 为负）是否如实报告、无过度声称；\n  C. §10.3 的 PHM2010 PCC=0.982 是否被错误标注为实测稳定性验证（应为物理代理标签训练）；\n  D. Inconel 718 的 num_lobes 引擎局限是否诚实披露；\n  E. 章节重编号（§10 实测验证/§11 讨论/§12 实验计划/§13 结论）是否一致、无残留旧引用；\n  F. 摘要、贡献、局限、结论四处对新增真实数据验证的表述是否互相一致。\n七、对\"如何进一步提升学术价值\"给出不超过 3 条具体可操作建议。\n\n要求：用中文，不要客气，以顶刊标准严格评审，负面问题直接指出。\n完成后，将完整评审报告写入文件 docs/review_outputs/hermes_review_v4_anchored.md（使用你的文件写入能力，必须实际写入）。\n最后在回复中说明评审报告文件是否已写入成功。";
const outFile = 'C:/Users/Lenovo/Desktop/灵境制造（上线版）/docs/review_outputs/hermes_review_v4_anchored.md';

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
  writeFileSync('C:/Users/Lenovo/AppData/Local/Temp/hermes_review_conversation.txt', r.output, 'utf8');
  console.log('对话输出已存 hermes_review_conversation.txt, 长度:', r.output.length);
  console.log('--- 输出末尾 ---');
  console.log(r.output.slice(-1500));
} else {
  console.log('错误:', r.error);
  if (r.stderr) console.log('stderr:', r.stderr.slice(-800));
}