"""Manual 后端：手动校验清单 + 工程师回填（P1-3 拆分自原 cam_adapter.py）。

兜底后端，永不失败。当 NX/PowerMill/PyCAM 均不可用时自动降级到此。

工程流程：
    1. 生成 markdown 校验清单到 output_dir/{task_id}.manual_checklist.md
    2. 返回 CamSoftwareReport(status="manual_pending")
    3. 前端展示清单 + 工程师按清单在 CAM 软件中手动校验
    4. 工程师回填结果（pass / fail + 备注）→ pipeline 转 REVIEWED
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ._common import CamSoftwareReport, _BaseBackend, logger


class _ManualBackend(_BaseBackend):
    """生成手动校验清单 markdown，等待工程师在前端回填结果。"""

    backend_name = "manual"

    def validate(
        self,
        gcode_file_path: str,
        controller_type: str,
    ) -> CamSoftwareReport:
        # 生成 markdown 校验清单
        # 注意：此处的 output_dir 由 CamAdapter 注入，_ManualBackend 内部
        # 使用系统临时目录兜底，避免依赖外部 config；最终路径通过
        # CamAdapter._manual_output_dir 覆盖。
        checklist_path = self._generate_checklist(
            gcode_file_path=gcode_file_path,
            controller_type=controller_type,
        )

        logger.info(
            "CamAdapter(manual): 已生成手动校验清单 %s，等待工程师回填。",
            checklist_path,
        )

        return CamSoftwareReport(
            status="manual_pending",
            backend_used="manual",
            messages=[
                "已生成手动校验清单 markdown，等待工程师在前端回填校验结果。",
                f"校验清单路径：{checklist_path}",
                "工程师操作流程：",
                "  1. 在 NX / PowerMill / PyCAM 中加载 G 代码文件",
                "  2. 按清单逐项执行刀轨仿真",
                "  3. 在前端回填每项校验结果（pass / fail + 备注）",
                "  4. 全部通过后确认 SUCCEEDED",
            ],
            collisions=[],
            degraded=False,
            degradation_reason="",
            gcode_file_path=gcode_file_path,
            controller_type=controller_type,
            validation_timestamp=self._now_iso(),
            subprocess_returncode=None,
            manual_checklist_path=checklist_path,
        )

    def _generate_checklist(
        self,
        gcode_file_path: str,
        controller_type: str,
    ) -> str:
        """生成手动校验清单 markdown 文件，返回绝对路径。

        Args:
            gcode_file_path: G 代码文件路径
            controller_type: 控制器类型

        Returns:
            校验清单 markdown 文件绝对路径
        """
        # 使用系统临时目录兜底（pipeline.py 会在 confirm 时覆盖路径）
        # 实际生产路径由 CamAdapter._resolve_manual_output_dir() 决定
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"manual_checklist_{timestamp}.md"

        # CamAdapter 在调用前会设置 _ManualBackend._output_dir
        output_dir = getattr(self, "_output_dir", None)
        if output_dir:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)
            checklist_path = output_path / filename
        else:
            # 兜底：系统临时目录
            checklist_path = Path(tempfile.gettempdir()) / filename

        # 提取 G 代码文件名
        gcode_filename = Path(gcode_file_path).name

        # 生成 markdown 内容
        content = self._render_checklist_markdown(
            gcode_file_path=gcode_file_path,
            gcode_filename=gcode_filename,
            controller_type=controller_type,
            timestamp=timestamp,
        )

        try:
            checklist_path.write_text(content, encoding="utf-8")
        except OSError as e:
            # 写入失败时降级到临时目录
            logger.warning(
                "CamAdapter(manual): 写入校验清单到 %s 失败：%s，降级到系统临时目录。",
                checklist_path,
                e,
            )
            checklist_path = Path(tempfile.gettempdir()) / filename
            checklist_path.write_text(content, encoding="utf-8")

        return str(checklist_path)

    @staticmethod
    def _render_checklist_markdown(
        gcode_file_path: str,
        gcode_filename: str,
        controller_type: str,
        timestamp: str,
    ) -> str:
        """渲染手动校验清单 markdown 内容。

        Args:
            gcode_file_path: G 代码文件绝对路径
            gcode_filename: G 代码文件名（用于标题）
            controller_type: 控制器类型
            timestamp: 生成时间戳（UTC，YYYYMMDD_HHMMSS）

        Returns:
            markdown 字符串
        """
        return f"""# CAM 软件手动校验清单

## 基本信息

- **G 代码文件**：`{gcode_file_path}`
- **控制器类型**：`{controller_type}`
- **生成时间（UTC）**：{timestamp}
- **校验类型**：CAM 软件二次校验（手动模式）

## 工程师操作流程

1. 在 NX / PowerMill / PyCAM 中加载上述 G 代码文件
2. 按以下清单逐项执行刀轨仿真
3. 在前端「审核」页面回填每项校验结果（pass / fail + 备注）
4. 全部通过后点击「确认 SUCCEEDED」

## 必查项（必填）

- [ ] **刀柄-工件碰撞**：刀柄在所有快速移动（G00）和切削移动（G01/G02/G03）过程中未与工件或夹具碰撞
- [ ] **刀具-夹具碰撞**：刀具在换刀、定位过程中未与压板、虎钳等夹具碰撞
- [ ] **工作空间超限**：所有刀轨点在机床工作空间内（X/Y/Z 行程未超限）
- [ ] **安全 Z 高度**：所有 G00 快速移动在安全 Z 平面以上执行
- [ ] **过切检查**：切削深度未超过毛坯底面，无残留过切

## 推荐查项（可选）

- [ ] **切削力仿真**：切削力在主轴扭矩限制内
- [ ] **机床运动学**：5-axis RTCP/TWP 旋转轴无奇异点
- [ ] **后处理器语法**：G 代码语法与目标控制器（{controller_type}）兼容
- [ ] **刀轨光滑性**：刀轨无明显尖角、急停、回环

## 回填说明

- 若任一必查项失败，前端「审核」时选择 `rejected` 或 `edited`（修正后重审）
- 若全部必查项通过，前端「审核」时选择 `confirmed`，pipeline 自动转 SUCCEEDED
- 备注字段可记录发现的碰撞 block_number / 坐标 / 严重程度

## 工业硬门槛告知

- **G 代码必须经 CAM 软件二次校验后方可上机**
- **物理机床执行需持证操作员 + 导师签字 + 保险**
- **本系统绝不直接接口 CNC 控制器**，仅生成校验报告 JSON

---

*本清单由 阶段 7 CamAdapter(_ManualBackend) 自动生成*
"""
