"""告警与建议模块。

根据异常严重程度生成对应建议措施。
实现分级告警机制和可操作建议。

告警分级：
- 轻微：记录日志，持续监控
- 中等：发出预警，建议降速或调整参数
- 严重：立即告警，建议停机或换刀
- 危险：紧急停机

建议措施矩阵：
| 异常类型 | 轻微          | 中等                | 严重                  | 危险                  |
|----------|--------------|---------------------|----------------------|---------------------|
| 断刀     | 检查刀具      | 降速+安排换刀        | 紧急停机+换刀         | 紧急停机+全面检查    |
| 振动异常  | 监控振动      | 降进给+检查平衡      | 降速30%+调整参数     | 停机+检查地基主轴    |
| 过切     | 检查补偿值    | 降切削深度+重算刀路  | 暂停+重置坐标系       | 停机+检查装夹程序    |
| 撞刀     | 检查安全高度  | 暂停+验证碰撞检测    | 降速10%+回退刀具     | 紧急停机+全面检查    |
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime
import json
import os

logger = logging.getLogger(__name__)


class AlertModule:
    """加工异常告警与建议模块。

    处理检测结果，生成分级告警和可操作建议。

    Attributes:
        alert_history: 告警历史记录
        alert_cooldown: 告警冷却时间（秒）
        escalation_threshold: 升级阈值（连续异常次数）
    """

    RECOMMENDATIONS = {
        ("断刀", "轻微"): "建议检查刀具状态，准备备刀。密切监控切削力信号变化趋势。",
        ("断刀", "中等"): "立即将主轴转速降低50%，安排刀具更换计划。检查切削液供应。",
        ("断刀", "严重"): "立即紧急停机！更换刀具后方可恢复加工。检查已加工表面质量。",
        ("断刀", "危险"): "立即紧急停机！全面检查刀具系统、主轴和刀库状态。",

        ("振动异常", "轻微"): "监控振动趋势，检查工件夹具紧固状态。记录振动频谱数据。",
        ("振动异常", "中等"): "降低进给速率20-30%，检查主轴动平衡状态。评估刀具磨损程度。",
        ("振动异常", "严重"): "立即降速至30%，重新调整切削参数。检查刀柄夹紧力。",
        ("振动异常", "危险"): "立即紧急停机！检查机床地基、主轴轴承状态和丝杠间隙。",

        ("过切", "轻微"): "检查刀具补偿值，微调切削深度参数。验证G代码刀路是否正确。",
        ("过切", "中等"): "降低切削深度50%，重新计算刀具路径。检查刀具半径补偿。",
        ("过切", "严重"): "立即暂停加工！重置工件坐标系，验证加工程序正确性。",
        ("过切", "危险"): "立即紧急停机！检查工件装夹稳定性，全面验证NC程序。",

        ("撞刀", "轻微"): "检查刀路安全高度设置，降低G00快进速度。验证刀具长度补偿。",
        ("撞刀", "中等"): "立即暂停加工！运行刀路碰撞检测，确认工件坐标系。",
        ("撞刀", "严重"): "立即降速至10%！将刀具回退至安全位置。检查刀具和工件状态。",
        ("撞刀", "危险"): "立即紧急停机！全面检查机床、刀具、工件和夹具状态。",
    }

    ACTION_LEVELS = {
        "正常": {"level": 0, "action": "continue", "color": "green"},
        "轻微": {"level": 1, "action": "log_and_monitor", "color": "yellow"},
        "中等": {"level": 2, "action": "warn_and_adjust", "color": "orange"},
        "严重": {"level": 3, "action": "alert_and_stop", "color": "red"},
        "危险": {"level": 4, "action": "emergency_stop", "color": "dark_red"},
    }

    def __init__(
        self,
        alert_cooldown_seconds: float = 3.0,
        escalation_threshold: int = 3,
        log_dir: str = "./logs/alerts/",
    ):
        self.alert_cooldown = alert_cooldown_seconds
        self.escalation_threshold = escalation_threshold
        self.log_dir = log_dir

        self.alert_history: List[Dict] = []
        self.last_alert_time: Optional[datetime] = None
        self.consecutive_anomalies = 0
        self.last_severity_level = 0

        os.makedirs(log_dir, exist_ok=True)

    def process_result(self, detection_result: Dict) -> Dict:
        """处理检测结果，生成告警和建议。

        Args:
            detection_result: 检测结果字典

        Returns:
            包含告警信息和建议的结果字典
        """
        anomaly_type = detection_result.get("异常类型", "正常")
        severity = detection_result.get("严重程度", "正常")
        anomaly_prob = detection_result.get("帧级异常概率", 0.0)
        cosine_similarity = detection_result.get("余弦相似度", 1.0)
        euclidean_distance = detection_result.get("欧氏距离", 0.0)

        action_info = self.ACTION_LEVELS.get(severity, self.ACTION_LEVELS["正常"])
        recommendation = self._get_recommendation(anomaly_type, severity)

        alert = {
            "timestamp": datetime.now().isoformat(),
            "anomaly_type": anomaly_type,
            "severity": severity,
            "anomaly_probability": anomaly_prob,
            "cosine_similarity": cosine_similarity,
            "euclidean_distance": euclidean_distance,
            "action_level": action_info["level"],
            "recommended_action": action_info["action"],
            "recommendation": recommendation,
        }

        # 判断是否需要触发告警
        should_alert = action_info["level"] >= 1

        if should_alert:
            self.consecutive_anomalies += 1
            if self.consecutive_anomalies >= self.escalation_threshold:
                alert["escalated"] = True
        else:
            self.consecutive_anomalies = 0

        # 检查告警冷却
        now = datetime.now()
        if self.last_alert_time is not None:
            cooldown_elapsed = (now - self.last_alert_time).total_seconds()
            if cooldown_elapsed < self.alert_cooldown:
                alert["suppressed"] = True

        if should_alert and not alert.get("suppressed"):
            self.last_alert_time = now
            self._log_alert(alert)

        self.alert_history.append(alert)
        if len(self.alert_history) > 10000:
            self.alert_history = self.alert_history[-5000:]

        detection_result["alert"] = alert
        return detection_result

    def _get_recommendation(self, anomaly_type: str, severity: str) -> str:
        """获取建议措施。

        Args:
            anomaly_type: 异常类型
            severity: 严重程度

        Returns:
            建议措施字符串
        """
        key = (anomaly_type, severity)
        if key in self.RECOMMENDATIONS:
            return self.RECOMMENDATIONS[key]

        # 尝试部分匹配
        for (at, sev), rec in self.RECOMMENDATIONS.items():
            if at == anomaly_type:
                return rec

        return f"检测到{anomaly_type}({severity}程度)，建议人工确认后采取相应措施。"

    def _log_alert(self, alert: Dict):
        """记录告警到文件。"""
        log_file = os.path.join(
            self.log_dir,
            f"alerts_{datetime.now().strftime('%Y%m%d')}.jsonl",
        )
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(alert, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to log alert: {e}")

    def get_alert_summary(self, window_minutes: int = 60) -> Dict:
        """获取最近告警统计摘要。

        Args:
            window_minutes: 统计窗口（分钟）

        Returns:
            告警统计字典
        """
        now = datetime.now()
        recent = [
            a for a in self.alert_history
            if a.get("timestamp")
            and (now - datetime.fromisoformat(a["timestamp"])).total_seconds()
            <= window_minutes * 60
        ]

        summary = {
            "window_minutes": window_minutes,
            "total_alerts": len(recent),
            "by_severity": {},
            "by_type": {},
        }

        for a in recent:
            severity = a.get("severity", "正常")
            anomaly_type = a.get("anomaly_type", "正常")
            summary["by_severity"][severity] = summary["by_severity"].get(severity, 0) + 1
            summary["by_type"][anomaly_type] = summary["by_type"].get(anomaly_type, 0) + 1

        return summary

    def clear_history(self):
        """清空告警历史。"""
        self.alert_history.clear()
        self.consecutive_anomalies = 0
