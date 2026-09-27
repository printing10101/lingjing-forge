"""HTML 性能报告生成器。

生成交互式 HTML 报告，包含性能概览、阈值状态、历史趋势图表。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>性能基准测试报告 - {timestamp}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #f5f7fa; color: #333; padding: 20px;
}}
.container {{ max-width: 1200px; margin: 0 auto; }}
.header {{
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white; padding: 30px; border-radius: 12px; margin-bottom: 24px;
}}
.header h1 {{ font-size: 24px; margin-bottom: 8px; }}
.header .meta {{ opacity: 0.9; font-size: 14px; }}
.summary {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px; margin-bottom: 24px;
}}
.summary-card {{ background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
.summary-card .value {{ font-size: 28px; font-weight: bold; }}
.summary-card .label {{ font-size: 13px; color: #888; margin-top: 4px; }}
.summary-card .value.pass {{ color: #22c55e; }}
.summary-card .value.warning {{ color: #f59e0b; }}
.summary-card .value.critical {{ color: #ef4444; }}
.section {{
    background: white; border-radius: 10px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    padding: 24px; margin-bottom: 24px;
}}
.section h2 {{ font-size: 18px; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 2px solid #667eea; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #eee; font-size: 14px; }}
th {{ background: #f8f9fa; font-weight: 600; color: #555; }}
tr:hover {{ background: #f8f9fa; }}
.status-pass {{ color: #22c55e; font-weight: 600; }}
.status-warning {{ color: #f59e0b; font-weight: 600; }}
.status-critical {{ color: #ef4444; font-weight: 600; }}
.status-improved {{ color: #3b82f6; font-weight: 600; }}
.status-new {{ color: #888; font-weight: 600; }}
.status-skip {{ color: #888; font-style: italic; }}
.chart-container {{ height: 300px; margin-top: 16px; }}
.benchmark-type {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; }}
.type-lnn {{ background: #dbeafe; color: #1d4ed8; }}
.type-gcode {{ background: #dcfce7; color: #16a34a; }}
.type-render {{ background: #fef3c7; color: #d97706; }}
.footer {{ text-align: center; color: #888; font-size: 13px; padding: 20px; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>性能基准测试报告</h1>
    <div class="meta">
      生成时间: {timestamp} | Git: {git_info} | 运行环境: {env_info}
    </div>
  </div>

  <div class="summary">
    <div class="summary-card">
      <div class="value {overall_status_class}">{overall_status}</div>
      <div class="label">总体状态</div>
    </div>
    <div class="summary-card">
      <div class="value">{total_metrics}</div>
      <div class="label">指标总数</div>
    </div>
    <div class="summary-card">
      <div class="value">{passed_metrics}</div>
      <div class="label">通过</div>
    </div>
    <div class="summary-card">
      <div class="value">{failed_metrics}</div>
      <div class="label">失败/警告</div>
    </div>
    <div class="summary-card">
      <div class="value">{regression_count}</div>
      <div class="label">回归检测</div>
    </div>
  </div>

  <div class="section">
    <h2>基准测试结果</h2>
    <table>
      <thead>
        <tr><th>类型</th><th>指标</th><th>当前值</th><th>阈值</th><th>状态</th></tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
  </div>

  <div class="section">
    <h2>性能趋势</h2>
    <div class="chart-container">
      <canvas id="trendChart"></canvas>
    </div>
  </div>

  <div class="section">
    <h2>回归检测</h2>
    <table>
      <thead>
        <tr><th>指标</th><th>当前值</th><th>上次值</th><th>变化率</th><th>状态</th></tr>
      </thead>
      <tbody>
        {regression_rows}
      </tbody>
    </table>
  </div>
</div>

<script>
const trendCtx = document.getElementById('trendChart').getContext('2d');
new Chart(trendCtx, {{
  type: 'line',
  data: {chart_data},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    interaction: {{ intersect: false, mode: 'index' }},
    scales: {{
      x: {{ title: {{ display: true, text: '运行次数' }} }},
      y: {{ title: {{ display: true, text: '值' }}, beginAtZero: false }}
    }}
  }}
}});
</script>

<div class="footer">
  灵境制造 V4 性能基准测试系统 | {timestamp}
</div>
</body>
</html>"""


def generate_html_report(
    results: dict[str, dict[str, Any]],
    regression_results: list[dict[str, Any]] | None = None,
    output_path: str | None = None,
    git_info: str = "",
    env_info: str = "",
) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    all_metrics: list[dict[str, Any]] = []
    total = 0
    passed = 0
    failed = 0
    regression_count = 0

    for bench_type, metrics in results.items():
        for metric, data in metrics.items():
            if isinstance(data, dict) and "value" in data:
                total += 1
                status = data.get("status", "PASS")
                if status in ("PASS", "IMPROVED"):
                    passed += 1
                else:
                    failed += 1
                all_metrics.append({
                    "type": bench_type,
                    "metric": metric,
                    "value": data["value"],
                    "unit": data.get("unit", ""),
                    "status": status,
                })

    has_critical = any(m.get("status") == "CRITICAL" for m in all_metrics)
    has_warning = any(m.get("status") in ("WARNING", "VIOLATED") for m in all_metrics)

    if has_critical:
        overall_status = "CRITICAL"
        overall_status_class = "critical"
    elif has_warning:
        overall_status = "WARNING"
        overall_status_class = "warning"
    else:
        overall_status = "PASS"
        overall_status_class = "pass"

    if regression_results:
        regression_count = sum(1 for r in regression_results if r.get("status") in ("WARNING", "CRITICAL"))

    def _type_class(bt: str) -> str:
        if "lnn" in bt.lower():
            return "type-lnn"
        if "gcode" in bt.lower() or "nc" in bt.lower():
            return "type-gcode"
        if "render" in bt.lower() or "fps" in bt.lower():
            return "type-render"
        return ""

    def _status_html(status: str) -> str:
        cls = status.lower()
        return f'<span class="status-{cls}">{status}</span>'

    rows = ""
    for m in all_metrics:
        type_tag = f'<span class="benchmark-type {_type_class(m["type"])}">{m["type"]}</span>'
        rows += (
            f"<tr>"
            f"<td>{type_tag}</td>"
            f"<td>{m['metric']}</td>"
            f"<td>{m['value']} {m['unit']}</td>"
            f"<td>-</td>"
            f"<td>{_status_html(m['status'])}</td>"
            f"</tr>"
        )

    regression_rows = ""
    if regression_results:
        for r in regression_results:
            change_pct = r.get("change_pct", 0)
            change_str = f"{change_pct:+.1f}%" if isinstance(change_pct, (int, float)) else "-"
            regression_rows += (
                f"<tr>"
                f"<td>{r.get('metric', '')}</td>"
                f"<td>{r.get('current', '')}</td>"
                f"<td>{r.get('previous', '')}</td>"
                f"<td>{change_str}</td>"
                f"<td>{_status_html(r.get('status', ''))}</td>"
                f"</tr>"
            )

    chart_data = json.dumps({
        "datasets": [
            {
                "label": m["metric"],
                "data": [m["value"]],
                "borderColor": ["#667eea", "#22c55e", "#f59e0b", "#ef4444", "#3b82f6"][i % 5],
                "tension": 0.3,
            }
            for i, m in enumerate(all_metrics[:10])
        ],
        "labels": [timestamp],
    })

    html = REPORT_TEMPLATE.format(
        timestamp=timestamp,
        git_info=git_info or "N/A",
        env_info=env_info or "N/A",
        overall_status=overall_status,
        overall_status_class=overall_status_class,
        total_metrics=str(total),
        passed_metrics=str(passed),
        failed_metrics=str(failed),
        regression_count=str(regression_count),
        rows=rows,
        regression_rows=regression_rows,
        chart_data=chart_data,
    )

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  HTML 报告已保存: {output_path}")

    return html
