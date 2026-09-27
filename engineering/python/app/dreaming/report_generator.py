"""反思报告生成器：输出 Markdown 格式的反思报告。

反思报告（Reflection Report）输出约定：
    - 人类可读的 Markdown 文档
    - 包含去重/更新/洞察/规则候选的完整说明
    - 记录 LLM 模型和置信度（学术诚信 D-2）
    - 硬约束合规性检查结果

报告结构：
    1. 头部元信息（时间、版本、LLM 模型）
    2. 输入摘要（Session 统计）
    3. 去重结果
    4. 过时更新结果
    5. 洞察浮现
    6. 规则候选
    7. 硬约束合规性
    8. 审稿人复核信息（学术诚信）
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


from app.dreaming.reflector import ReflectionResult
from app.dreaming.rule_synthesizer import RuleDraft
from app.dreaming.session_extractor import ProjectSession

logger = logging.getLogger(__name__)


class ReportGenerator:
    """生成 Markdown 反思报告。

    用法：
        generator = ReportGenerator(output_dir="python/outputs/dreaming/reports")
        report_path = generator.generate(
            sessions=sessions,
            reflection=reflection_result,
            rules=rule_drafts,
        )
    """

    def __init__(
        self,
        output_dir: str | None = None,
    ) -> None:
        """初始化报告生成器。

        Args:
            output_dir: 报告输出目录。默认 python/outputs/dreaming/reports
        """
        self.output_dir = Path(output_dir or "python/outputs/dreaming/reports")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # 主入口

    def generate(
        self,
        sessions: list[ProjectSession],
        reflection: ReflectionResult,
        rules: list[RuleDraft] | None = None,
        instructions: str | None = None,
    ) -> str:
        """生成 Markdown 反思报告。

        Args:
            sessions: 反思输入的 Session 列表
            reflection: 反思结果
            rules: 合成的规则草稿（可选）
            instructions: 反思指令

        Returns:
            报告文件路径
        """
        rules = rules or []
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = self.output_dir / f"dream_report_{timestamp}.md"

        content = self._build_report_content(
            sessions=sessions,
            reflection=reflection,
            rules=rules,
            instructions=instructions,
        )

        try:
            with open(report_file, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info("反思报告已生成：%s", report_file)
        except OSError as e:
            logger.error("反思报告写入失败：%s", e)
            raise

        return str(report_file)

    # 报告内容构建

    def _build_report_content(
        self,
        sessions: list[ProjectSession],
        reflection: ReflectionResult,
        rules: list[RuleDraft],
        instructions: str | None,
    ) -> str:
        """构建完整报告内容。"""
        lines: list[str] = []

        # 头部
        lines.append(self._build_header(reflection, instructions))

        # 输入摘要
        lines.append(self._build_input_summary(sessions))

        # 去重结果
        lines.append(self._build_dedup_section(reflection))

        # 过时更新结果
        lines.append(self._build_update_section(reflection))

        # 洞察浮现
        lines.append(self._build_insights_section(reflection))

        # 规则候选
        lines.append(self._build_rules_section(rules))

        # 硬约束合规性
        lines.append(self._build_compliance_section(reflection, rules))

        # 审稿人复核信息
        lines.append(self._build_reviewer_section(sessions, reflection))

        # 落地结论
        lines.append(self._build_conclusion(reflection, rules))

        return "\n".join(lines)

    # 各章节构建

    def _build_header(self, reflection: ReflectionResult, instructions: str | None) -> str:
        """构建报告头部。"""
        return f"""# Dreaming 反思报告

**生成时间**：{reflection.reflected_at}  
**Memory Version**：`{reflection.new_memory_version or "(未提交)"}`  
**LLM 模型**：{reflection.llm_model if reflection.llm_used else "规则统计降级"}  
**LLM 使用**：{"是" if reflection.llm_used else "否"}  
**反思指令**：{instructions or "(默认)"}

---

## 摘要

{reflection.summary}
"""

    def _build_input_summary(self, sessions: list[ProjectSession]) -> str:
        """构建输入 Session 摘要。"""
        total = len(sessions)
        success = sum(1 for s in sessions if s.outcome == "success")
        failure = sum(1 for s in sessions if s.outcome == "failure")
        warning = sum(1 for s in sessions if s.outcome == "warning")

        # 按数据源统计
        by_source: dict[str, int] = {}
        for s in sessions:
            by_source[s.source] = by_source.get(s.source, 0) + 1

        # AR-02 标记统计
        ar02_count = sum(1 for s in sessions if s.is_ar_02_pre_fix)

        source_lines = "\n".join(f"- {src}：{cnt} 个" for src, cnt in by_source.items())

        return f"""---

## 1. 输入 Session 摘要

| 指标 | 值 |
|------|-----|
| 总 Session 数 | {total} |
| 成功 | {success} |
| 失败 | {failure} |
| 警告 | {warning} |
| AR-02 修复前数据 | {ar02_count}（已{"包含" if ar02_count > 0 else "排除"}） |

**数据源分布**：

{source_lines or "(无数据)"}
"""

    def _build_dedup_section(self, reflection: ReflectionResult) -> str:
        """构建去重结果章节。"""
        dedup = reflection.deduplicated
        return f"""---

## 2. 去重结果

| 指标 | 值 |
|------|-----|
| 合并条目数 | {dedup.merged_count} |
| 移除节点数 | {len(dedup.removed_node_ids)} |
| 保留节点数 | {len(dedup.kept_node_ids)} |

**策略**：按 entity 分组，同一 entity 下 content 相同的条目合并，
保留 validation_count 最高的节点作为主节点，其余标记 `deprecated` 并
将 `merged_into` 指向主节点（不移除，保留审计记录）。
"""

    def _build_update_section(self, reflection: ReflectionResult) -> str:
        """构建过时更新结果章节。"""
        update = reflection.updated
        details_text = (
            "(无)"
            if not update.details
            else "\n".join(
                f"- `{d['node_id']}`：confidence {d.get('old_confidence', '?')} → "
                f"{d.get('new_confidence', '?')}（{d.get('reason', '?')}）"
                for d in update.details[:10]  # 最多显示 10 条
            )
        )
        if len(update.details) > 10:
            details_text += f"\n- ... 其余 {len(update.details) - 10} 条省略"

        return f"""---

## 3. 过时更新结果

| 指标 | 值 |
|------|-----|
| 失效节点数 | {len(update.invalidated_node_ids)} |
| 需重新验证节点数 | {len(update.updated_node_ids)} |

**失效详情**：

{details_text}

**硬约束执行**：
- HRC52 `pending_calibration` 强制降低置信度至 ≤0.3
- CAM 验证失败的 Session 对应 memory 标记 `requires_revalidation=True`
- SUCCEEDED 任务对应 memory 不可删除（仅可降低 confidence）
"""

    def _build_insights_section(self, reflection: ReflectionResult) -> str:
        """构建洞察浮现章节。"""
        insights = reflection.insights
        if not insights:
            return """

---

## 4. 洞察浮现

(无洞察浮现)
"""

        insights_text = "\n\n".join(
            f"### 4.{i + 1} [{insight.category}] {insight.content[:80]}\n\n"
            f"- **置信度**：{insight.confidence:.2f}\n"
            f"- **支撑 Session 数**：{len(insight.supporting_sessions)}\n"
            f"- **支撑 Session ID**：{', '.join(insight.supporting_sessions[:5])}"
            + (f"... 等 {len(insight.supporting_sessions)} 个" if len(insight.supporting_sessions) > 5 else "")
            for i, insight in enumerate(insights)
        )

        return f"""

---

## 4. 洞察浮现

共浮现 {len(insights)} 条洞察。

{insights_text}
"""

    def _build_rules_section(self, rules: list[RuleDraft]) -> str:
        """构建规则候选章节。"""
        if not rules:
            return """

---

## 5. 规则候选

(无规则草稿生成)
"""

        rules_text = "\n\n".join(
            f"### 5.{i + 1} [{rule.rule_type}] {rule.description}\n\n"
            f"- **规则 ID**：`{rule.rule_id}`\n"
            f"- **状态**：{rule.status}\n"
            f"- **置信度**：{rule.confidence:.2f}\n"
            f"- **遵守 CAM 验证**：{'是' if rule.respects_cam_validation else '否'}\n"
            f"- **遵守 SUCCEEDED 锁定**：{'是' if rule.respects_succeeded_lock else '否'}\n"
            f"- **触发条件**：`{rule.condition}`\n"
            f"- **执行动作**：`{rule.action}`\n"
            f"- **支撑 Session 数**：{len(rule.supporting_sessions)}\n"
            f"- **来源洞察**：{rule.source_insight_content[:60]}"
            for i, rule in enumerate(rules)
        )

        return f"""

---

## 5. 规则候选

共合成 {len(rules)} 条规则草稿。所有规则状态为 `draft`，
需经过 RuleValidator 沙箱验证后才能应用。

{rules_text}

**注意**：规则不直接生效，需经过以下流程才能应用：
1. RuleValidator 沙箱验证
2. 人工审核（可选）
3. 灰度应用（progressive publisher）
4. 持久化到知识图谱
"""

    def _build_compliance_section(
        self,
        reflection: ReflectionResult,
        rules: list[RuleDraft],
    ) -> str:
        """构建硬约束合规性章节。"""
        # 检查规则是否违反硬约束
        violations = []
        for rule in rules:
            if not rule.respects_cam_validation:
                violations.append(f"- 规则 `{rule.rule_id}` 试图绕过 CAM 二次验证（已拒绝）")
            if not rule.respects_succeeded_lock:
                violations.append(f"- 规则 `{rule.rule_id}` 试图解锁 SUCCEEDED 任务（已拒绝）")

        violation_text = "\n".join(violations) if violations else "（无违规）"

        return f"""

---

## 6. 硬约束合规性

| 约束 | 状态 |
|------|------|
| CAM 二次验证始终 True | {"合规" if all(r.respects_cam_validation for r in rules) else "违规"} |
| SUCCEEDED 任务禁删 | {"合规" if all(r.respects_succeeded_lock for r in rules) else "违规"} |
| HRC52 pending_calibration 降低置信度 | 已在过时更新阶段执行 |
| 单轮审核状态机 | 未触碰（反思只修改 memory，不修改任务状态） |

**违规明细**：

{violation_text}
"""

    def _build_reviewer_section(
        self,
        sessions: list[ProjectSession],
        reflection: ReflectionResult,
    ) -> str:
        """构建审稿人复核信息章节（学术诚信 D-2）。"""
        ar02_sessions = [s for s in sessions if s.is_ar_02_pre_fix]

        artifact_lines = []
        for s in sessions[:20]:  # 最多列出 20 个
            if s.raw_artifact_path:
                artifact_lines.append(f"- `{s.session_id}`：{s.raw_artifact_path}")
        if len(sessions) > 20:
            artifact_lines.append(f"- ... 其余 {len(sessions) - 20} 个见完整日志")

        return f"""

---

## 7. 审稿人复核信息（学术诚信 D-2）

### 7.1 AR-02 数据排除

- AR-02 修复前数据数：{len(ar02_sessions)}
- 论文数据处理：{"排除" if ar02_sessions else "不适用"}
- 排除依据：`is_ar_02_pre_fix=True` 标记

### 7.2 LLM 使用透明度

- 是否使用 LLM：{"是" if reflection.llm_used else "否"}
- LLM 模型：{reflection.llm_model or "N/A"}
- 降级原因：{"N/A" if reflection.llm_used else "ProviderRouter 无可用 Provider"}

### 7.3 原始 Artifact 路径

以下列出支撑本反思报告的原始 Session artifact 路径，供审稿人复核：

{chr(10).join(artifact_lines) if artifact_lines else "(无)"}

### 7.4 可复现性

- Memory Version：`{reflection.new_memory_version or "N/A"}`
- Git Diff 命令：`git diff {reflection.new_memory_version or "<parent>"}~1 {reflection.new_memory_version or "HEAD"}`
- MLflow Tracking URI：见 `python/app/ai/lnn/training/experiment_tracker.py`
"""

    def _build_conclusion(
        self,
        reflection: ReflectionResult,
        rules: list[RuleDraft],
    ) -> str:
        """构建落地结论章节。"""
        return f"""

---

## 8. 落地结论

本次 Dreaming 反思完成以下工作：

1. **去重**：合并 {reflection.deduplicated.merged_count} 条重复 memory
2. **过时更新**：失效 {len(reflection.updated.invalidated_node_ids)} 条，
   标记 {len(reflection.updated.updated_node_ids)} 条需重新验证
3. **洞察浮现**：{len(reflection.insights)} 条洞察
4. **规则候选**：{len(rules)} 条草稿（状态 `draft`，待验证）
5. **Memory Version**：`{reflection.new_memory_version or "未提交"}`

**后续动作**：
- [ ] 人工审阅本报告
- [ ] 对 `draft` 规则执行 RuleValidator 沙箱验证
- [ ] 通过验证的规则进入灰度应用队列
- [ ] 将本报告归档到 `python/outputs/dreaming/reports/`

---

*本报告由 Dreaming 离线反思模块自动生成（ADR-021）。*
"""
