"""知识图谱健康检查报告生成器（M1.5）。

将 HealthCheckResult 转换为结构化、易读的 Markdown 格式报告。

报告结构：
    1. 检测概览（摘要统计）
    2. 孤立节点详情
    3. 矛盾关系详情
    4. 老旧数据详情
    5. 检测元数据（时间戳、耗时等）

设计原则：
    - 自然语言描述与表格结合
    - 结构化、条理清晰
    - 避免纯 JSON 输出
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TextIO

from app.knowledge_graph.health.checker import (
    HealthCheckResult,
    IsolatedNodeResult,
    ContradictoryEdgeResult,
    StaleNodeResult,
)

logger = logging.getLogger(__name__)


class HealthReportGenerator:
    """健康检查报告生成器。

    示例::

        checker = HealthChecker(graph_store)
        result = checker.run_all_checks()

        generator = HealthReportGenerator()
        report = generator.generate(result)
        # report 为 Markdown 格式字符串

        # 或写入文件
        with open("health-report.md", "w", encoding="utf-8") as f:
            generator.write(result, f)
    """

    def generate(self, result: HealthCheckResult) -> str:
        """生成 Markdown 格式的健康检查报告。

        Args:
            result: 健康检查结果。

        Returns:
            Markdown 字符串。
        """
        lines: list[str] = []

        # 标题
        lines.append("# 知识图谱健康检查报告")
        lines.append("")

        # 检测概览
        lines.extend(self._generate_overview(result))
        lines.append("")

        # 孤立节点详情
        lines.extend(self._generate_isolated_nodes_section(result.isolated_nodes))
        lines.append("")

        # 矛盾关系详情
        lines.extend(self._generate_contradictory_edges_section(result.contradictory_edges))
        lines.append("")

        # 老旧数据详情
        lines.extend(self._generate_stale_nodes_section(result.stale_nodes))
        lines.append("")

        # 检测元数据
        lines.extend(self._generate_metadata(result))
        lines.append("")

        return "\n".join(lines)

    def write(self, result: HealthCheckResult, file: TextIO) -> None:
        """将报告写入文件。

        Args:
            result: 健康检查结果。
            file: 可写入的文件对象。
        """
        report = self.generate(result)
        file.write(report)

    # ============================================================== 各部分生成

    def _generate_overview(self, result: HealthCheckResult) -> list[str]:
        """生成检测概览部分。"""
        lines: list[str] = []

        lines.append("## 检测概览")
        lines.append("")
        lines.append(f"本次健康检查于 **{self._format_timestamp(result.check_timestamp)}** 完成。")
        lines.append("")

        # 统计表格
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 节点总数 | {result.total_nodes:,} |")
        lines.append(f"| 关系总数 | {result.total_edges:,} |")
        lines.append(f"| 发现问题总数 | {result.issue_count:,} |")
        lines.append(f"| 孤立节点数 | {len(result.isolated_nodes):,} |")
        lines.append(f"| 矛盾关系对数 | {len(result.contradictory_edges):,} |")
        lines.append(f"| 老旧节点数 | {len(result.stale_nodes):,} |")
        lines.append(f"| 检查耗时 | {result.check_duration_seconds:.2f} 秒 |")
        lines.append("")

        # 健康状态评估
        if result.issue_count == 0:
            lines.append("**健康状态**: 良好 - 未发现明显问题")
        elif result.issue_count <= 10:
            lines.append("**健康状态**: 一般 - 发现少量问题，建议关注")
        elif result.issue_count <= 100:
            lines.append("**健康状态**: 需关注 - 发现较多问题，建议及时处理")
        else:
            lines.append("**健康状态**: 警告 - 发现大量问题，建议立即处理")

        return lines

    def _generate_isolated_nodes_section(self, isolated_nodes: list[IsolatedNodeResult]) -> list[str]:
        """生成孤立节点详情部分。"""
        lines: list[str] = []

        lines.append("## 孤立节点检测")
        lines.append("")

        if not isolated_nodes:
            lines.append("未发现孤立节点。")
            return lines

        lines.append(f"共发现 **{len(isolated_nodes)}** 个孤立节点。")
        lines.append("")

        # 按类型分组统计
        type_counts: dict[str, int] = {}
        for node in isolated_nodes:
            type_counts[node.node_type] = type_counts.get(node.node_type, 0) + 1

        if type_counts:
            lines.append("### 按类型统计")
            lines.append("")
            lines.append("| 节点类型 | 数量 |")
            lines.append("|----------|------|")
            for node_type, count in sorted(type_counts.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"| {node_type} | {count} |")
            lines.append("")

        # 详情列表（限制显示数量，避免报告过长）
        max_display = 50
        display_nodes = isolated_nodes[:max_display]

        lines.append("### 详情列表")
        lines.append("")
        lines.append("| 节点 ID | 节点类型 | 孤立原因 |")
        lines.append("|---------|----------|----------|")
        for node in display_nodes:
            reason_text = self._format_isolated_reason(node.reason)
            lines.append(f"| `{node.node_id}` | {node.node_type} | {reason_text} |")

        if len(isolated_nodes) > max_display:
            lines.append("")
            lines.append(f"*注：仅显示前 {max_display} 个，共 {len(isolated_nodes)} 个孤立节点。*")

        return lines

    def _generate_contradictory_edges_section(self, contradictory_edges: list[ContradictoryEdgeResult]) -> list[str]:
        """生成矛盾关系详情部分。"""
        lines: list[str] = []

        lines.append("## 矛盾关系检测")
        lines.append("")

        if not contradictory_edges:
            lines.append("未发现矛盾关系（互逆关系对）。")
            return lines

        lines.append(f"共发现 **{len(contradictory_edges)}** 对互逆关系（A→B 且 B→A）。")
        lines.append("")
        lines.append("互逆关系可能表示数据录入错误或业务逻辑冲突，建议人工审核。")
        lines.append("")

        # 详情列表
        max_display = 50
        display_edges = contradictory_edges[:max_display]

        lines.append("### 详情列表")
        lines.append("")
        lines.append("| 节点 A | 节点 B | A→B 关系类型 | B→A 关系类型 | A→B 创建时间 | B→A 创建时间 |")
        lines.append("|--------|--------|--------------|--------------|--------------|--------------|")
        for edge in display_edges:
            forward_time = self._format_timestamp(edge.forward_created_at) or "-"
            reverse_time = self._format_timestamp(edge.reverse_created_at) or "-"
            lines.append(
                f"| `{edge.source_id}` | `{edge.target_id}` | "
                f"{edge.edge_type_forward} | {edge.edge_type_reverse} | "
                f"{forward_time} | {reverse_time} |"
            )

        if len(contradictory_edges) > max_display:
            lines.append("")
            lines.append(f"*注：仅显示前 {max_display} 对，共 {len(contradictory_edges)} 对互逆关系。*")

        return lines

    def _generate_stale_nodes_section(self, stale_nodes: list[StaleNodeResult]) -> list[str]:
        """生成老旧数据详情部分。"""
        lines: list[str] = []

        lines.append("## 老旧数据检测")
        lines.append("")

        if not stale_nodes:
            lines.append("未发现超过 5 年未更新的节点。")
            return lines

        lines.append(f"共发现 **{len(stale_nodes)}** 个节点超过 5 年未更新。")
        lines.append("")
        lines.append("老旧数据可能已过时，建议审核并更新或归档。")
        lines.append("")

        # 按类型分组统计
        type_counts: dict[str, int] = {}
        for node in stale_nodes:
            type_counts[node.node_type] = type_counts.get(node.node_type, 0) + 1

        if type_counts:
            lines.append("### 按类型统计")
            lines.append("")
            lines.append("| 节点类型 | 数量 |")
            lines.append("|----------|------|")
            for node_type, count in sorted(type_counts.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"| {node_type} | {count} |")
            lines.append("")

        # 详情列表（按 age_days 降序）
        max_display = 50
        display_nodes = stale_nodes[:max_display]

        lines.append("### 详情列表（按数据存活时长降序）")
        lines.append("")
        lines.append("| 节点 ID | 节点类型 | 最后更新时间 | 存活时长（天） | 存活时长（年） |")
        lines.append("|---------|----------|--------------|----------------|----------------|")
        for node in display_nodes:
            last_updated = self._format_timestamp(node.last_updated) or "未知"
            age_years = node.age_days / 365.0
            lines.append(
                f"| `{node.node_id}` | {node.node_type} | {last_updated} | {node.age_days:,} | {age_years:.1f} |"
            )

        if len(stale_nodes) > max_display:
            lines.append("")
            lines.append(f"*注：仅显示前 {max_display} 个，共 {len(stale_nodes)} 个老旧节点。*")

        return lines

    def _generate_metadata(self, result: HealthCheckResult) -> list[str]:
        """生成检测元数据部分。"""
        lines: list[str] = []

        lines.append("---")
        lines.append("")
        lines.append("## 检测元数据")
        lines.append("")
        lines.append(f"- **检测时间**: {self._format_timestamp(result.check_timestamp)}")
        lines.append(f"- **检测耗时**: {result.check_duration_seconds:.2f} 秒")
        lines.append(f"- **节点总数**: {result.total_nodes:,}")
        lines.append(f"- **关系总数**: {result.total_edges:,}")
        lines.append(f"- **问题总数**: {result.issue_count:,}")
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("*本报告由知识图谱健康检查系统自动生成*")

        return lines

    # ============================================================== 辅助方法

    @staticmethod
    def _format_timestamp(ts_str: str | None) -> str:
        """格式化时间戳字符串为易读格式。"""
        if not ts_str:
            return ""
        try:
            # 尝试解析 ISO8601
            if ts_str.endswith("Z"):
                ts_str = ts_str[:-1] + "+00:00"
            dt = datetime.fromisoformat(ts_str)
            # 格式化为 YYYY-MM-DD HH:MM:SS
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return ts_str

    @staticmethod
    def _format_isolated_reason(reason: str) -> str:
        """格式化孤立原因。"""
        reason_map = {
            "no_edges": "无任何关系",
            "no_in_edges": "无入边（仅有出边）",
            "no_out_edges": "无出边（仅有入边）",
        }
        return reason_map.get(reason, reason)


__all__ = ["HealthReportGenerator"]
