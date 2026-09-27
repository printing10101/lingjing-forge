// run_review_v5.js — 第 5 轮 Hermes 评审（润色后 v2）
import { runAgentConversation } from './hermes_bridge.js';
import { writeFileSync, existsSync, statSync } from 'node:fs';

const prompt = "你是一名资深审稿人（IJMTM / MSSP / JMP 一区制造工程期刊标准），评审一篇中文论文的最新润色版。\n任务：先读取项目文件 docs/LAM_chatter_paper_draft_v2_zh.md 的完整全文（这是 v2 润色版：在上一轮评审基础上新增了 §10.3 PCC 局限分析、§6.3 多种子统计、并做了全篇中文语言润色——et al.→等、口语词收敛、表述克制化、章节引导句、数字一致性修正），然后完成严格评审。\n\n请使用你的文件读取工具完整读取该论文全文，然后输出以下七个部分：\n一、12 维分项评分（每维 10 分 + 论文内证据 + 扣分理由）：1创新性与新颖性 2方法严谨性 3实验设计 4统计分析 5写作质量 6可复现性 7文献综述 8数据质量 9泛化性证据 10工程价值 11诚实性/负面结果处理 12图表质量。\n二、总分（百分制），并说明与本轮之前版本（上一轮总分约 74）相比的变化及原因。\n三、一区可发表判定（是/否 + 理由）。\n四、致命缺陷清单（最多 3 条）。\n五、小修建议清单（最多 5 条）。\n六、学术诚信专项审计（最高优先级，逐项给出结论）：\n  A. §10 实测数据验证是否与合成数据严格区分、无混淆；\n  B. §10.2 的\"7 个实测点\"结果（acc 0.43-0.57、MCC 为负）是否如实报告、无过度声称；\n  C. §10.3 的 PHM2010 PCC=0.982 是否被错误标注为实测稳定性验证（应为物理代理标签训练）；\n  D. Inconel 718 的 num_lobes 引擎局限是否诚实披露；\n  E. 章节编号（§1-§13）是否一致、无残留旧引用；\n  F. 摘要、贡献、局限、结论四处对新增真实数据验证的表述是否互相一致；\n  G. §6.3 多种子统计与 §10.3 PCC 置信区间这两个新增统计是否恰当、无过度声称。\n七、针对本次中文润色给出评价：语言是否更符合中文科技论文习惯、是否还有英文直译腔或口语化残留（举例指出具体行文位置），并给出不超过 3 条具体可操作建议。\n\n要求：用中文，不要客气，以顶刊标准严格评审，负面问题直接指出。\n完成后，将完整评审报告写入文件 docs/review_outputs/hermes_review_v5_polished.md（使用你的文件写入能力，必须实际写入）。\n最后在回复中说明评审报告文件是否已写入成功。";
const outFile = 'C:/Users/Lenovo/Desktop/灵境制造（上线版）/docs/review_outputs/hermes_review_v5_polished.md';

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
  writeFileSync('C:/Users/Lenovo/AppData/Local/Temp/hermes_review_v5_conversation.txt', r.output, 'utf8');
  console.log('对话输出已存 hermes_review_v5_conversation.txt, 长度:', r.output.length);
  console.log('--- 输出末尾 ---');
  console.log(r.output.slice(-1500));
} else {
  console.log('错误:', r.error);
  if (r.stderr) console.log('stderr:', r.stderr.slice(-800));
}