"""InternalOnly 后端：跳过 CAM 软件二次校验（P1-3 拆分自原 cam_adapter.py）。

返回 status="skipped"，告知文本必须明确标注「未二次校验」。
本后端不调用任何外部软件，秒级返回。

工程语义：
    - 用户选择 internal_only 表示「快速预筛，不上机」
    - cam_report.json 中 cam_software_report.status="skipped"
    - 前端需显示警告「未执行 CAM 软件二次校验，G 代码不可上机」
"""

from __future__ import annotations

from ._common import CamSoftwareReport, _BaseBackend, logger


class _InternalOnlyBackend(_BaseBackend):
    """仅内部预校验，跳过 CAM 软件二次校验。"""

    backend_name = "internal_only"

    def validate(
        self,
        gcode_file_path: str,
        controller_type: str,
    ) -> CamSoftwareReport:
        logger.info("CamAdapter(internal_only): 跳过 CAM 软件二次校验，仅使用 InternalValidator 内部预校验结果。")
        return CamSoftwareReport(
            status="skipped",
            backend_used="internal_only",
            messages=[
                "未执行 CAM 软件二次校验（internal_only 模式）。",
                "G 代码仅经过 InternalValidator AABB 包围盒级快速预筛，不可直接上机加工。",
                "上机前必须由工程师手动加载到 NX / PowerMill / PyCAM 完成完整刀轨仿真。",
            ],
            collisions=[],
            degraded=False,
            degradation_reason="",
            gcode_file_path=gcode_file_path,
            controller_type=controller_type,
            validation_timestamp=self._now_iso(),
            subprocess_returncode=None,
            manual_checklist_path="",
        )
