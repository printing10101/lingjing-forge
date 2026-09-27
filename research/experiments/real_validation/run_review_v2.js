// run_review_v2.js — 单 agent 直接评审（禁止子代理）
import { createSession, promptAsync, waitForResult } from './openscience_bridge.js';
import { writeFileSync } from 'node:fs';

const prompt = "你是一名资深审稿人（IJMTM / MSSP / JMP 一区制造工程期刊标准）。\n【重要】直接由你本人完成以下评审工作。禁止调用 task 工具或任何 subagent/子代理工具——你只有一个主 agent 会话，所有工作必须由你自己完成。\n现在请读取项目文件 docs/LAM_chatter_paper_draft_v2_zh.md 的全文（中文初稿 v2，661 行，含新增 §10 实测数据交叉验证与公开数据缺口分析），然后完成严格评审。\n\n评审输出必须包含七个部分：\n一、12 维分项评分（每维 10 分 + 论文内证据 + 扣分理由）：1创新性与新颖性 2方法严谨性 3实验设计 4统计分析 5写作质量 6可复现性 7文献综述 8数据质量 9泛化性证据 10工程价值 11诚实性/负面结果处理 12图表质量。\n二、总分（百分制）。\n三、一区可发表判定（是/否 + 理由）。\n四、致命缺陷清单（最多 3 条）。\n五、小修建议清单（最多 5 条）。\n六、学术诚信专项审计（最高优先级，逐项给出结论）：\n  A. §10 新增的实测数据验证是否与合成数据严格区分、无混淆；\n  B. §10.2 的\"7 个实测点\"结果（acc 0.43-0.57、MCC 为负）是否如实报告、无过度声称；\n  C. §10.3 的 PHM2010 PCC=0.982 是否被错误标注为实测稳定性验证（应为物理代理标签训练）；\n  D. Inconel 718 的 num_lobes 引擎局限是否诚实披露；\n  E. 章节重编号（§10 实测验证/§11 讨论/§12 实验计划/§13 结论）是否一致、无残留旧引用；\n  F. 摘要、贡献、局限、结论四处对新增真实数据验证的表述是否互相一致。\n七、对\"如何进一步提升学术价值\"给出不超过 3 条具体可操作建议。\n\n要求：用中文，不要客气，以顶刊标准严格评审，负面问题直接指出。\n评审完成后，将完整评审报告保存为 markdown 文件到项目 docs/review_outputs/openscience_review_lj_v2paper.md（必须实际执行文件写入）。";
const outFile = 'C:/Users/Lenovo/Desktop/灵境制造（上线版）/docs/review_outputs/openscience_review_lj_v2paper.md';

console.log('[1/3] 创建会话...');
const ses = await createSession('Strict review of LAM paper v2 (single-agent, no subagent)');
console.log('  session:', ses.id);
console.log('[2/3] 提交评审任务...');
await promptAsync(ses.id, prompt, { agent: 'research' });
console.log('  已提交，等待（最长 35 分钟）...');
const r = await waitForResult(ses.id, 35 * 60 * 1000, { outFile });
console.log('\n[3/3] 完成');
console.log('status:', r.status, '| 耗时:', Math.round(r.elapsedMs / 1000) + 's');
if (r.files) {
  const st = await import('node:fs').then(m => m.statSync(outFile));
  console.log('输出文件存在:', outFile, st.size + 'B');
} else if (r.output) {
  writeFileSync('C:/Users/Lenovo/AppData/Local/Temp/review_fallback2.txt', r.output, 'utf8');
  console.log('assistant 文本已存 review_fallback2.txt');
} else {
  console.log('无输出');
}
